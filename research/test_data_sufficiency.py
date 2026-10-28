import unittest
import audit_data_sufficiency as audit
from build_sufficiency_workflow import build


def row(time='10:00:00', **kwargs):
    return {'secid': 'GDH6', 'tradedate': '2026-01-01', 'tradetime': time, 'vol': 1, 'im': None} | kwargs


class SufficiencyTests(unittest.TestCase):
    def test_unknown_collateral_is_not_zero_or_positive_and_months_are_separate(self):
        rows = [row(), row('10:05:00', im=100), row('10:10:00', im=-1), row('10:15:00', im=0, tradedate='2026-02-01')]
        result = audit.observation_quality(rows, 'tradestats')
        self.assertEqual(result['counts']['null_margin'], 1)
        self.assertEqual(result['counts']['positive_margin'], 1)
        self.assertEqual(result['counts']['invalid_margin'], 2)
        self.assertEqual(result['margin_by_month']['2026-02']['invalid_margin'], 1)
        self.assertIsNone(rows[0]['im'])

    def test_accepted_obstats_null_and_negative_values_are_also_counted(self):
        rows = [row(mid_price=None, spread_l1=None), row('10:05:00', mid_price=1, spread_l1=-0.1), row('10:10:00', mid_price=2, spread_l1=0)]
        result = audit.obstats_file_quality(rows)
        self.assertEqual(result['counts']['invalid_mid'], 1)
        self.assertEqual(result['counts']['invalid_spread'], 2)
        self.assertEqual(rows[1]['spread_l1'], -0.1)

    def test_duplicate_and_out_of_order_panel_cannot_pass(self):
        for values in [[row(), row()], [row('10:05:00'), row()]]:
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, 'chronology'):
                audit.obstats_file_quality(values)

    def test_later_recalculation_time_is_counted_and_not_called_publication(self):
        result = audit.observation_quality([row(SYSTIME='2026-06-01 09:00:00')], 'tradestats')
        self.assertEqual(result['counts']['systime_after_trade_date'], 1)

    def test_audit_cannot_make_market_requests_or_send_messages(self):
        content = build(123, 'a' * 64)
        self.assertNotIn('MOEX_ALGOPACK_TOKEN:', content)
        self.assertNotIn('TELEGRAM_BOT_TOKEN:', content)
        self.assertNotIn('contents: write', content)
        self.assertIn("SUFFICIENCY_REFERENCES['resume'] = (123,", content)
        with self.assertRaises(ValueError): build(123, 'bad')


if __name__ == '__main__': unittest.main()
