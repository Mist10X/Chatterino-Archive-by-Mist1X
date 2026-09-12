import io,json,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import Mock,patch
from twitch_core import API,CLIENT_ID,TwitchError
from twitch_worker import TwitchWorker
from test_twitch import FakeAPI,FakeCredentials

class Opener:
    def __init__(self,error):self.error=error
    def open(self,*args,**kwargs):raise self.error

def http_error(status,message):
    return urllib.error.HTTPError('https://id.twitch.tv/oauth2/token',status,'error',{},io.BytesIO(json.dumps({'message':message}).encode()))

class AuthResilienceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name);self.now=[100.];self.credentials=FakeCredentials()
        self.worker=TwitchWorker(self.data,{'enabled':True,'channels':['one'],'tray':True},credentials=self.credentials,clock=lambda:self.now[0])
        self.worker.account={'access_token':'secret-access-value','refresh_token':'secret-refresh-value','login':'mist1x_x'};self.credentials.save(self.worker.account)
    def tearDown(self):self.tmp.cleanup()
    def test_validate_401_and_generic_refresh_rejection_are_retryable(self):
        for code,source in ((401,'validate'),('refresh_rejected','refresh'),('network','validate')):
            account=dict(self.worker.account);saved=dict(self.credentials.value)
            self.worker.auth_failure(TwitchError(code,source=source))
            self.assertEqual(self.worker.account,account);self.assertEqual(self.credentials.value,saved)
            self.assertEqual(self.worker.snapshot()['phase'],'reconnecting');self.assertFalse(self.worker.auth_blocked)
        self.assertEqual(self.worker.snapshot()['auth_retries'],3);self.assertEqual(self.worker.next_retry,self.now[0]+20)
    def test_backoff_caps_and_success_resets_diagnostic_state(self):
        for _ in range(7):self.worker.auth_failure(TwitchError('network'))
        self.assertEqual(self.worker.next_retry,self.now[0]+60);self.assertEqual(self.worker.snapshot()['auth_retries'],7)
        self.worker.api=FakeAPI([{'client_id':CLIENT_ID,'login':'mist1x_x','user_id':'42','scopes':['chat:read'],'expires_in':1000}])
        self.worker.validate();self.assertEqual(self.worker.auth_failures,0);self.assertFalse(self.worker.auth_blocked)
        self.worker.handle_line(':tmi.twitch.tv 001 mist1x_x :Welcome')
        self.assertEqual(self.worker.snapshot()['auth_retries'],0);self.assertEqual(self.worker.snapshot()['auth_reason'],'')
    def test_only_confirmed_invalid_refresh_clears_login(self):
        self.worker.auth_failure(TwitchError('invalid_refresh',source='refresh'))
        self.assertIsNone(self.worker.account);self.assertIsNone(self.credentials.value);self.assertTrue(self.worker.auth_blocked)
        state=self.worker.snapshot();self.assertEqual(state['phase'],'auth_required');self.assertEqual(state['auth_reason'],'refresh_token_invalid')
    def test_irc_auth_failure_keeps_refresh_token_for_validation(self):
        self.worker.next_validate=999;self.worker.token_deadline=999
        self.worker.auth_failure(TwitchError(401,source='irc'))
        self.assertIsNotNone(self.worker.account);self.assertEqual(self.credentials.value['refresh_token'],'secret-refresh-value')
        self.assertEqual(self.worker.next_validate,0);self.assertEqual(self.worker.token_deadline,0)
    def test_http_response_classification_uses_endpoint_and_message(self):
        fields={'grant_type':'refresh_token','refresh_token':'secret','client_id':CLIENT_ID}
        with patch('urllib.request.build_opener',return_value=Opener(http_error(400,'Invalid refresh token'))):
            with self.assertRaises(TwitchError) as caught:API().request('token',fields)
        self.assertEqual(caught.exception.code,'invalid_refresh');self.assertEqual(caught.exception.source,'refresh')
        with patch('urllib.request.build_opener',return_value=Opener(http_error(400,'Temporary rejection'))):
            with self.assertRaises(TwitchError) as caught:API().request('token',fields)
        self.assertEqual(caught.exception.code,'refresh_rejected')
        with patch('urllib.request.build_opener',return_value=Opener(http_error(401,'invalid access token'))):
            with self.assertRaises(TwitchError) as caught:API().request('validate',token='secret')
        self.assertEqual(caught.exception.code,401);self.assertEqual(caught.exception.source,'validate')
        with patch('urllib.request.build_opener',return_value=Opener(http_error(400,'missing client secret'))):
            with self.assertRaises(TwitchError) as caught:API().request('token',fields)
        self.assertEqual(caught.exception.code,'client_type');self.assertEqual(caught.exception.source,'refresh')
    def test_client_type_error_preserves_login_and_manual_retry_unblocks(self):
        saved=dict(self.credentials.value)
        self.worker.auth_failure(TwitchError('client_type',source='refresh'))
        self.assertEqual(self.worker.credentials.value,saved);self.assertEqual(self.worker.account,saved)
        self.assertTrue(self.worker.auth_blocked);self.assertEqual(self.worker.snapshot()['auth_reason'],'client_type_confidential')
        self.assertIn('Public',self.worker.snapshot()['text'])
        self.worker.command('retry');self.worker.process_commands()
        self.assertFalse(self.worker.auth_blocked);self.assertEqual(self.worker.next_retry,0)
        self.assertEqual(self.worker.next_validate,0);self.assertEqual(self.worker.snapshot()['auth_retries'],0)
    def test_three_generic_refresh_rejections_stop_waiting_but_keep_login(self):
        for _ in range(3):self.worker.auth_failure(TwitchError('refresh_rejected',source='refresh'))
        self.assertTrue(self.worker.auth_blocked);self.assertIsNotNone(self.worker.account);self.assertIsNotNone(self.credentials.value)
        self.assertEqual(self.worker.snapshot()['auth_reason'],'refresh_rejected_repeated')
    def test_wake_resets_backoff_and_reconnects_immediately(self):
        wall=[1000.];self.worker.wall_clock=lambda:wall[0];self.worker.last_loop_wall=wall[0]
        self.worker.auth_failures=4;self.worker.next_retry=999;wall[0]+=120
        self.assertTrue(self.worker.wake_check());self.assertEqual(self.worker.next_retry,0);self.assertEqual(self.worker.auth_failures,0)
        self.assertEqual(self.worker.snapshot()['phase'],'reconnecting')
    def test_public_diagnostics_have_no_tokens(self):
        self.worker.auth_failure(TwitchError('refresh_rejected',source='refresh'));self.worker.persist_status(force=True)
        status=(self.data/'twitch/status.json').read_text(encoding='utf-8');events=(self.data/'twitch/connection-events.json').read_text(encoding='utf-8')
        self.assertNotIn('secret-access-value',status+events);self.assertNotIn('secret-refresh-value',status+events)

if __name__=='__main__':unittest.main()
