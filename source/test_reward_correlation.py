from test_evidence import EvidenceTests,BASE
from moderation_evidence import rules_save,attribution_blocks

class RewardCorrelationTests(EvidenceTests):
    def configure(self):
        rules_save(self.data,[{'channel':'channel','reward':reward,'action':action,'mode':'correlation','duration':60}
            for reward,action in [('reward','timeout'),('unmute','untimeout')]])
    def test_target_comma_and_duration(self):
        self.configure();self.reward(target='target,');self.action()
        self.assertEqual(self.row()['reward_attribution']['buyer'],'buyer')
        row=self.row();row['duration']=600;self.assertIsNone(self.index.evidence.enrich(row)['reward_attribution'])
    def test_target_with_comment(self):
        self.configure();self.reward(target='target, вот так братан');self.action()
        self.assertEqual(self.row()['reward_attribution']['buyer'],'buyer')
    def test_partial_target_prefix_does_not_match(self):
        self.configure();self.reward(target='targetother comment');self.action()
        self.assertIsNone(self.row()['reward_attribution'])
    def test_no_bot_does_not_claim_confirmation(self):
        self.configure();self.reward();self.action()
        self.assertIn('исполнитель не подтверждён',attribution_blocks(self.row())[0]['text'])
    def test_no_self_inference(self):
        self.configure();self.reward(target='protected');self.action(user='buyer')
        self.assertIsNone(self.row()['reward_attribution'])
    def test_competing_purchases(self):
        self.configure();self.reward();self.reward(ident='two',buyer='other');self.action()
        self.assertIsNone(self.row()['reward_attribution'])
    def test_old_purchase(self):
        self.configure();self.reward(at=BASE-16000);self.action()
        self.assertIsNone(self.row()['reward_attribution'])
    def test_pending_unmute_does_not_invent_event(self):
        self.configure();self.action();self.reward(reward='unmute',at=BASE+5000,target='target,')
        row=self.row();self.assertIsNone(row['lifted_by']);self.assertEqual(row['unmute_purchases'],['buyer'])
        self.action(kind='untimeout',at=BASE+6000)
        row=self.row();self.assertEqual(row['unmute_purchases'],[])
        self.assertEqual(row['lifted_by']['reward_attribution']['buyer'],'buyer')
    def test_unmute_purchase_is_a_separate_moderation_row(self):
        self.configure();self.reward(reward='unmute',at=BASE+5000,target='target comment')
        result=self.index.query(kind='reward_unmute')
        self.assertEqual(result['total'],1);self.assertEqual(result['rows'][0]['kind'],'reward_unmute')
