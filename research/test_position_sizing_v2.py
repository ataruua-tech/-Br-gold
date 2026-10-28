import unittest
from verify_position_sizing_v2 import position_quantity


class PositionSizingTests(unittest.TestCase):
    def values(self,**changes):
        return dict(equity_rub=100000,stop_risk_one_rub=350,margin_one_rub=12000,median_volume=10000,risk_fraction=.01,economics_confirmed=True)|changes

    def test_integer_contracts_respect_risk_and_margin(self):
        r=position_quantity(**self.values())
        self.assertEqual(r['quantity'],2)
        self.assertLessEqual(r['risk_rub'],1000)
        self.assertLessEqual(r['margin_rub'],50000)

    def test_existing_positions_reduce_both_combined_budgets(self):
        r=position_quantity(**self.values(used_margin_rub=41000,used_stop_risk_rub=1800))
        self.assertEqual(r['quantity'],0)
        self.assertEqual(r['status'],'SKIPPED_CAPITAL_OR_VOLUME')

    def test_liquidity_does_not_force_a_minimum_one_contract(self):
        r=position_quantity(**self.values(median_volume=99))
        self.assertEqual(r['quantity'],0)

    def test_unknown_economics_and_null_margin_do_not_become_zero_return(self):
        r=position_quantity(**self.values(economics_confirmed=False))
        self.assertIsNone(r['quantity']);self.assertEqual(r['status'],'BLOCKED_ECONOMICS')
        r=position_quantity(**self.values(margin_one_rub=None))
        self.assertIsNone(r['quantity']);self.assertEqual(r['status'],'DATA_ERROR')


if __name__=='__main__':unittest.main()
