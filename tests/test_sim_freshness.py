import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, date, timezone, timedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from sim_checkpoint import checkpoint_matches, generation_id, validate_publication, write_json_atomic, file_digest
from nse_bhavcopy_fetch import validate_csv, target_session


class FreshnessTests(unittest.TestCase):
    def test_legacy_and_changed_inputs_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'result.json'
            write_json_atomic(p, {'meta': {'last_date': '2026-06-18'}})
            self.assertFalse(checkpoint_matches(p, 'new', 'signals'))
            write_json_atomic(p, {'meta': {'generation': 'new', 'signal_signature': 'signals'}})
            self.assertTrue(checkpoint_matches(p, 'new', 'signals'))
            self.assertFalse(checkpoint_matches(p, 'changed', 'signals'))
            self.assertFalse(checkpoint_matches(p, 'new', 'changed'))
            source = Path(tmp) / 'input'
            source.write_text('old')
            before = generation_id('2026-11-03', [source])
            source.write_text('new')
            self.assertNotEqual(before, generation_id('2026-11-03', [source]))

    def test_partial_and_stale_publication_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta = {'configs': ['C1'], 'expected_configs': ['C1', 'C2'],
                    'generation': 'g', 'last_date': '2026-11-03'}
            write_json_atomic(root/'sim_meta.json', meta)
            write_json_atomic(root/'trade_ohlc.json', {})
            meta['ohlc_digest'] = file_digest(root/'trade_ohlc.json')
            with self.assertRaises(ValueError): validate_publication(root)
            meta['configs'].append('C2')
            write_json_atomic(root/'sim_meta.json', meta)
            for cid in meta['configs']:
                write_json_atomic(root/f'sim_results_{cid}.json', {'meta': {'generation': 'g', 'last_date': '2026-06-18'}})
            with self.assertRaises(ValueError): validate_publication(root)
            for cid in meta['configs']:
                write_json_atomic(root/f'sim_results_{cid}.json', {'meta': {'generation': 'g', 'last_date': '2026-11-03'}})
            validate_publication(root)

    def test_bhavcopy_validation_and_ist_target(self):
        header = 'SYMBOL,SERIES,DATE1,CLOSE_PRICE,OPEN_PRICE,LOW_PRICE,HIGH_PRICE\n'
        content = (header + 'TEST,EQ,03-Nov-2026,100,100,99,101\n'*100).encode()
        validate_csv(content, date(2026, 11, 3))
        with self.assertRaises(ValueError): validate_csv(content, date(2026, 11, 2))
        with self.assertRaises(ValueError): validate_csv(b'<html>upstream error</html>', date(2026,11,3))
        ist = timezone(timedelta(hours=5, minutes=30))
        self.assertEqual(target_session(datetime(2026,11,3,20,tzinfo=ist)), date(2026,11,3))
        self.assertEqual(target_session(datetime(2026,11,2,10,tzinfo=ist)), date(2026,10,30))

    def test_simulation_legacy_refresh_resume_and_empty_config(self):
        import pandas as pd
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ['db', 'config', 'docs/data']: (root/folder).mkdir(parents=True)
            config = {'id':'C1','max_buys':1,'buy_drop':0.04,'target':0.05,'stoploss':0.05,
                      'max_duration':10,'days_back':5,'pct_min':-0.1,'pct_max':0.1,'ath_min':-1,'ath_max':1}
            other = {**config, 'id':'C2'}
            (root/'config/params.json').write_text(json.dumps({'configs':[config, other], 'simulation':{}}))
            pd.DataFrame([{'SYMBOL':'TEST','DATE1':pd.Timestamp('2026-11-03'),
                           'PREV_CLOSE':100,'OPEN_PRICE':100,'HIGH_PRICE':101,'LOW_PRICE':99,'CLOSE_PRICE':100}]).to_parquet(root/'db/eq_data.parquet', index=False)
            write_json_atomic(root/'docs/data/sim_results_C1.json', {'meta':{'last_date':'2026-06-18'}, 'signals':[{'old':True}]})
            env = {**os.environ, 'SKIP_EXCEL':'true', 'SIM_TIME_BUDGET_MINUTES':'50'}
            def run():
                return subprocess.run([sys.executable,str(ROOT/'src/05_run_simulation.py')],cwd=root,env=env,capture_output=True,text=True)
            first = run()
            self.assertEqual(first.returncode, 0, first.stdout+first.stderr)
            meta = json.loads((root/'docs/data/sim_meta.json').read_text())
            self.assertEqual(set(meta['configs']), {'C1','C2'})
            self.assertEqual(meta['last_date'], '2026-11-03')
            self.assertEqual(json.loads((root/'docs/data/sim_results_C1.json').read_text())['signals'], [])
            second = run()
            self.assertEqual(second.returncode,0,second.stdout+second.stderr)
            self.assertIn('2/2 current',second.stdout)
            # A matching partial resumes just the missing config; old OHLC keys do not leak.
            (root/'docs/data/sim_results_C2.json').unlink()
            write_json_atomic(root/'docs/data/trade_ohlc.json', {'TEST_2026-06-08_C2':{}})
            checkpoint = json.loads((root/'docs/data/sim_meta.json').read_text())
            checkpoint['ohlc_digest'] = file_digest(root/'docs/data/trade_ohlc.json')
            write_json_atomic(root/'docs/data/sim_meta.json', checkpoint)
            third = run()
            self.assertEqual(third.returncode,0,third.stdout+third.stderr)
            self.assertIn('1/2 current',third.stdout)
            self.assertEqual(json.loads((root/'docs/data/trade_ohlc.json').read_text()),{})
            # A price/input change invalidates every previous configuration.
            eq = pd.read_parquet(root/'db/eq_data.parquet')
            eq['CLOSE_PRICE'] = 102
            eq.to_parquet(root/'db/eq_data.parquet',index=False)
            fourth = run()
            self.assertEqual(fourth.returncode,0,fourth.stdout+fourth.stderr)
            self.assertIn('0/2 current',fourth.stdout)


if __name__ == '__main__': unittest.main()
