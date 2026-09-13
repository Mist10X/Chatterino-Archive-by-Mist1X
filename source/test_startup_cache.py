import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from moderation_store import ModerationIndex
from shared_bans import Store
from test_user_history import card

class StartupCacheTests(unittest.TestCase):
    def test_unchanged_cards_not_reparsed_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);Store(data).save(card());index=ModerationIndex(data)
            index.sync();index.combined.next_scan=0;index.sync();count=index.totals()['bans'];index.close()
            index=ModerationIndex(data)
            try:
                with patch.object(index.combined.store,'load',side_effect=AssertionError('Unchanged file parsed again')):index.sync()
                self.assertEqual(index.totals()['bans'],count)
                changed=card();changed['fetched_at']+=1;Store(data).save(changed);index.combined.next_scan=0
                with patch.object(index.combined.store,'load',wraps=index.combined.store.load) as load:index.sync();self.assertGreater(load.call_count,0)
            finally:index.close()
