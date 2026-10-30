"""Checks of the independent mathematical verifier; no native-execution claim."""
import copy
from datetime import datetime, timedelta
from decimal import Decimal as D
import json
import os
from pathlib import Path
import unittest

import independent_indicators as verifier


def falling_series(count=65):
    return [dict(begin=str(datetime(2026, 1, 1) + timedelta(hours=i)),
                 open=100-i, high=100-i, low=100-i, close=100-i, volume=1)
            for i in range(count)]


class IndependentChecks(unittest.TestCase):
    def archived_input(self, symbol):
        directories=[]
        if os.environ.get('CONTROL_PREPARED_DIR'):
            directories.append(Path(os.environ['CONTROL_PREPARED_DIR']))
        directories.extend([Path.cwd()/'control-output'/'prepared',
                            Path(__file__).parent/'local-prepared',
                            Path(__file__).parent/'independent-checks'])
        for directory in directories:
            for suffix in ('-raw.json','-H1.json'):
                path=directory/(symbol+suffix)
                if path.exists():
                    return path
        if os.environ.get('REQUIRE_ARCHIVE_CHECKS')=='1':
            self.fail('Required archived '+symbol+' input not materialized')
        self.skipTest('Archived '+symbol+' input not materialized; only synthetic tests ran')

    def test_source_rsi_warmup_and_one_sided_quirk(self):
        rows = verifier.calculate(falling_series())
        self.assertTrue(all(r['rsi'] == 0 for r in rows[:35]))
        self.assertTrue(all(r['rsi'] == 100 for r in rows[35:]))
        self.assertFalse(rows[34]['rsi_ready'])
        self.assertTrue(rows[35]['rsi_ready'])

    def test_population_variance_hand_calculation(self):
        rows = verifier.calculate(falling_series())
        # At index 32, closes 88..68 have mean 78 and variance 110/3.
        self.assertEqual(rows[32]['bollinger']['center'], D(78))
        self.assertEqual(rows[32]['bollinger']['upper'], D('84.055301'))
        self.assertEqual(rows[32]['bollinger']['lower'], D('71.944699'))

    def test_readiness_is_source_specific(self):
        rows = verifier.calculate(falling_series())
        self.assertFalse(rows[9]['fast']['ready'])
        self.assertTrue(rows[10]['fast']['ready'])
        self.assertFalse(rows[49]['slow']['ready'])
        self.assertTrue(rows[50]['slow']['ready'])
        self.assertFalse(rows[58]['A_robot_warmup_passed'])
        self.assertTrue(rows[59]['A_robot_warmup_passed'])
        self.assertFalse(rows[21]['bollinger']['ready'])
        self.assertTrue(rows[22]['bollinger']['ready'])
        self.assertTrue(rows[20]['B_robot_warmup_passed'])

    def test_future_values_cannot_change_prefix(self):
        candles = falling_series()
        whole = verifier.calculate(copy.deepcopy(candles))
        for count in (23, 35, 36, 51, 60):
            partial = verifier.calculate(copy.deepcopy(candles[:count]))
            self.assertEqual(partial, whole[:count])

    def test_bad_inputs_rejected(self):
        for variant in ('duplicate', 'reverse', 'bad_ohlc', 'negative_volume'):
            candles = falling_series()
            if variant == 'duplicate':
                candles[1]['begin'] = candles[0]['begin']
            elif variant == 'reverse':
                candles.reverse()
            elif variant == 'bad_ohlc':
                candles[0]['high'] = -1
            else:
                candles[0]['volume'] = -1
            with self.assertRaises(ValueError, msg=variant):
                verifier.calculate(candles)

    def test_archived_brx6_first_events(self):
        path = self.archived_input('BRX6')
        rows = verifier.calculate(json.loads(path.read_text(), parse_float=D, parse_int=D))
        self.assertEqual(len(rows), 1686)
        self.assertEqual(sum(r['A_raw_buy'] or r['A_raw_sell'] for r in rows), 609)
        self.assertEqual(sum(r['B_raw_buy'] or r['B_raw_sell'] for r in rows), 2)
        first_a = rows[59]
        self.assertEqual(first_a['begin'], '2026-07-04 10:00:00')
        self.assertEqual(first_a['fast']['center'], D('73.769904903'))
        self.assertEqual(first_a['slow']['center'], D('73.711777911'))
        self.assertTrue(first_a['A_raw_buy'])
        bad_b = rows[25]
        self.assertEqual(bad_b['begin'], '2026-07-01 19:00:00')
        self.assertEqual(bad_b['rsi'], 0)
        self.assertTrue(bad_b['B_raw_signal_with_unready_RSI'])

    def test_archived_gdz6_first_events(self):
        path = self.archived_input('GDZ6')
        rows = verifier.calculate(json.loads(path.read_text(), parse_float=D, parse_int=D))
        self.assertEqual(len(rows), 1865)
        self.assertEqual(sum(r['A_raw_buy'] or r['A_raw_sell'] for r in rows), 749)
        self.assertEqual(sum(r['B_raw_buy'] or r['B_raw_sell'] for r in rows), 4)
        self.assertEqual(rows[59]['fast']['center'], D('4255.524454266'))
        self.assertEqual(rows[59]['slow']['center'], D('4259.135022994'))
        self.assertTrue(rows[59]['A_raw_sell'])
        first_b = next(r for r in rows if r['B_raw_buy'] or r['B_raw_sell'])
        self.assertEqual(first_b['begin'], '2026-08-06 13:00:00')
        self.assertEqual(first_b['rsi'], D('70.81'))
        self.assertTrue(first_b['B_raw_sell'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
