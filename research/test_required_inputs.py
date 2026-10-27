import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
import collect_required_inputs as inp
from build_required_input_workflow import build, script, ROOT


def panel_row(**changes):
    return dict(tradedate='2025-01-31', tradetime='10:00:00', secid='BRG5',
                asset_code='BR', vol=10, trades=3, val=1000, im=None,
                SYSTIME='2025-01-31 10:00:10') | changes


class RequiredInputTests(unittest.TestCase):
    def test_null_margin_stays_missing_and_aggregates_never_become_bbo(self):
        rows = [panel_row(), panel_row(tradetime='10:05:00', im=10528.82)]
        actual, coverage = inp.validate_panel(rows, {'secid': 'BRG5', 'asset': 'BR'}, date(2025, 1, 31), date(2025, 2, 1), 'tradestats')
        self.assertIsNone(actual[0]['im'])
        self.assertEqual(coverage['im_null_rows'], 1)
        self.assertEqual(coverage['im_positive_rows'], 1)
        self.assertFalse(coverage['historical_executable_BBO'])

    def test_ignored_contract_dates_nonfinite_and_conflicting_duplicates_fail(self):
        examples = [[panel_row(asset_code='GOLD')], [panel_row(tradedate='2025-02-01')],
                    [panel_row(im=float('nan'))], [panel_row(), panel_row(vol=11)]]
        for rows in examples:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                inp.validate_panel(rows, {'secid': 'BRG5', 'asset': 'BR'}, date(2025, 1, 31), date(2025, 2, 1), 'tradestats')

    def test_nonadjacent_repeated_page_is_detected(self):
        class Client:
            def __init__(self): self.offsets = []
            def request(self, path, query):
                self.offsets.append(query['start'])
                value = 'A' if len(self.offsets) != 2 else 'B'
                return {'data': {'columns': ['value'], 'data': [[value]]}}
        client = Client()
        with self.assertRaisesRegex(ValueError, 'Repeated page'):
            inp.input_rows(client, '/iss/test.json', 'data', {})
        self.assertEqual(client.offsets, [0, 1, 2])

    def test_actual_page_size_drives_offset_until_empty(self):
        class Client:
            def __init__(self): self.offsets = []
            def request(self, path, query):
                self.offsets.append(query['start'])
                values = [[1], [2]] if query['start'] == 0 else [[3]] if query['start'] == 2 else []
                return {'data': {'columns': ['value'], 'data': values}}
        client = Client()
        self.assertEqual(len(inp.input_rows(client, '/iss/test.json', 'data', {})), 3)
        self.assertEqual(client.offsets, [0, 2, 3])

    def test_standalone_failure_retains_manifest_without_local_dependencies(self):
        self.assertEqual(build(), (ROOT / '.github/workflows/algopack-research-required-inputs.yml').read_text())
        body = script().rsplit('sys.exit(required_inputs_main(FROZEN_CONTRACTS))', 1)[0]
        module = types.ModuleType('required_input_standalone_test')
        with patch.dict(sys.modules, {module.__name__: module}), tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'MOEX_ALGOPACK_TOKEN': '', 'INPUT_MODE': 'catalogue'}), contextlib.redirect_stdout(io.StringIO()):
            exec(compile(body, '<required-input-test>', 'exec'), module.__dict__)
            self.assertEqual(module.required_inputs_main(module.FROZEN_CONTRACTS, Path(d)), 1)
            report = json.loads(Path(d, 'manifest.json').read_text())
            self.assertFalse(report['all_required_inputs_ready'])
            self.assertFalse(report['fitting_or_backtesting_performed'])
            self.assertEqual(report['requests'], 0)


if __name__ == '__main__':
    unittest.main()
