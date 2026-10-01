"""Domain correction and explicit compatibility tests; synthetic data only."""
import copy
import unittest
from unittest.mock import patch
import bandoribench as b

class RewardTopologyTests(unittest.TestCase):
    def task(self,tier,era='voice500_1500',server='cn'):
        return dict(tier=tier,era=era,server=server)
    def test_all_four_modern_cutoffs_are_rewarded(self):
        for tier in (500,1000,1500,2000):
            f=b.reward_feature_dict(self.task(tier))
            self.assertEqual(f['reward_known'],1)
            self.assertEqual(f['reward_boundary'],1)
            self.assertEqual(f['reward_log_distance'],0)
            self.assertEqual(f['reward_known_boundary_count'],4)
    def test_relative_attraction_is_separate_categorical_fact(self):
        for tier in (500,1000,1500,2000):
            f=b.reward_feature_dict(self.task(tier))
            self.assertEqual(f['reward_attraction_higher'],int(tier in (500,1500)))
            self.assertEqual(f['reward_other_rewarded'],int(tier in (1000,2000)))
            self.assertEqual(f['reward_attraction_known'],1)
    def test_legacy_sole_boundary_is_not_equal_to_modern_strong(self):
        for tier in (500,1000,1500,2000):
            f=b.reward_feature_dict(self.task(tier,'voice1000'))
            self.assertEqual(f['reward_boundary'],int(tier==1000))
            self.assertEqual(f['reward_legacy_sole_boundary'],int(tier==1000))
            self.assertEqual(f['reward_attraction_known'],0)
    def test_unmentioned_server_era_or_rank_remains_unknown(self):
        for task in (self.task(750),self.task(2500),self.task(1000,'unknown'),self.task(1000,server='jp')):
            self.assertFalse(b.reward_topology(task)['known'])
            self.assertIsNone(b.reward_topology(task)['is_reward_boundary'])
            self.assertEqual(b.reward_feature_dict(task)['reward_known'],0)
    def test_schema_source_and_legacy_geometry_explicit(self):
        task=self.task(1000)
        self.assertEqual(b.reward_topology(task)['schema'],'cn-reward-topology-v2')
        self.assertEqual(b.reward_topology(task)['source'],'user_supplied_2026-10-01')
        self.assertEqual(b.legacy_reward_feature_dict(task)['reward_boundary'],0)
        self.assertEqual(b.reward_feature_dict(task)['reward_boundary'],1)
    def test_existing_care_does_not_silently_consume_v2(self):
        public=b.public_bundle(b.freeze_walkforward(b.synthetic_dataset(),8))
        for task in public['tasks']:task.update(server='cn',era='voice500_1500')
        for event in public['reference_events']:event.update(server='cn',era='voice500_1500')
        before={name:b.predict(public,name)['predictions'] for name in ('care-s','care-s2')}
        with patch.object(b,'reward_feature_dict',side_effect=AssertionError('v2 must be opt-in for new CARE candidate')):
            after={name:b.predict(public,name)['predictions'] for name in ('care-s','care-s2')}
        self.assertEqual(before,after)
        self.assertTrue(all('prediction' in row and 'care' in row for rows in after.values() for row in rows))

if __name__=='__main__':unittest.main()
