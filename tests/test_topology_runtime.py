"""Synthetic state, causal update, and usable point-only entry contracts."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'examples/local_app'))
import export_state
import training_state as S
from engine import predict, read_state
import reward_topology_control as T
from topology_report import build


def event(eid):
    start = eid * 120 * T.HOUR
    end = start + 100*T.HOUR
    tiers = {}
    for tier, scale in zip(T.TIERS, (4., 3., 2., 1.)):
        points = [{'time': start+i*T.HOUR, 'ep': scale*(10000+30*i+i*i)} for i in range(101)]
        tiers[str(tier)] = {'points': points, 'label': {'time': end+1, 'ep': points[-1]['ep']}}
    return {'event_id': eid, 'server': 'cn', 'era': 'voice500_1500', 'event_type': 'story',
            'start_at': start, 'end_at': end, 'aggregate_end_at': end+1, 'tiers': tiers}


class TopologyRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
    def export(self, events, topology=True):
        path = self.root/'input.json';path.write_text(json.dumps({'reference_events': events}))
        return export_state.export(path, self.root/'state.json', with_topology=topology)
    def test_legacy_export_unchanged_fit_and_available_modes(self):
        events = [event(i) for i in range(1, 5)]
        old = self.export(events, False); new = self.export(events)
        fit = dict(new['fit']);fit.pop('topology_t')
        self.assertEqual(old['fit'], fit)
        self.assertEqual(read_state(self.root/'state.json')['fit_sha256'], new['fit_sha256'])
        with self.assertRaisesRegex(ValueError, '不含 T'):
            predict(T.make_panel(event(5), 12), old, 'topology')
    def test_prequential_incremental_full_reconstruction_all_horizons(self):
        seed = self.export([event(i) for i in range(1, 4)])
        full = self.export([event(i) for i in range(1, 5)])
        delta = S.event_delta(seed, event(4))
        core = {'schema': S.SCHEMA, 'seed_fit_sha256': seed['fit_sha256'], 'updates': [
            {'event_id': 4, 'start_at': event(4)['start_at'], 'end_at': event(4)['end_at'], 'delta': delta}]}
        rebuilt = S.rebuild(seed, dict(core, sha256=S._digest(core)))
        self.assertEqual(full['fit']['topology_t'], rebuilt['fit']['topology_t'])
        for h in T.HORIZONS:
            panel = T.make_panel(event(5), h)
            self.assertEqual(predict(panel, full, 'topology')['control'], predict(panel, rebuilt, 'topology')['control'])
    def test_future_and_unavailable_observations_do_not_change_point(self):
        state = self.export([event(i) for i in range(1, 4)])
        panel = T.make_panel(event(4), 12); dirty = copy.deepcopy(panel)
        for task in dirty['tasks']:
            issue = task['issued_at']
            task['history'] += [{'time': issue+1, 'ep': 1e20}, {'time': issue, 'available_at': issue+1, 'ep': 1e20}]
        self.assertEqual(predict(panel,state,'topology'), predict(dirty,state,'topology'))
    def test_target_or_unavailable_state_is_rejected(self):
        state = self.export([event(i) for i in range(1, 4)])
        with self.assertRaisesRegex(ValueError,'state contains'):
            predict(T.make_panel(event(3), 12),state,'topology')
        panel=T.make_panel(event(4),12); state['fit']['topology_t']['available_at']=panel['tasks'][0]['issued_at']+1
        with self.assertRaisesRegex(ValueError,'state contains'):
            predict(panel,state,'topology')
    def test_point_only_report_and_off_grid_disclosure(self):
        state = self.export([event(i) for i in range(1, 4)])
        for h, usage in ((12,'exact'),(13,'nearest_horizon_approximation'),(3,'outside_evaluated_range')):
            panel=T.make_panel(event(4),h)
            snapshot=predict(panel,state,'topology')
            self.assertEqual(snapshot['topology_diagnostics']['horizon_usage'],usage)
            self.assertEqual(snapshot['members'],[])
            self.assertEqual(snapshot['member_p10'],{})
            self.assertNotIn('rank_order_adjustment',snapshot)
            self.assertTrue(all(a>=b for a,b in zip(snapshot['control'].values(),list(snapshot['control'].values())[1:])))
            path=self.root/'report.html';build(snapshot,path)
            self.assertIn('不提供概率区间',path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError,'72 小时'):
            predict(T.make_panel(event(4),80),state,'topology')
    def test_residuals_use_prior_control_and_event_count(self):
        class Recording:
            def __init__(self):self.calls=[]
            def predict_panel(self,p):
                self.calls.append([t['tier'] for t in p['tasks']])
                return [{'case_id':t['case_id'],'prediction':t['history'][-1]['ep']*1.2} for t in p['tasks']]
        model=Recording();rows=T.collect_residuals(model,event(4),0)
        self.assertEqual(len(rows),5)
        self.assertEqual(model.calls,[[500,1000,2000],[1500]]*5)
        bias=T.grouped_bias([{'errors':[1,2,3,4]}],((500,1500),(1000,2000)))
        for tier, value in {500:.4,1500:.4,1000:.6,2000:.6}.items():self.assertAlmostEqual(bias[tier],value)
    def test_bad_state_records_fail_closed(self):
        state=self.export([event(1)])
        fit=state['fit']['topology_t'];fit['records'].append(copy.deepcopy(fit['records'][0]))
        with self.assertRaisesRegex(ValueError,'duplicate'):
            T.validate_fit(fit)
    def test_training_failure_does_not_mutate_seed(self):
        seed=self.export([event(1)]);before=copy.deepcopy(seed)
        with self.assertRaises(ValueError):S.event_delta(seed,event(1))
        self.assertEqual(seed,before)

if __name__=='__main__':unittest.main()
