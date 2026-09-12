import json,socket,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
import test_twitch as fixtures
from connection_alerts import connection_warning

class QuietSocket(fixtures.Socket):
    def recv(self,size):raise socket.timeout()

class ChannelRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.TwitchTests();self.fixture.setUp();self.now=[100.]
        self.w=self.fixture.worker(clock=lambda:self.now[0]);self.w.account={'login':'mist1x_x'};self.w.connection=QuietSocket()
        self.w.ready=True;self.w.last_traffic=100.;self.w.config['channels']=['dangerlyoha','morphe_ya']
        self.w.replay=Mock();self.w.replay.wanted.return_value=[]
        self.w.confirm_channel('dangerlyoha');self.w.confirm_channel('morphe_ya');self.w.replay.reset_mock()
    def tearDown(self):self.fixture.tearDown()
    def tick(self,seconds=0):
        self.now[0]+=seconds;self.w.last_traffic=self.now[0];self.w.read_chat()
    def test_informational_notices_and_send_restrictions_never_drop_reading(self):
        for msg in ('emote_only_on','emote_only_off','followers_on','followers_off','slow_on','slow_off','subs_on','subs_off','msg_banned','msg_timedout','msg_ratelimit','msg_requires_verified_phone_number','unknown_future_notice'):
            self.w.handle_line(f'@msg-id={msg} :tmi.twitch.tv NOTICE #morphe_ya :NOTICE BODY NOT LOGGED')
        self.tick(40)
        self.assertEqual(self.w.joined,{'dangerlyoha','morphe_ya'});self.assertFalse(self.w.failed)
        self.w.replay.connection.assert_not_called();self.assertEqual(self.w.connection.sent,[])
        self.w.persist_status(force=True)
        log=(self.fixture.data/'twitch/connection-events.json').read_text(encoding='utf-8')
        self.assertNotIn('NOTICE BODY',log);self.assertIn('followers_on',log)
    def test_real_part_retries_only_affected_channel_and_confirmation_clears_failure(self):
        self.w.handle_line(':other!other@other.tmi.twitch.tv PART #morphe_ya');self.assertIn('morphe_ya',self.w.joined)
        self.w.handle_line(':mist1x_x!mist1x_x@mist1x_x.tmi.twitch.tv PART #morphe_ya')
        self.assertEqual(self.w.joined,{'dangerlyoha'});self.tick(29);self.assertFalse(self.w.connection.sent)
        self.tick(1);self.assertEqual(self.w.connection.sent,[b'JOIN #morphe_ya\r\n'])
        self.w.handle_line('@room-id=77 :tmi.twitch.tv USERSTATE #morphe_ya')
        self.assertFalse(self.w.failed);self.assertFalse(self.w.pending);self.assertFalse(self.w.join_retries)
        self.assertEqual(self.w.snapshot()['joined'],['dangerlyoha','morphe_ya'])
        self.w.replay.connection.assert_called_with(self.w.joined)
    def test_join_timeout_keeps_retrying_with_backoff_and_bounded_rate(self):
        self.w.joined.discard('morphe_ya');self.tick();self.assertEqual(len(self.w.connection.sent),1)
        self.tick(31);self.assertEqual(self.w.channel_errors['morphe_ya'],'join_timeout')
        self.tick(29);self.assertEqual(len(self.w.connection.sent),1)
        self.tick(1);self.assertEqual(len(self.w.connection.sent),2)
        self.tick(31);self.tick(59);self.assertEqual(len(self.w.connection.sent),2)
        self.tick(1);self.assertEqual(len(self.w.connection.sent),3)
        self.assertEqual(self.w.joined,{'dangerlyoha'})
    def test_actual_message_repairs_stale_state_and_is_still_archived(self):
        self.w.retry_channel('morphe_ya','join_timeout');self.w.users={'mist1x_x':True}
        self.w.handle_line(fixtures.LINE)
        self.assertIn('morphe_ya',self.w.joined);self.assertFalse(self.w.failed);self.assertEqual(self.w.snapshot()['saved'],1)
        self.w.handle_line(fixtures.LINE);self.assertEqual(self.w.snapshot()['saved'],1)
        self.assertFalse(self.w.connection.sent)
    def test_suspended_channel_waits_longer_and_removed_channel_is_not_rejoined(self):
        self.w.handle_line('@msg-id=msg_channel_suspended :tmi.twitch.tv NOTICE #morphe_ya :unavailable')
        self.tick(299);self.assertFalse(self.w.connection.sent)
        self.tick(1);self.assertEqual(len(self.w.connection.sent),1)
        self.w.config['channels']=['dangerlyoha'];self.tick()
        self.assertFalse(self.w.failed);self.assertFalse(self.w.pending)
        count=len(self.w.connection.sent);self.tick(600);self.assertEqual(len(self.w.connection.sent),count)
    def test_quiet_channel_is_not_disconnected_and_global_join_rate_is_preserved(self):
        self.tick(600);self.assertFalse(self.w.connection.sent);self.assertIn('morphe_ya',self.w.joined)
        self.w.joined.clear();self.tick();self.assertEqual(len(self.w.connection.sent),1)
        self.tick(2.1);self.assertEqual(len(self.w.connection.sent),1)
        self.tick(.2);self.assertEqual(len(self.w.connection.sent),2)
    def test_banner_names_missing_channel(self):
        warning=connection_warning({'phase':'connected','joined':['dangerlyoha']},{'enabled':True},['morphe_ya','dangerlyoha'])
        self.assertIn('#morphe_ya',warning[1]);self.assertNotIn('#dangerlyoha',warning[1])
    def test_authentication_error_still_disconnects_and_log_has_no_secrets(self):
        with self.assertRaises(fixtures.TwitchError):self.w.handle_line(':tmi.twitch.tv NOTICE * :Login authentication failed')
        self.w.account['access_token']='SECRET_TOKEN';self.w.report(auth_code='SECRET_CODE')
        for i in range(150):self.w.connection_event('notice','morphe_ya','slow_on')
        self.w.persist_status(force=True)
        events=json.loads((self.fixture.data/'twitch/connection-events.json').read_text(encoding='utf-8'))
        self.assertEqual(len(events),100)
        self.assertNotIn('SECRET',(self.fixture.data/'twitch/status.json').read_text(encoding='utf-8'))
        self.assertNotIn('SECRET',json.dumps(events))

if __name__=='__main__':unittest.main()
