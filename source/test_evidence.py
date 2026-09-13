import json,tempfile,unittest
from pathlib import Path
from moderation_store import ModerationIndex
from moderation_evidence import rules_save,MOD_SCOPES,REWARD_SCOPE,AUTOMOD_SCOPE,millis
from moderation_events import notification,ModerationEvents

BASE=1789200000000
class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name);self.index=ModerationIndex(self.data)
        rules_save(self.data,[{'channel':'channel','reward':'reward','action':'timeout','bot':'trustedbot','template':'{buyer} muted {target}','self':True},
            {'channel':'channel','reward':'unmute','action':'untimeout','bot':'trustedbot','template':'{buyer} unmuted {target}','self':False}])
    def tearDown(self):self.index.close();self.tmp.cleanup()
    def action(self,user='target',at=BASE+2000,kind='timeout',actor=None):
        e={'kind':kind,'user':user,'channel':'channel','at_ms':at,'duration':60 if kind=='timeout' else None,'source':'test','context':[]}
        self.index.ingest({'kind':'executor','actor':actor,'action':e} if actor else e);self.index.combined.sync(True);self.index.db.commit()
    def reward(self,buyer='buyer',target='target',status='observed',ident='one',at=BASE,reward='reward'):
        self.index.ingest({'kind':'reward','id':ident,'channel':'channel','buyer':buyer,'reward':reward,'input':'@'+target,'at_ms':at,'status':status})
    def bot(self,text='buyer muted target',bot='trustedbot',at=BASE+2100):
        self.index.ingest({'kind':'bot_result','id':str(at)+text,'channel':'channel','bot':bot,'text':text,'at_ms':at})
    def row(self):return next(r for r in self.index.query()['rows'] if r['kind'] in ('ban','timeout','delete'))
    def test_requires_purchase_bot_and_actual_action(self):
        self.action(actor='moderator');self.reward();self.assertIsNone(self.row()['reward_attribution'])
        self.bot();r=self.row();self.assertEqual(r['executors'],['moderator']);self.assertEqual(r['reward_attribution']['buyer'],'buyer')
    def test_no_attribution_without_purchase_or_from_imposter(self):
        self.action();self.bot(bot='imposter');self.reward();self.assertIsNone(self.row()['reward_attribution'])
    def test_canceled_and_out_of_order_receipts_do_not_match(self):
        self.action();self.bot();self.reward(status='canceled');self.assertIsNone(self.row()['reward_attribution'])
        self.reward(status='unfulfilled');self.assertIsNone(self.row()['reward_attribution'])
        self.reward(ident='late',at=BASE+3000);self.assertIsNone(self.row()['reward_attribution'])
    def test_two_purchases_or_two_actions_are_ambiguous(self):
        self.action();self.bot();self.reward();self.reward(ident='two');self.assertIsNone(self.row()['reward_attribution'])
    def test_self_purchase_needs_explicit_actual_target(self):
        self.action(user='buyer');self.reward(target='protected');self.bot();self.assertIsNone(self.row()['reward_attribution'])
        self.bot(text='buyer muted buyer',at=BASE+2200);self.assertTrue(self.row()['reward_attribution']['self'])
    def test_unmute_is_attached_to_original_punishment(self):
        self.action();self.action(at=BASE+12000,kind='untimeout',actor='mod2')
        self.reward(reward='unmute',at=BASE+10000);self.bot(text='buyer unmuted target',at=BASE+12100)
        lifted=self.row()['lifted_by'];self.assertEqual(lifted['executors'],['mod2']);self.assertEqual(lifted['reward_attribution']['buyer'],'buyer')
    def test_cross_source_executor_does_not_duplicate_timeout(self):
        self.action();self.index.ingest({'kind':'executor','actor':'mod','action':{'kind':'timeout','user':'target','channel':'channel','at_ms':BASE+2100,'duration':60,'source':'twitch_eventsub'}})
        self.index.combined.sync(True)
        self.assertEqual(self.index.query()['total'],1);self.assertEqual(self.row()['executors'],['mod'])
    def test_replay_reward_updates_preserve_cancellation(self):
        self.reward(status='canceled');self.reward(status='observed');self.action();self.bot();self.assertIsNone(self.row()['reward_attribution'])
    def test_cancellation_also_suppresses_duplicate_chat_receipt(self):
        self.reward(ident='irc:receipt');self.reward(ident='eventsub-receipt',status='canceled');self.action();self.bot();self.assertIsNone(self.row()['reward_attribution'])
    def test_two_actual_actions_are_not_arbitrarily_assigned(self):
        self.reward();self.bot();self.action();self.action(at=BASE+3000);self.assertIsNone(self.row()['reward_attribution'])
    def test_notification_maps_moderator_and_delete_identity(self):
        p={'subscription':{'type':'channel.moderate'},'event':{'broadcaster_user_login':'channel','action':'delete','moderator_user_login':'mod','delete':{'user_login':'target','message_id':'m','message_body':'text'}}}
        e=notification(p,'2026-09-12T12:00:00Z');self.index.ingest(e);self.index.combined.sync(True)
        r=self.row();self.assertEqual(r['executors'],['mod']);self.assertEqual(r['message']['text'],'text')
    def test_notification_captures_every_automod_hold(self):
        p={'subscription':{'type':'automod.message.hold'},'event':{'broadcaster_user_login':'channel','user_login':'target','user_name':'Target','user_id':'42','message_id':'held','message':'not public','held_at':'2026-09-12T12:00:00Z'}}
        e=notification(p,'2026-09-12T12:00:01Z');self.index.ingest(e);self.index.combined.sync(True)
        stored=self.index.db.execute("SELECT message FROM actions WHERE kind='automod'").fetchone()
        self.assertEqual(json.loads(stored[0])['text'],'not public')
        self.assertEqual(self.index.query(kind='automod_ban')['total'],0)
        self.action(at=millis('2026-09-12T12:00:05Z'),kind='ban')
        result=self.index.query(kind='automod_ban');self.assertEqual(result['total'],1)
        ban=result['rows'][0];self.assertEqual(ban['context'][-1]['id'],'held');self.assertTrue(ban['context'][-1]['automod']);self.assertTrue(ban['automod_ban'])
    def test_notifications_ignore_other_actions(self):
        self.assertIsNone(notification({'subscription':{'type':'channel.moderate'},'event':{'action':'vip'}},'2026-09-12T12:00:00Z'))
    def test_subscriptions_only_authorized_selected_channels(self):
        class Twitch:
            def wanted_channels(self):return ['channel','mine']
        service=ModerationEvents(Twitch());calls=[]
        def request(token,path,data=None):calls.append(path);return {'data':[{'broadcaster_login':'channel','broadcaster_id':'2'},{'broadcaster_login':'unselected','broadcaster_id':'3'}]}
        service.request=request
        subs=service.subscriptions({'access_token':'fake','user_id':'1','login':'mine','scopes':MOD_SCOPES+[REWARD_SCOPE,AUTOMOD_SCOPE,'user:read:moderated_channels']})
        self.assertEqual(len(subs),6);self.assertFalse(any(s['condition']['broadcaster_user_id']=='3' for s in subs))
        self.assertEqual(sum(s['type']=='automod.message.hold' for s in subs),2)
        self.assertTrue(all(s['condition']['broadcaster_user_id']=='1' for s in subs if 'redemption' in s['type']))
        self.assertEqual(service.subscriptions({'access_token':'fake','user_id':'1','login':'mine','scopes':['chat:read']}),[])

if __name__=='__main__':unittest.main()
