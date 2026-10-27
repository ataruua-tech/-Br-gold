import contextlib
import io
import json
import os
import sys
import tempfile
import textwrap
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
import collect_full_universe as full
from build_full_universe_workflow import build, ROOT


class UniverseDataTests(unittest.TestCase):
    def test_missing_listing_date_remains_unknown_not_fabricated(self):
        result = full.contract_dates({'LSTTRADE': '2027-06-18'}, {'secid': 'GDM7', 'calendar_expiration': '2027-06-18'})
        self.assertIsNone(result['first_trade'])
        self.assertFalse(result['historical_listing_dates_verified'])
        self.assertTrue(result['missing_first_trade_is_not_imputed'])
        self.assertEqual(result['acquisition_start'], '2022-10-08')
        with self.assertRaises(ValueError):
            full.contract_dates({}, {'secid': 'GDM7', 'calendar_expiration': None})

    def test_half_open_cached_scopes_are_not_requested_again(self):
        first, end = date(2025, 1, 1), date(2025, 1, 10)
        cases = [([], [(first, end)]),
                 ([(first, end)], []),
                 ([(date(2024, 12, 1), date(2025, 1, 3)), (date(2025, 1, 5), date(2025, 2, 1))], [(date(2025, 1, 3), date(2025, 1, 5))]),
                 ([(first, date(2025, 1, 6)), (date(2025, 1, 5), date(2025, 1, 8))], [(date(2025, 1, 8), end)]),
                 ([(first, date(2025, 1, 5)), (date(2025, 1, 5), end)], [])]
        for covered, expected in cases:
            with self.subTest(covered=covered):
                self.assertEqual(full.missing_spans(first, end, covered), expected)

    def test_duplicate_cache_values_must_agree_and_null_margin_stays_null(self):
        contract = {'secid': 'BRG5', 'asset': 'BR'}
        row = {'secid': 'BRG5', 'asset_code': 'BR', 'tradedate': '2025-01-01', 'tradetime': '10:00:00', 'vol': 1, 'im': None}
        result = full.merge_rows([row, row.copy()], 'tradestats', contract, date(2025, 1, 1), date(2025, 1, 2))
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]['im'])
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            full.merge_rows([row, row | {'vol': 2}], 'tradestats', contract, date(2025, 1, 1), date(2025, 1, 2))
        with self.assertRaisesRegex(ValueError, 'Invalid volume'):
            full.merge_rows([row | {'vol': -1}], 'tradestats', contract, date(2025, 1, 1), date(2025, 1, 2))

    def test_standalone_workflow_runs_with_no_local_modules_or_missing_credentials(self):
        content = build()
        self.assertEqual(content, (ROOT / '.github/workflows/algopack-research-full-universe.yml').read_text())
        body = textwrap.dedent(content.split("python3 - <<'PY'\n", 1)[1].split('\n          PY\n', 1)[0]).rsplit('sys.exit(full_universe_main())', 1)[0]
        module = types.ModuleType('full_universe_standalone_test')
        with patch.dict(sys.modules, {module.__name__: module}), tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'MOEX_ALGOPACK_TOKEN': ''}), contextlib.redirect_stdout(io.StringIO()):
            exec(compile(body, '<full-universe-test>', 'exec'), module.__dict__)
            self.assertEqual(module.full_universe_main(Path(d)), 1)
            manifest = json.loads(Path(d, 'manifest.json').read_text())
            self.assertEqual(manifest['status'], 'BLOCKED_OR_INCOMPLETE')
            self.assertFalse(manifest['all_required_inputs_ready'])
            self.assertFalse(manifest['strategy_testing_performed'])
            self.assertEqual(manifest['requests'], 0)


if __name__ == '__main__':
    unittest.main()
