import unittest
from urllib.parse import parse_qs, urlsplit
import repair_missing_inputs as repair
from build_missing_input_repair_workflow import build, ROOT


RECORD = {'metric': 'tradestats', 'contract': 'BRG4', 'asset': 'BR', 'first': '2023-12-21', 'end_exclusive': '2024-01-01'}


def row(time='10:00:00', **changes):
    return {'secid': 'BRG4', 'asset_code': 'BR', 'tradedate': '2023-12-21', 'tradetime': time, 'vol': 1, 'im': None} | changes


def doc(rows, index, total):
    columns = list(rows[0]) if rows else ['secid', 'asset_code', 'tradedate', 'tradetime', 'vol', 'im']
    return {'data': {'columns': columns, 'data': [[r[k] for k in columns] for r in rows]},
            'data.cursor': {'columns': ['INDEX', 'TOTAL', 'PAGESIZE'], 'data': [[index, total, 1000]]}}


class Client:
    def __init__(self, replies): self.replies, self.offsets = replies, []
    def request(self, path, query):
        self.offsets.append(query['start'])
        return self.replies[len(self.offsets) - 1]


class RepairTests(unittest.TestCase):
    def test_prefix_is_reused_after_overlap_verification_and_only_missing_rows_append(self):
        rows = [row()]
        client = Client([doc(rows, 0, 2), doc([row('10:05:00')], 1, 2), doc([], 0, 2)])
        self.assertTrue(repair.append_pages(client, RECORD, rows))
        self.assertEqual(client.offsets, [0, 1, 2])
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]['im'])

    def test_changed_boundary_is_not_merged(self):
        rows = [row()]
        with self.assertRaisesRegex(ValueError, 'boundary changed'):
            repair.append_pages(Client([doc([row(vol=2)], 0, 2)]), RECORD, rows)
        self.assertEqual(len(rows), 1)

    def test_out_of_range_or_wrong_contract_and_ordering_fail(self):
        cases = [[row(secid='GDH4')], [row(tradedate='2024-01-01')], [row('10:05:00'), row('10:00:00')]]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                repair.append_pages(Client([doc(values, 0, len(values))]), RECORD, [])

    def test_incomplete_empty_page_is_not_success(self):
        with self.assertRaisesRegex(ValueError, 'before declared total'):
            repair.append_pages(Client([doc([], 0, 5)]), RECORD, [])

    def test_repeated_page_cannot_create_duplicate_rows(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            repair.append_pages(Client([doc([row()], 0, 2), doc([row()], 1, 2)]), RECORD, [])

    def test_standalone_workflow_is_exact_and_has_bounded_read_only_permissions(self):
        content = build()
        self.assertEqual(content, (ROOT / '.github/workflows/algopack-research-repair-inputs.yml').read_text())
        self.assertIn('actions: read', content)
        self.assertNotIn('contents: write', content)
        self.assertNotIn('TELEGRAM_BOT_TOKEN:', content)


if __name__ == '__main__':
    unittest.main()
