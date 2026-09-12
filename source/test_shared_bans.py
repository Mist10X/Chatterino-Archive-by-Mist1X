import copy,json,sqlite3,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from shared_bans import API,Store,SharedService,SharedError,AuthRequired,parse_card,auth_record

RAW={'op':'sbdoc','u':'123','items':[{'room':'456','login':'one','since':1788962832,
    'msgs':[{'ts':1788962829,'am':1,'t':'Привет <b>текст</b>'}]}],'note':''}
ACCOUNT={'ticket':'TEST_ONLY','uid':'42','exp':4102444800,'login':'tester'}
class Credentials:
    def __init__(self):self.value=None
    def load(self):return self.value
    def save(self,v):self.value=v
    def clear(self):self.value=None
class Socket:
    def __init__(self,verified=True,raw=None):self.input=[{'op':'hello','verified':verified},raw or RAW];self.sent=[];self.closed=False
    def send(self,value):self.sent.append(json.loads(value))
    def recv(self):return json.dumps(self.input.pop(0))
    def close(self,**kw):self.closed=True

class SharedTests(unittest.TestCase):
    def test_only_authenticated_read_commands(self):
        sock=Socket()
        with patch('shared_bans.websocket.create_connection',return_value=sock):value=API().card(ACCOUNT,'123','someone')
        self.assertEqual(sock.sent,[{'op':'hello','v':2,'ticket':'TEST_ONLY'},{'op':'sbget','u':'123'}])
        self.assertTrue(sock.closed);self.assertTrue(value['items'][0]['messages'][0]['automod'])
        sock=Socket(False)
        with patch('shared_bans.websocket.create_connection',return_value=sock),self.assertRaises(AuthRequired):API().card(ACCOUNT,'123','someone')
        self.assertEqual(len(sock.sent),1);self.assertTrue(sock.closed)
    def test_wrong_user_malformed_and_duplicate_events(self):
        with self.assertRaises(SharedError):parse_card(RAW,'999','someone')
        raw=copy.deepcopy(RAW);raw['items']*=2
        self.assertEqual(len(parse_card(raw,'123','someone')['items']),1)
        for value in (None,[],{},dict(RAW,items=[{}])):
            with self.assertRaises(SharedError):parse_card(value,'123','someone')
        raw=copy.deepcopy(RAW);raw['items'][0]['msgs'][0]['ts']=10**40
        with self.assertRaises(SharedError):parse_card(raw,'123','someone')
    def test_cache_persists_offline_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            card=parse_card(RAW,'123','someone');Store(folder).save(card)
            service=SharedService(folder,network=False,credentials=Credentials());service.fetch('someone')
            self.assertEqual(service.results.get()[1],card)
            self.assertEqual(Store(folder).load('someone'),card)
            path=Store(folder).path('someone');bad=copy.deepcopy(card);bad['items'][0]['messages']=None
            path.write_text(json.dumps(bad));self.assertIsNone(Store(folder).load('someone'))
            with self.assertRaises(ValueError):Store(folder).path('../../escape')
    def test_cache_throttle_and_error_keep_saved_data(self):
        with tempfile.TemporaryDirectory() as folder:
            service=SharedService(folder,credentials=Credentials());service.account=ACCOUNT
            card=parse_card(RAW,'123','someone');service.store.save(card)
            with patch.object(service.api,'card',side_effect=SharedError('offline')) as api:
                service.fetch('someone');api.assert_not_called()
                with self.assertRaises(SharedError):service.fetch('someone',True)
                api.assert_called_once();self.assertEqual(service.store.load('someone'),card)
                with self.assertRaises(SharedError):service.fetch('someone',True)
                api.assert_called_once()
    def test_resolve_existing_identity_without_twitch_call(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'panel-cache';p.mkdir()
            db=sqlite3.connect(p/'moderation.sqlite3');db.execute('CREATE TABLE identities(user TEXT,user_id TEXT)')
            db.execute("INSERT INTO identities VALUES('someone','123')");db.commit();db.close()
            service=SharedService(folder,credentials=Credentials())
            with patch.object(service.api,'user_id',side_effect=AssertionError('Unexpected network')):
                self.assertEqual(service.resolve('someone'),'123')
    def test_auth_renew_uses_separate_credential_and_validates_origin(self):
        with patch('shared_bans.request_json',return_value=ACCOUNT) as call:
            self.assertEqual(API().renew(dict(ACCOUNT,renew='RENEW_TEST',renew_exp=4102444800)),ACCOUNT)
            self.assertEqual(call.call_args.args,('https://ws.chat.eblo.id/auth/renew',{'renew':'RENEW_TEST'}))
        with patch('shared_bans.request_json',return_value={'state':'test','url':'https://example.invalid/oauth2/authorize'}),self.assertRaises(SharedError):API().begin()
        with self.assertRaises(SharedError):auth_record({'ticket':'x','uid':'42'})
    def test_auth_worker_saves_then_disconnect_preserves_cards(self):
        with tempfile.TemporaryDirectory() as folder:
            credentials=Credentials();service=SharedService(folder,credentials=credentials)
            service.store.save(parse_card(RAW,'123','someone'))
            service.auth={'state':'TEST','deadline':time.monotonic()+60};service.next_poll=0
            with patch.object(service.api,'poll',return_value=ACCOUNT):
                service.start()
                until=time.monotonic()+3
                while credentials.value is None and time.monotonic()<until:time.sleep(.01)
                self.assertEqual(credentials.value,ACCOUNT)
                service.command('disconnect')
                while credentials.value is not None and time.monotonic()<until:time.sleep(.01)
                self.assertIsNone(credentials.value);self.assertIsNotNone(service.store.load('someone'))
                service.stopped.set();service.join(2);self.assertFalse(service.is_alive())

if __name__=='__main__':unittest.main()
