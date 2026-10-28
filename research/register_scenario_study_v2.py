"""Preregister six scenario passports and a finite execution design before scoring."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from freeze_six_strategy_study import add_trial, canonical

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'research/scenario-study-v2'


def build(now=None):
    now = now or datetime.now(timezone.utc).isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'protocol.json').exists():
        raise ValueError('Already registered; use a new version instead of rewriting')
    original = json.loads((ROOT / 'research/six-strategy-v1/study-protocol.json').read_text())
    source_review = json.loads((ROOT / 'research/six-strategy-v1/hypothesis-source-review-v2.json').read_text())
    candidates = original['candidates']
    for c in candidates:
        c['version'] = 'scenario-v2'
        c['implementation'] = 'PREREGISTERED_NOT_SCORED'
        c['assets'] = ['BR', 'GOLD']
        c['decision_interval'] = 'Closed H1; observed M1 execution, completed H4 where needed'
        c['risk'] = 'Common price-book1R planned loss per accepted trade, at most one position per asset/twoR total; not a capital return'
        c['entry_cancel'] = ['Missing/nonfinite required inputs', 'expiry<9 calendar days', 'No positive-volume M1 within5 minutes after requested entry', 'Adverse drift>.5ATR from signal close', 'Entry outside stop/target', 'Entry after main-session expiry for S2', 'Median prior20 H1 volume implies less than one whole contract at1% participation']
        c['reference_stop'] = 'Signal close -direction*1.5ATR14, rounded to explicitly assumed native tick'
        c['reference_target'] = '2R from reference, except S1 frozen prior20 mean'
        c['maximum_hold'] = 'S0 five calendar days; S1 twentyfour hours; S2 session end; S3/S5 sixty minutes after actual entry; S4 next eligible daily decision; roll and fold end override'
        c['trading_hours'] = 'Common entries: weekdays10:00<=entry<19:00MSK, excluding API calendar off-days; source observed bars and protective exits may be outside these hours'
        c['overnight'] = 'Allowed only for S0/S1/S4; no fabricated fill through an unobserved interval'
        c['roll'] = 'Frozen original contract windows, expiry>=9days; exit at expiry-8days; never stitch prices across contract codes'
        c['parameter_search_before_baseline_comparison'] = 0
        if c['id'] != 'S0':
            c['primary_source'] = next(s.get('primary') for s in source_review['families'] if s['id'] == c['id'])
    s5 = next(c for c in candidates if c['id'] == 'S5')
    s5.update(name='Chaikin Money Flow H1', mechanism='price_location_volume_accumulation',
              inputs=['H1 OHLCV'],
              entry='CMF20=sum(((2close-high-low)/(high-low))*volume)/sum(volume). Zero-range bar contributes zero. LONG on crossing above+.05, SHORT on crossing below-.05; current H1 volume>=prior20 median',
              exit='1.5ATR reference stop,2R reference target, or first observed eligible M1 open60minutes after actual entry',
              replacement='Supersedes unimplemented Q proxy before performance testing; prior record preserved, not silently erased',
              primary_source='https://help.chaikinanalytics.com/knowledge-center/chaikin-money-flow',
              interpretation='Price-volume proxy; not actual aggressor flow or executable order book')
    scenarios = []
    for delay in [18, 20, 25, 30, 40, 60]:
        for reaction in [0, 30, 60, 120]:
            scenarios.append({'id': f'd{delay}-r{reaction}-s2-l2-f1', 'delay_minutes': delay, 'reaction_seconds': reaction, 'spread_ticks': 2, 'slippage_ticks': 2, 'fee_multiplier': 1, 'track': 'agent' if reaction == 0 else 'human'})
    for delay in [1, 5]:
        scenarios.append({'id': f'counterfactual-d{delay}', 'delay_minutes': delay, 'reaction_seconds': 0, 'spread_ticks': 2, 'slippage_ticks': 2, 'fee_multiplier': 1, 'track': 'COUNTERFACTUAL_SOURCE_AVAILABILITY_UNCONFIRMED'})
    for reaction in [0, 60]:
        for spread in [1, 5]:
            scenarios.append({'id': f'd18-r{reaction}-s{spread}-l2-f1', 'delay_minutes': 18, 'reaction_seconds': reaction, 'spread_ticks': spread, 'slippage_ticks': 2, 'fee_multiplier': 1, 'track': 'cost_sensitivity'})
        scenarios.append({'id': f'stress-r{reaction}', 'delay_minutes': 18, 'reaction_seconds': reaction, 'spread_ticks': 5, 'slippage_ticks': 5, 'fee_multiplier': 2, 'track': 'joint_cost_stress'})
    protocol = {'id': 'BR_GOLD_SCENARIO_STUDY_V2', 'registered_at_utc': now, 'user_approved_disclosed_scenarios': True,
        'approved_scope': 'Six families; monetary performance only where required inputs are confirmed; exact S0 remains BLOCKED_DATA',
        'historical_data': {'source': 'apim.moex.com verified archives only', 'first': '2023-01-01', 'end_exclusive': '2026-10-26', 'contract_scope': 'Original63 frozen nearest-expiry windows; not exact point-in-time maxVOLTODAY', 'yearly_zip_sha256': {'2023':'fdbd8f024c12ab110c0d9f15a1aabd107039bde066b0410054a9b7939abf578c','2024':'5a99d6eee7f98d7e76a1dffe86dcfeea4b523be14c587b22f8c3000905d4c04c','2025':'d8eff7373c9bcaafb570fbd24a44d84709353112396f90c62bd299e86b003253','2026':'b8344fbe990dd1ae069ceef3ae0709ca12375a7205a26f1360e3ba34b66b1ffb'}},
        'candidates': candidates, 'scenario_design': scenarios,
        'primary_agent': 'd18-r0-s2-l2-f1', 'primary_human': 'd18-r60-s2-l2-f1',
        'execution': {'quote_proxy': 'Observed trade-price M1 open treated as a hypothetical quote midpoint; no historical BBO claim', 'entry': 'Ceil(signalH1_end +assumed source/processing delay +human reaction) to next whole minute; first positive-volume M1 within5minutes', 'signal_max_age_minutes': 78, 'spread': 'Symmetric assumed spread, adverse tick rounding; spread<=5ticks', 'reference_ticks': {'BR':.01,'GOLD':.1}, 'tick_scope': 'Explicit price-scenario constants, not a confirmed historical economics series', 'broker_fraction_per_side': .00015, 'exchange_fraction_per_side': .000132, 'fee_scope': 'Fixed disclosed present-tariff sensitivity; not reconstructed historical debits', 'stop': 'Executable-side proxy crossing; adverse gap open and slippage; stop wins if both stop/target touched in a full entry-or-later minute', 'target': 'No favorable fill improvement beyond target', 'timed_exit': 'First positive-volume M1 at/after prescribed time; missing coverage leaves UNRESOLVED, not an invented boundary fill', 'M1_availability': 'OHLC stop simulation with modeled resting protection; observed notification time would follow source/runner delay and is not an actual broker fill', 'no_subminute_precision': 'Entry uses next whole-minute open; stop/target hit time known only to a minute', 'source_publication': 'SYSTIME is never used as historical first-publication time', 'end_of_fold': 'No entries after evaluation end; forced exits at first observed minute at boundary, predeclared and shown separately'},
        'chronology': {'initial_training': ['2023-01-01','2025-01-01'], 'evaluation': 'Eight successive calendar quarters2025Q1..2026Q4(partial)', 'purge': 'Entry windows start5calendar days after quarter start and end5days before quarter/study end; open positions close at the evaluation boundary; no position crosses folds', 'S3_fit': 'Expanding past only; label starts at delayed actual scenario entry and ends60minutes later; label end<=quarter_start-5days; >=60 observations per asset/MSKhour', 'S4_daily': '20 immediately prior assumed weekday trading dates, all nine main-session H1(10..18) observed in the same contract. Daily log(close18/open10). Missing date/bar blocks score; do not skip unknown dates to create a full history', 'history_status': 'All2023-2026 already inspected; retrospective only. No untouched holdout claimed'},
        'comparison': {'primary_units': 'NetR per trade and fixed1R/twoR-cap price-book; no RUB or capital percentage without economic gate', 'one_position_per_asset': True, 'S4_max_positions': 1, 'same_time_priority': 'Exit first; then BR before GOLD; one attempt per strategy/contract/H1UID', 'monetary_gate': ['Historical effective contract tick/multiplier/fee inputs confirmed', 'Observed positive contemporaneous collateral with known semantics', 'Historical FX only; integer contracts;100000RUB capital;50% aggregate margin cap;2% aggregate stop-risk cap'], 'monetary_risks': [.005,.01,.02], 'unknown_economics': 'BLOCKED_DATA, never zero return or present-day margin proxy', 'monetary_risk_interpretation': 'Sensitivity only, not an approved user drawdown limit', 'max_drawdown_user_approved': None},
        'selection': {'minimum_evaluation_trades': 50, 'require': ['Positive primary netR in both2025 and2026', 'Positive netR under joint spread5/slip5/feesx2 stress', 'Positive primary-human netR and no sign reversal at delay40minutes+reaction60seconds', 'Conditional20-observed-session-day block bootstrap90% lower meanR bound>0'], 'rank': 'Worst meanR across the predeclared primary/human/stress/delay40 cases, then smaller price-book max drawdownR', 'promotion': 'Price-logic candidate only until monetary gate is satisfied; no live switch or new pilot on blocked economics', 'failure': 'No candidate or insufficient evidence is allowed'},
        'optimization': {'before_selection': 'NONE', 'after_selection': 'Only one candidate; preregister at most9 parameter combinations, train-only choice; replay already seen evaluation as retrospective sensitivity, never independent validation', 'stability': 'Reject isolated optimum; examine neighboring settings, fees and years', 'no_candidate': 'Do not optimize or add families'},
        'accounting': {'legacy_trial_count': 'UNKNOWN', 'families': 6, 'execution_scenarios': len(scenarios), 'new_candidate_score_cells': 6*len(scenarios), 'S3_fits': 'Each execution-clock/fold fit recorded with exact training cutoff and input hash', 'old_Q_spec': 'Retained as unimplemented abandoned specification, not hidden from search history', 'global_search_significance': 'No valid global multiplicity-adjusted claim with unknown old search'},
        'unchanged': {'signal_blob':'38e147112ad48c3959b4dff6838fbec88d66f576','financial_engine_sha256':'ce4b80188eefa457179bb2b5129e6fbc762d701bd6deea6585467e2ac518f932','old_pilot_id':'1d88347c8df1a0db74d49667','telegram_and_shared_lock':True,'new_pilot':'NOT_STARTED','real_orders':False}}
    raw = canonical(protocol)
    (OUT / 'protocol.json').write_bytes(raw + b'\n')
    ledger = ROOT / 'research/six-strategy-v1/trial-ledger.jsonl'
    for c in candidates:
        (OUT / (c['id'] + '-passport.json')).write_bytes(canonical(c) + b'\n')
        add_trial(ledger, {'trial_id': c['id'] + '-scenario-spec-v2', 'strategy_id': c['id'], 'registered_at_utc': now, 'status': 'PREREGISTERED_NOT_SCORED', 'selection_on_final_test':False,'results_inspected':False,'definition_sha256':hashlib.sha256(canonical(c)).hexdigest(),'protocol_sha256':hashlib.sha256(raw).hexdigest()})
    add_trial(ledger, {'trial_id':'six-scenarios-v2-execution-design','registered_at_utc':now,'status':'PREREGISTERED_NOT_SCORED','selection_on_final_test':False,'results_inspected':False,'protocol_sha256':hashlib.sha256(raw).hexdigest(),'scenario_cells':6*len(scenarios),'prior_trials_total':'UNKNOWN'})
    print(json.dumps({'protocol_sha256':hashlib.sha256(raw).hexdigest(),'scenario_cells':6*len(scenarios),'out':str(OUT)}))


if __name__ == '__main__': build()
