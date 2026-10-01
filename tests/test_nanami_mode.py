"""Requested public wording and third-mode bulletin compatibility."""
import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from html.parser import HTMLParser
from unittest.mock import patch

APP=Path(__file__).resolve().parents[1]/'examples/local_app'
sys.path.insert(0,str(APP))
import app
from bulletins import format_bulletins,NANAMI_HEADING
from test_bulletins import fixture
from test_topology_runtime import event
from export_state import export
from engine import predict
from reward_topology_control import make_panel

HEADING='Nanami（在T1000和T1500上更优的实验性模型）'
TOOLTIP='七深是实验性模型，可能在T1000和T1500上表现更优'

class NanamiTests(unittest.TestCase):
    def test_exact_public_selector_and_tooltip(self):
        class Parser(HTMLParser):
            def handle_starttag(self,tag,attrs):
                values=dict(attrs)
                if values.get('id')=='mode-topology':self.attrs=values
        html=(APP/'index.html').read_text(encoding='utf-8');p=Parser();p.feed(html)
        self.assertEqual(p.attrs['data-tooltip'],TOOLTIP)
        self.assertEqual(p.attrs['aria-description'],TOOLTIP)
        self.assertIn('>Nanami</button>',html)
        self.assertNotIn('Topology T',html)
        self.assertEqual(html.count('七深'),2) # tooltip and accessible description only
    def test_point_block_exact_heading_no_fabricated_interval(self):
        panel,snaps=fixture();point=copy.deepcopy(snaps['mashiro'])
        point['member_p10']={};point['member_p90']={};snaps['nanami']=point
        result=format_bulletins(panel,snaps)
        self.assertEqual(NANAMI_HEADING,HEADING)
        self.assertEqual(result['numeric'].split('NANKAORI\n')[1], '1000\n800\n600\n400\nNANAOI\nUNAVAILABLE\nEDEDEDED\n')
        tail=result['readable'].split(HEADING+'\n')[1]
        self.assertNotIn('10%',tail);self.assertNotIn('90%',tail)
        self.assertIn('T1000：800',tail)
        alias=dict(snaps);alias['topology']=alias.pop('nanami')
        self.assertEqual(format_bulletins(panel,alias),result)
    def test_unavailable_nanami_retains_existing_modes_and_reports_reason(self):
        panel,snaps=fixture()
        result=format_bulletins(panel,snaps,{'nanami':'缺少校准状态'})
        self.assertIn('NANKAORI\nUNAVAILABLE\nNANAOI\nUNAVAILABLE\n',result['numeric'])
        self.assertIn(HEADING+'\n暂不可用：缺少校准状态',result['readable'])
        self.assertIn('MASKAORI',result['numeric']);self.assertIn('RUIKAORI',result['numeric'])
    def test_backend_saves_unavailable_reason_and_two_valid_snapshots(self):
        panel,snaps=fixture()
        with tempfile.TemporaryDirectory() as d:
            local=object.__new__(app.LocalApp);local.lock=threading.Lock();local.state={'fit_sha256':'test'};local.output_dir=Path(d)
            with patch.object(app,'live_panel',return_value=(panel,['synthetic'])),patch.object(app,'predict',side_effect=[snaps['mashiro'],snaps['rui'],ValueError('超过72小时')]),patch.object(app.time,'time',return_value=snaps['mashiro']['issued_at']/1000):
                result=local.generate_bulletins('bestdori',325)
            stored=json.loads((Path(d)/(result['report_id']+'.json')).read_text(encoding='utf-8'))
            self.assertEqual(set(stored['snapshots']),{'mashiro','rui'})
            self.assertEqual(stored['bulletins']['unavailable'],{'nanami':'超过72小时'})
            for kind in ('numeric','readable'):
                self.assertEqual((Path(d)/(result['report_id']+'.'+kind+'.txt')).read_text(encoding='utf-8'),result[kind])
    def test_api_alias_preserves_all_predictions_and_state(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);source=d/'input.json';source.write_text(json.dumps({'events':[event(i) for i in range(1,4)]}))
            state=export(source,d/'state.json',True);before=copy.deepcopy(state)
            for h in (72,48,24,12,6):
                panel=make_panel(event(4),h)
                a=predict(panel,state,'nanami');b=predict(panel,state,'topology')
                self.assertEqual(a,b);self.assertEqual(a['logic_mode'],'Nanami')
            self.assertEqual(state,before)
    def test_saved_copy_download_all_use_same_returned_text(self):
        html=(APP/'index.html').read_text(encoding='utf-8')
        self.assertIn('bulletinData[bulletinFormat]',html)
        self.assertNotIn('Mashiro/Rui 文本报文',html)

if __name__=='__main__':unittest.main()
