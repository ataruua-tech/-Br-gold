import contextlib
import gzip
import hashlib
import io
import json
import os
import tempfile
import textwrap
import types
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import resume_full_universe as resume
from build_universe_resume_workflow import build, ROOT


def doc(rows, offset=0, total=None):
    return {'data': {'columns': ['x'], 'data': [[v] for v in rows]},
            'data.cursor': {'columns': ['INDEX', 'TOTAL'], 'data': [[offset, total if total is not None else offset + len(rows)]]}}


class Client:
    def __init__(self, replies): self.replies, self.offsets = replies, []
    def request(self, path, query):
        self.offsets.append(query['start'])
        return self.replies[len(self.offsets) - 1]


class ResumeTests(unittest.TestCase):
    def test_verified_empty_cached_scope_is_not_a_missing_cache(self):
        empty = b'[]'
        file = {'file': 'empty.gz', 'uncompressed_sha256': hashlib.sha256(empty).hexdigest(), 'rows': 0}
        manifest = {'records': [{'contract': 'BRZ6', 'metric': 'tradestats', 'status': 'SOURCE_EMPTY', 'first': '2026-10-25', 'end_exclusive': '2026-10-26', 'file': file}]}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as z:
            z.writestr('manifest.json', json.dumps(manifest))
            z.writestr('empty.gz', gzip.compress(empty))
        with zipfile.ZipFile(buffer) as z:
            values = resume.old_cached_values({'panels2026': z}, {'contract': 'BRZ6', 'metric': 'tradestats', 'cached_query_spans_reused': [['2026-10-25', '2026-10-26']]})
            self.assertEqual(values, [])

    def test_complete_retained_query_needs_no_market_request(self):
        c = Client([])
        self.assertEqual(resume.resume_pages(c, '/iss/x', 'data', {}, [{'x': 1}], True, None), [{'x': 1}])
        self.assertEqual(c.offsets, [])

    def test_partial_prefix_boundary_verified_then_only_missing_offset(self):
        c = Client([doc([1, 2], 0, 3), doc([3], 2, 3), doc([], 3, 3)])
        rows = [{'x': 1}, {'x': 2}]
        self.assertEqual(len(resume.resume_pages(c, '/iss/x', 'data', {}, rows, False, (0, rows.copy()))), 3)
        self.assertEqual(c.offsets, [0, 2, 3])

    def test_changed_prefix_and_incomplete_empty_page_rejected(self):
        with self.assertRaisesRegex(ValueError, 'boundary changed'):
            resume.resume_pages(Client([doc([2])]), '/iss/x', 'data', {}, [{'x': 1}], False, (0, [{'x': 1}]))
        with self.assertRaisesRegex(ValueError, 'before advertised'):
            resume.resume_pages(Client([doc([], 0, 5)]), '/iss/x', 'data', {}, [], False, None)

    def test_raw_prefix_hash_and_exact_query_identity(self):
        raw = json.dumps(doc([1, 2])).encode()
        transcript = [{'url': 'https://apim.moex.com/iss/x?from=2026-01-01&till=2026-01-02&start=0', 'status': 'RECEIVED', 'file': 'a.gz', 'response_sha256': hashlib.sha256(raw).hexdigest()}]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as z: z.writestr('a.gz', gzip.compress(raw))
        with zipfile.ZipFile(buffer) as z:
            rows, complete, boundary = resume.query_prefix(z, transcript, '/iss/x', 'data', {'from': '2026-01-01', 'till': '2026-01-02'})
            self.assertEqual(rows, [{'x': 1}, {'x': 2}])
            self.assertFalse(complete)
            self.assertEqual(boundary[0], 0)
            self.assertEqual(resume.query_prefix(z, transcript, '/iss/x', 'data', {'from': '2026-01-03'})[0], [])

    def test_standalone_no_secret_no_hidden_import_no_live_side_effects(self):
        content = build()
        self.assertEqual(content, (ROOT / '.github/workflows/algopack-research-resume-sixteen.yml').read_text())
        self.assertNotIn('TELEGRAM_BOT_TOKEN:', content)
        self.assertNotIn('contents: write', content)
        body = textwrap.dedent(content.split("python3 - <<'PY'\n", 1)[1].split('\n          PY\n', 1)[0]).rsplit('sys.exit(resume_main())', 1)[0]
        m = types.ModuleType('standalone_resume_test')
        with patch.dict(sys.modules, {m.__name__: m}), tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'MOEX_ALGOPACK_TOKEN': ''}), contextlib.redirect_stdout(io.StringIO()):
            exec(compile(body, '<resume-test>', 'exec'), m.__dict__)
            self.assertEqual(m.resume_main(Path(d)), 1)
            result = json.loads(Path(d, 'manifest.json').read_text())
            self.assertEqual(result['requests'], 0)
            self.assertFalse(result['strategy_testing_performed'])


if __name__ == '__main__': unittest.main()
