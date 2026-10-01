"""Production-safe visibility fixes, independent of the rank-gap experiment."""
import copy
import sys
import unittest
from pathlib import Path

APP=Path(__file__).resolve().parents[1]/'examples'/'local_app'
sys.path.insert(0,str(APP))
from engine import HOUR,predict
from test_rui_mode import fixture


class ForecastVisibilityTests(unittest.TestCase):
    def test_future_and_unavailable_rows_cannot_change_either_mode(self):
        panel,state=fixture(9)
        dirty=copy.deepcopy(panel)
        for task in dirty['tasks']:
            task['history'] += [{'time':10*HOUR,'ep':1e9},
                                {'time':9*HOUR,'available_at':10*HOUR,'ep':1e9}]
        for mode in ('mashiro','rui'):
            self.assertEqual(predict(panel,state,mode),predict(dirty,state,mode))

    def test_visible_revisions_are_order_invariant_and_latest_wins(self):
        panel,state=fixture(9)
        for task in panel['tasks']:
            last=task['history'][-1]
            task['history'].append(dict(last,ep=last['ep']*1.01,available_at=last['time']+1))
            task['issued_at']+=2
        shuffled=copy.deepcopy(panel)
        for task in shuffled['tasks']:
            task['history'].reverse()
        for mode in ('mashiro','rui'):
            expected=predict(panel,state,mode)
            self.assertEqual(expected,predict(shuffled,state,mode))
            self.assertAlmostEqual(expected['current'][500],16300*1.01)

    def test_rui_replay_resolves_each_checkpoint_without_discarding_old_revision(self):
        from unittest.mock import patch
        import engine
        panel, state = fixture(9)
        for task in panel['tasks']:
            old = next(p for p in task['history'] if p['time'] == 3*HOUR)
            task['history'].append(dict(old, ep=old['ep']*1.01, available_at=7*HOUR))
        captured=[]
        original=engine._early_snapshot
        def record(p,s):
            captured.append(p)
            return original(p,s)
        with patch.object(engine,'_early_snapshot',side_effect=record):
            predict(panel,state,'rui')
        for p in captured:
            at=p['tasks'][0]['issued_at']
            for task in p['tasks']:
                values=[row for row in task['history'] if row['time']==3*HOUR]
                self.assertEqual(len(values),1)
                self.assertLessEqual(values[0].get('available_at',values[0]['time']),at)
                self.assertEqual('available_at' in values[0],at>=7*HOUR)

    def test_explicit_input_cutoff_limits_early_current_value(self):
        panel,state=fixture(9)
        for task in panel['tasks']:
            task['input_cutoff_at']=8*HOUR
        snapshot=predict(panel,state)
        self.assertEqual(snapshot['current'][500],14600)
        self.assertTrue(all(p['time']<=8*HOUR for rows in snapshot['visible_history'].values() for p in rows))

    def test_conflicting_revisions_fail_instead_of_depends_on_input_order(self):
        panel,state=fixture(9)
        task=panel['tasks'][0]
        task['history'].append(dict(task['history'][-1],ep=999999))
        with self.assertRaisesRegex(ValueError,'conflicting tracker'):
            predict(panel,state)


if __name__=='__main__':
    unittest.main()
