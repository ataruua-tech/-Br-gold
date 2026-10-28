import copy
import unittest
from types import SimpleNamespace
import numpy as np
import pandas as pd

from scenario_features_v2 import ContractData, frozen_signal_definitions, prepare_features, merge_price_rows, minute
from scenario_engine_v2 import execution_trade, price_book, scenario_clock, monetary_gate, delayed_label, fit_hour_model, fold_windows

SCENARIO={'id':'test','delay_minutes':18,'reaction_seconds':0,'spread_ticks':2,'slippage_ticks':2,'fee_multiplier':1}


def fixture(direction=1):
    begin=minute('2025-01-10T10:00')
    times=np.arange(begin+78,begin+200)
    bars=np.tile([100.,100.5,99.5,100.,10.],(len(times),1))
    meta={'secid':'BRG5','asset':'BR','active_start':'2025-01-01','active_end':'2025-02-01','expires':'2025-02-10'}
    setup={'strategy':'S0','fold':'test','signal_begin':begin,'signal_end':begin+60,'reference':100.,'atr':2.,'median_volume':1000.,'direction':direction,'target_override':None,'next_decision_end':None}
    fold={'id':'test','first':begin,'end':begin+190}
    return ContractData(meta,pd.DataFrame(),bars,times),setup,fold


class ScenarioTests(unittest.TestCase):
    def test_frozen_indicator_values_and_future_h4_do_not_leak(self):
        frozen=frozen_signal_definitions()
        ix=pd.date_range('2024-01-01',periods=450,freq='h')
        x=100+np.sin(np.arange(450)/13)+np.arange(450)/100
        frame=pd.DataFrame({'open':x-.1,'high':x+.5,'low':x-.5,'close':x,'volume':100.},index=ix)
        result=prepare_features(frame,frozen,{})
        i=350
        bars=[frozen.Candle(t.to_pydatetime(),*r) for t,r in zip(frame.iloc[:i+1].index,frame.iloc[:i+1].to_numpy())]
        exact=frozen.indicator_snapshot(bars)
        for name,key in [('ema20','ema20'),('ema50','ema50'),('atr','atr14'),('adx','adx14'),('h4_ema20','h4_ema20'),('h4_ema50','h4_ema50')]:
            self.assertAlmostEqual(result.iloc[i][name],exact[key],places=10)
        changed=frame.copy();changed.iloc[i+1:,:4]+=10000
        later=prepare_features(changed,frozen,{})
        for name in ['ema20','ema50','atr','adx','h4_ema20','h4_ema50','cmf','relative_score']:
            self.assertTrue(np.allclose(result.iloc[:i+1][name],later.iloc[:i+1][name],equal_nan=True),name)

    def test_conflicting_source_snapshot_not_merged(self):
        a={'begin':'2025-01-01 10:00:00','open':100,'high':102,'low':99,'close':101,'volume':10}
        with self.assertRaisesRegex(ValueError,'snapshots conflict'):
            merge_price_rows([[a],[a|{'close':100}]])

    def test_entry_follows_delay_and_rounds_human_reaction_forward(self):
        d,s,f=fixture()
        trade,error=execution_trade(d,s,SCENARIO,f)
        self.assertIsNone(error)
        self.assertEqual(trade['entry_time'],s['signal_end']+18)
        self.assertNotEqual(trade['entry'],s['reference'])
        self.assertEqual(scenario_clock(s['signal_end'],SCENARIO|{'reaction_seconds':30}),s['signal_end']+19)

    def test_both_levels_in_same_full_minute_choose_stop_for_both_directions(self):
        for direction in (1,-1):
            d,s,f=fixture(direction);d.m1[0,1]=108;d.m1[0,2]=92
            trade,error=execution_trade(d,s,SCENARIO,f)
            self.assertIsNone(error)
            self.assertEqual(trade['reason'],'STOP')
            self.assertTrue(trade['both_levels_same_minute'])
            self.assertLess(trade['net_R'],-.99)

    def test_adverse_gap_is_not_filled_at_stop(self):
        d,s,f=fixture();d.m1[1,:4]=[95,96,94,95]
        trade,error=execution_trade(d,s,SCENARIO,f)
        self.assertIsNone(error)
        self.assertLess(trade['exit'],95)
        self.assertLess(trade['net_R'],-1)

    def test_zero_volume_bar_cannot_execute_a_fake_stop(self):
        d,s,f=fixture();d.m1[1]=[100,108,92,100,0]
        trade,error=execution_trade(d,s,SCENARIO,f)
        self.assertIsNone(error)
        self.assertEqual(trade['reason'],'FOLD_END')

    def test_missing_entry_and_exit_are_errors_not_zero_profit(self):
        d,s,f=fixture();d.m1_times+=20
        trade,error=execution_trade(d,s,SCENARIO,f)
        self.assertIsNone(trade);self.assertEqual(error,'ENTRY_M1_UNAVAILABLE')
        d,s,f=fixture();d.m1=d.m1[:1];d.m1_times=d.m1_times[:1]
        trade,error=execution_trade(d,s,SCENARIO,f)
        self.assertIsNone(trade);self.assertEqual(error,'UNRESOLVED_EXIT_COVERAGE')

    def test_orb_correction_exits_before_clearing_and_cannot_hold_overnight(self):
        d,s,f=fixture();s['strategy']='S2'
        s['session_exit_deadline']=d.m1_times[0]+10
        trade,error=execution_trade(d,s,SCENARIO,f)
        self.assertIsNone(error)
        self.assertEqual(trade['exit_time'],s['session_exit_deadline'])
        d.m1=d.m1[:2];d.m1_times=d.m1_times[:2];d.m1_times[1]+=1440
        trade,error=execution_trade(d,s,SCENARIO,f)
        self.assertIsNone(trade)
        self.assertEqual(error,'UNRESOLVED_INTRADAY_SESSION_EXIT')

    def test_orb_entry_after_predeclared_close_is_cancelled(self):
        d,s,f=fixture();s['strategy']='S2'
        s['session_exit_deadline']=d.m1_times[0]
        trade,error=execution_trade(d,s,SCENARIO,f)
        self.assertIsNone(trade)
        self.assertEqual(error,'SIGNAL_ALREADY_CANCELLED_AT_DEADLINE')

    def test_training_label_clock_and_cutoff(self):
        d,s,f=fixture();row=SimpleNamespace(t=s['signal_begin'])
        d.m1[60,0]=101
        self.assertIsNone(delayed_label(d,row,SCENARIO,s['signal_end']+77))
        value,end=delayed_label(d,row,SCENARIO,s['signal_end']+78)
        self.assertAlmostEqual(value,.01);self.assertEqual(end,s['signal_end']+78)

    def test_later_prices_do_not_change_a_past_training_fit(self):
        dates=pd.date_range('2024-08-01','2024-12-31',freq='B')
        begins=np.array([minute(str(t.date())+'T10:00') for t in dates])
        times=np.sort(np.r_[begins+78,begins+138])
        bars=np.tile([100.,101,99,100,10.],(len(times),1));bars[1::2,0]=100.1
        meta={'secid':'BRF5','asset':'BR','active_start':'2024-01-01','active_end':'2025-01-05','expires':'2025-02-01'}
        d=ContractData(meta,pd.DataFrame({'t':begins,'ready':True}),bars,times)
        fold={'id':'2025Q1','train_end':minute('2024-12-27T00:00')}
        model,audit=fit_hour_model([d],SCENARIO,fold,{})
        self.assertIn(('BR',11),model)
        d.m1[d.m1_times>fold['train_end'],0]=1000
        other,again=fit_hour_model([d],SCENARIO,fold,{})
        self.assertEqual(model,other);self.assertEqual(audit['input_sha256'],again['input_sha256'])
        self.assertLessEqual(audit['train_label_end_max'],fold['train_end'])

    def test_book_reconciles_marks_and_rejects_uid_duplicates(self):
        d,s,f=fixture();d.m1[5,1]=108
        trade,error=execution_trade(d,s,SCENARIO,f)
        result,accepted,curve=price_book([trade,copy.deepcopy(trade)],f['first'],f['end'],'S0')
        self.assertEqual(len(accepted),1);self.assertEqual(result['admission_skips']['duplicate_UID'],1)
        self.assertAlmostEqual(result['net_R'],trade['net_R'])
        self.assertGreaterEqual(result['max_drawdown_R'],0)
        self.assertNotIn('return_pct',result)

    def test_unknown_margin_or_economics_does_not_become_a_monetary_return(self):
        result=monetary_gate({'collateral_at_entry_confirmed':True})
        self.assertEqual(result['status'],'BLOCKED_DATA')
        self.assertIsNone(result['net_return_pct']);self.assertIsNone(result['net_pnl_rub'])

    def test_folds_leave_five_days_on_both_boundaries(self):
        folds=fold_windows()
        self.assertEqual(len(folds),8)
        for f in folds:self.assertEqual(f['first']-f['quarter_start'],5*1440)
        for a,b in zip(folds,folds[1:]):self.assertGreaterEqual(b['first']-a['end'],10*1440)


if __name__=='__main__':unittest.main()
