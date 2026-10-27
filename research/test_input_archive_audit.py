import hashlib
import base64
import contextlib
import gzip
import io
import json
import unittest
import zipfile
from pathlib import Path
import audit_required_input_artifacts as audit
from build_input_audit_workflow import build, ROOT


def archive(extra=None, record=None):
    manifest = {'source': 'apim.moex.com', 'orders_enabled': False,
                'fitting_or_backtesting_performed': False, 'requests': 0,
                'mode': 'panels', 'year': 2025, 'status': 'PARTIAL_ERROR',
                'records': [record] if record else []}
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest))
        z.writestr('requests.jsonl', '')
        for name, body in (extra or {}).items():
            z.writestr(name, body)
    return data.getvalue()


class InputArchiveAuditTests(unittest.TestCase):
    def test_data_acquisition_failure_is_preserved_after_integrity_success(self):
        body = archive(record={'status': 'ERROR', 'contract': 'BRG5', 'error': 'Missing required input'})
        result, _ = audit.audit_archive(body, 1, hashlib.sha256(body).hexdigest())
        self.assertEqual(result['integrity'], 'PASSED')
        self.assertEqual(result['acquisition_status'], 'PARTIAL_ERROR')
        self.assertEqual(len(result['acquisition_errors']), 1)
        self.assertFalse(result['exact_agent_replay_ready'])

    def test_wrong_archive_hash_fails_before_any_data_is_trusted(self):
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            audit.audit_archive(archive(), 1, '0' * 64)

    def test_path_traversal_is_rejected(self):
        body = archive({'../untrusted.txt': 'never extracted'})
        with self.assertRaisesRegex(ValueError, 'Unsafe archive'):
            audit.audit_archive(body, 1, hashlib.sha256(body).hexdigest())

    def test_generated_workflow_is_standalone_and_has_no_market_secret(self):
        source = build()
        self.assertEqual(source, (ROOT / '.github/workflows/algopack-research-audit-inputs.yml').read_text())
        self.assertNotIn('MOEX_ALGOPACK_TOKEN', source)
        self.assertIn('actions: read', source)
        self.assertIn('NoAuthRedirect', source)

    def test_unicode_reports_roundtrip_below_github_annotation_limit(self):
        value = {'type': 'TEST', 'text': 'Исторические данные и ограничения ' * 500}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            audit.emit_record(value, 'large-unicode-report')
        lines = output.getvalue().splitlines()
        self.assertTrue(all(len(line.encode()) < 3000 for line in lines))
        parts = [json.loads(line.removeprefix('::notice::')) for line in lines]
        self.assertEqual([p['index'] for p in parts], list(range(len(parts))))
        decoded = json.loads(base64.b64decode(''.join(p['base64'] for p in parts)))
        self.assertEqual(decoded, value)

    def test_raw_recovery_keeps_negative_spread_and_quarantines_only_bad_row(self):
        columns = ['secid', 'asset_code', 'tradedate', 'tradetime', 'mid_price', 'spread_l1']
        rows = [['BRG5', 'BR', '2025-01-31', '10:00:00', 100, -0.2],
                ['BRG5', 'BR', '2025-01-31', '10:05:00', 100, 0.2]]
        out = io.BytesIO()
        requests = []
        with zipfile.ZipFile(out, 'w') as z:
            for offset, values in [(0, rows), (2, [])]:
                file = str(offset) + '.json.gz'
                z.writestr(file, gzip.compress(json.dumps({'data': {'columns': columns, 'data': values}}).encode()))
                requests.append({'url': 'https://apim.moex.com/iss/datashop/algopack/fo/obstats/BRG5.json?from=2025-01-31&till=2025-01-31&start=' + str(offset), 'status': 'RECEIVED', 'file': file})
        record = {'metric': 'obstats', 'contract': 'BRG5', 'asset': 'BR', 'first': '2025-01-31', 'end_exclusive': '2025-02-01'}
        with zipfile.ZipFile(io.BytesIO(out.getvalue())) as z:
            raw, bad, info = audit.rebuild_panel_from_pages(z, requests, record)
            self.assertEqual(raw[0]['spread_l1'], -0.2)
            self.assertEqual(len(bad), 1)
            self.assertTrue(info['complete_page_sequence'])
            self.assertFalse(info['historical_executable_BBO'])
            _, _, incomplete = audit.rebuild_panel_from_pages(z, requests[:1], record)
            self.assertFalse(incomplete['complete_page_sequence'])
            self.assertEqual(incomplete['next_missing_offset'], 2)


if __name__ == '__main__':
    unittest.main()
