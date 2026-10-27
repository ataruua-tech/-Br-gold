import contextlib
import io
import json
import tempfile
import unittest
import types
import sys
from pathlib import Path
from unittest.mock import patch
import verify_empty_windows as verify
from build_empty_window_workflow import script, build, ROOT


def table(rows):
    columns = ['begin', 'open', 'high', 'low', 'close', 'volume']
    return {'candles': {'columns': columns, 'data': [[r[k] for k in columns] for r in rows]}}


class EmptyWindowTests(unittest.TestCase):
    def test_empty_both_is_archived_without_asserting_closed_session(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return table([])
        with tempfile.TemporaryDirectory() as d, patch.object(verify.ac.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(verify.verify_empty_windows(Path(d), fetch), 0)
            report = json.loads(Path(d, 'manifest.json').read_text())
            self.assertEqual(len(calls), 6)
            self.assertFalse(report['calendar_coverage_verified'])
            self.assertFalse(report['exact_agent_replay_ready'])
            self.assertEqual({w['status'] for w in report['windows']}, {'SOURCE_EMPTY_BOTH_INTERVALS'})
            self.assertTrue(all(u.startswith('https://apim.moex.com/') for u in calls))
            self.assertEqual(len(list(Path(d, 'responses').glob('*.gz'))), 6)

    def test_nonempty_h1_with_empty_minutes_is_blocking(self):
        row = {'begin': '2025-01-01T12:00:00', 'open': 100, 'high': 101, 'low': 99, 'close': 100, 'volume': 1}
        def fetch(url):
            if 'interval=60' in url and 'start=0' in url:
                return table([row])
            return table([])
        with tempfile.TemporaryDirectory() as d, patch.object(verify, 'WINDOWS', verify.WINDOWS[:1]), patch.object(verify.ac.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(verify.verify_empty_windows(Path(d), fetch), 1)
            report = json.loads(Path(d, 'manifest.json').read_text())
            self.assertEqual(report['windows'][0]['status'], 'COVERAGE_ERROR')
            self.assertEqual(report['windows'][0]['h1_rows'], 1)

    def test_auth_failure_stops_and_keeps_redacted_error(self):
        def fetch(url):
            raise RuntimeError('MOEX HTTP 401 fake-research-token')
        with tempfile.TemporaryDirectory() as d, patch.dict(verify.ac.os.environ, {'MOEX_ALGOPACK_TOKEN': 'fake-research-token'}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(verify.verify_empty_windows(Path(d), fetch), 1)
            text = Path(d, 'manifest.json').read_text()
            report = json.loads(text)
            self.assertEqual(report['requests'], 1)
            self.assertNotIn('fake-research-token', text)
            self.assertEqual(len(report['windows']), 1)

    def test_standalone_workflow_matches_source_and_runs_without_local_modules(self):
        self.assertEqual(build(), (ROOT / '.github/workflows/algopack-research-empty-windows.yml').read_text())
        content = script().rsplit('sys.exit(verify_empty_windows())', 1)[0]
        module = types.ModuleType('empty_window_standalone_test')
        with patch.dict(sys.modules, {module.__name__: module}), tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            exec(compile(content, '<standalone-test>', 'exec'), module.__dict__)
            with patch.object(module.time, 'sleep'):
                self.assertEqual(module.verify_empty_windows(Path(d), lambda _: table([])), 0)
            report = json.loads(Path(d, 'manifest.json').read_text())
            self.assertEqual(report['requests'], 6)
            self.assertFalse(report['strategy_changed'])


if __name__ == '__main__':
    unittest.main()
