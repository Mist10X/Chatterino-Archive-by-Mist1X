"""Offline OAuth, encrypted credentials, IRC and cross-source archive tests."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from twitch_core import Credentials, MessageWriter, parse_irc, chat_record, settings_save, settings_load, TwitchError, CLIENT_ID
from twitch_worker import TwitchWorker
from storage import ArchiveIndex


LINE = '@id=abc;user-id=42;room-id=77;tmi-sent-ts=1788950000000;display-name=Mist1X_X;reply-parent-msg-id=parent;reply-parent-user-id=77;reply-parent-user-login=someone;reply-parent-display-name=Someone;reply-parent-msg-body=hello\\sworld\\:ok :mist1x_x!mist1x_x@mist1x_x.tmi.twitch.tv PRIVMSG #morphe_ya :привет <b>мир</b>'


class FakeCredentials:
    def __init__(self):self.value=None;self.saved=[]
    def save(self,value):self.value=dict(value);self.saved.append(dict(value))
    def load(self):return self.value
    def clear(self):self.value=None


class FakeAPI:
    def __init__(self,responses):self.responses=list(responses);self.calls=[]
    def request(self,path,fields=None,token=None):
        self.calls.append((path,fields,token));result=self.responses.pop(0)
        if isinstance(result,Exception):raise result
        return result


class Socket:
    def __init__(self):self.sent=[];self.closed=False
    def sendall(self,b):self.sent.append(b)
    def close(self):self.closed=True


class TwitchTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def worker(self,api=None,clock=lambda:100):
        w=TwitchWorker(self.data,{'enabled':True,'channels':['morphe_ya'],'tray':True},api=api,credentials=FakeCredentials(),clock=clock)
        w.writer=MessageWriter(self.data/'twitch');return w

    def test_irc_unicode_reply_escaping_and_action(self):
        cmd,params,tags,prefix=parse_irc(LINE);self.assertEqual(cmd,'PRIVMSG')
        r=chat_record(params,tags,prefix)
        self.assertEqual(r['reply']['text'],'hello world;ok');self.assertEqual(r['reply']['user_id'],'77');self.assertEqual(r['user_id'],'42')
        self.assertEqual(r['text'],'привет <b>мир</b>');self.assertTrue(r['time_utc'].endswith('Z'))
        self.assertEqual(chat_record(['#morphe_ya','\x01ACTION hi\x01'],tags,prefix)['text'],'/me hi')
        self.assertIsNone(chat_record(params,{},prefix))

    def test_only_selected_users_channels_enabled_no_send_chat(self):
        w=self.worker();w.users={'mist1x_x':True};w.connection=Socket()
        w.handle_line('PING :tmi.twitch.tv');self.assertEqual(w.connection.sent,[b'PONG :tmi.twitch.tv\r\n'])
        w.handle_line(LINE);w.handle_line(LINE)
        self.assertEqual(w.snapshot()['saved'],1)
        w.users['mist1x_x']=False;w.handle_line(LINE.replace('id=abc;','id=def;'))
        w.users.clear();w.handle_line(LINE.replace('id=abc;','id=ghi;'))
        w.users['mist1x_x']=True;w.config['enabled']=False;w.handle_line(LINE.replace('id=abc;','id=jkl;'))
        w.config['enabled']=True;w.config['channels']=[];w.handle_line(LINE.replace('id=abc;','id=mno;'))
        self.assertEqual(w.snapshot()['saved'],1)
        rows=(self.data/'twitch/messages/mist1x_x--morphe_ya.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(rows),1)

    def test_plugin_and_direct_sources_merge_and_enrich_reply(self):
        _,params,tags,prefix=parse_irc(LINE);r=chat_record(params,tags,prefix)
        legacy=dict(r);legacy.pop('reply');legacy['source']='plugin'
        path=self.data/'mist1x_x--morphe_ya.jsonl';original=(json.dumps(legacy)+'\n').encode();path.write_bytes(original)
        index=ArchiveIndex(self.data)
        try:
            index.sync();MessageWriter(self.data/'twitch').append(r);index.sync()
            count,rows=index.query('mist1x_x');self.assertEqual(count,1);self.assertEqual(rows[0]['user_id'],'42');self.assertEqual(rows[0]['reply']['text'],'hello world;ok')
            self.assertEqual(index.sync(),0);self.assertEqual(path.read_bytes(),original)
            self.assertEqual(index.db.execute('SELECT count(*) FROM files').fetchone()[0],2)
        finally:index.close()

    @unittest.skipUnless(os.name=='nt','DPAPI requires Windows')
    def test_dpapi_roundtrip_no_plaintext_and_corruption(self):
        vault=Credentials(self.data);tokens={'access_token':'sensitive-access','refresh_token':'sensitive-refresh'}
        vault.save(tokens);self.assertNotIn(b'sensitive',vault.path.read_bytes());self.assertEqual(vault.load(),tokens)
        vault.path.write_text('broken')
        with self.assertRaises(OSError):vault.load()
        vault.clear();self.assertIsNone(vault.load())

    def test_channel_settings_normalization_limit_and_preserve_on_invalid(self):
        saved=settings_save(self.data,{'enabled':True,'channels':['#Morphe_Ya','morphe_ya','DANGERLYOHA'],'tray':True})
        self.assertEqual(saved['channels'],['dangerlyoha','morphe_ya']);self.assertEqual(settings_load(self.data),saved)
        for channels in [['a\r\nJOIN #bad'],[str(i) for i in range(101)]]:
            with self.assertRaises(ValueError):settings_save(self.data,{'channels':channels})
        self.assertEqual(settings_load(self.data),saved)

    def test_device_pending_slowdown_success_validates_identity(self):
        now=[100.];api=FakeAPI([
            {'device_code':'private','user_code':'ABCD','verification_uri':'https://www.twitch.tv/activate?public=true','expires_in':120,'interval':5},
            TwitchError('authorization_pending'),TwitchError('slow_down'),
            {'access_token':'access','refresh_token':'refresh'},
            {'client_id':CLIENT_ID,'login':'mist1x_x','user_id':'42','scopes':['chat:read'],'expires_in':14400}])
        w=self.worker(api,lambda:now[0]);w.start_auth();w.poll_auth();self.assertEqual(len(api.calls),1)
        now[0]+=5;w.poll_auth();self.assertIsNotNone(w.auth)
        now[0]+=5;w.poll_auth();self.assertEqual(w.auth['interval'],10)
        now[0]+=10;w.poll_auth();self.assertEqual(w.snapshot()['login'],'mist1x_x');self.assertIsNone(w.auth)
        self.assertEqual(w.snapshot()['auth_code'],'');self.assertEqual(w.credentials.value['refresh_token'],'refresh')

    def test_auth_cancel_expiry_and_untrusted_url(self):
        w=self.worker();w.auth={'until':99,'next':101};w.poll_auth();self.assertIsNone(w.auth)
        w.auth={'until':999};w.command('cancel_auth');w.process_commands();self.assertIsNone(w.auth)
        w.api=FakeAPI([{'verification_uri':'https://evil.invalid/activate'}])
        with self.assertRaises(TwitchError):w.start_auth()
        self.assertEqual(w.snapshot()['auth_url'],'')

    def test_refresh_rotation_saved_before_validation(self):
        api=FakeAPI([{'access_token':'new-access','refresh_token':'new-refresh'},TwitchError('network')]);w=self.worker(api)
        w.account={'access_token':'old','refresh_token':'old-refresh'}
        with self.assertRaises(TwitchError):w.refresh()
        self.assertEqual(w.credentials.value['refresh_token'],'new-refresh')
        self.assertNotIn('client_secret',api.calls[0][1])

    def test_expired_token_at_restart_refreshes_without_new_login(self):
        api=FakeAPI([TwitchError(401),{'access_token':'new','refresh_token':'rotated'},
            {'client_id':CLIENT_ID,'login':'mist1x_x','user_id':'42','scopes':['chat:read'],'expires_in':14400}])
        w=self.worker(api);w.account={'access_token':'expired','refresh_token':'old-refresh'};w.ensure_token()
        self.assertEqual(w.account['access_token'],'new');self.assertEqual(w.snapshot()['login'],'mist1x_x')

    def test_invalid_client_and_scope_rejected(self):
        for client,scopes in [('wrong',['chat:read']),(CLIENT_ID,[])]:
            w=self.worker(FakeAPI([{'client_id':client,'scopes':scopes,'login':'user','user_id':'1'}]));w.account={'access_token':'x'}
            with self.assertRaises(TwitchError):w.validate()

    def test_disconnect_forgets_tokens_even_revoke_offline(self):
        w=self.worker(FakeAPI([TwitchError('network')]));w.account={'access_token':'x'};w.credentials.save(w.account)
        w.connection=Socket();sock=w.connection;w.disconnect()
        self.assertTrue(sock.closed);self.assertIsNone(w.credentials.value);self.assertIsNone(w.account)

    def test_partial_direct_tail_recovered_preserving_fragment(self):
        _,p,t,prefix=parse_irc(LINE);r=chat_record(p,t,prefix);writer=MessageWriter(self.data/'twitch')
        path=writer.folder/'mist1x_x--morphe_ya.jsonl';path.write_bytes(b'{"unfinished')
        writer.append(r);self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['id'],'abc')
        self.assertEqual(next(writer.folder.glob('*.partial-*')).read_bytes(),b'{"unfinished')

    def test_join_confirmation_and_unavailable_channel_status(self):
        w=self.worker();w.handle_line(':tmi.twitch.tv 001 nick :Welcome');self.assertTrue(w.ready)
        w.handle_line('@room-id=77 :tmi.twitch.tv ROOMSTATE #morphe_ya');self.assertEqual(w.snapshot()['joined'],['morphe_ya'])
        w.handle_line('@msg-id=msg_channel_suspended :tmi.twitch.tv NOTICE #morphe_ya :Channel unavailable')
        self.assertEqual(w.snapshot()['joined'],[]);self.assertIn('morphe_ya',w.failed)

    def test_status_file_excludes_tokens_and_device_codes(self):
        w=self.worker();w.account={'access_token':'secret-token','refresh_token':'secret-refresh'}
        w.report(auth_url='https://www.twitch.tv/activate?device-code=SECRET',auth_code='SECRET')
        w.persist_status(force=True);text=(self.data/'twitch/status.json').read_text(encoding='utf-8')
        self.assertNotIn('secret',text.lower());self.assertNotIn('auth_url',text);self.assertIn('updated_at',text)


if __name__=='__main__':unittest.main()
