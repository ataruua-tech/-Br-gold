"""Freeze research definitions without changing a live workflow or paper book."""
from __future__ import annotations
import csv
import ast
import hashlib
import json
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

OUT=ROOT/'research/six-strategy-v1'


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')).encode()


def file_sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def frozen_references():
    """A repository clone can inspect/regenerate specs without hidden modules.

    Absent old research datasets remain hash references, never verified inputs.
    Financial constants are parsed as literals from the deployed standalone file.
    """
    workflow=(ROOT/'.github/workflows/algopack-control.yml').read_text()
    source=textwrap.dedent(workflow.split("python3 - <<'PY'\n",1)[1].split('\n          PY\n',1)[0])
    constants={}
    for node in ast.parse(source).body:
        if isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in ('PAPER_CONFIG','CONTROL_WEEK_ENGINE_SHA256'):
                    constants[target.id]=ast.literal_eval(node.value)
    old_passport=ROOT/'research/six-strategy-v1/baseline-passport.json'
    old_protocol=ROOT/'research/six-strategy-v1/study-protocol.json'
    p=json.loads(old_passport.read_text()) if old_passport.exists() else {}
    s=json.loads(old_protocol.read_text()) if old_protocol.exists() else {}
    references=s.get('search_accounting',{}).get('legacy_evidence_sha256',{})
    def source_hash(name):
        if (ROOT/name).exists():return file_sha(ROOT/name)
        value=p.get('financial_source_sha256',{}).get(name) or references.get(name)
        if not value:raise ValueError('Missing declared source reference: '+name)
        return value
    if (ROOT/'paper_week.py').exists() and (ROOT/'paper_engine.py').exists():
        engine=hashlib.sha256((ROOT/'paper_week.py').read_bytes()+(ROOT/'paper_engine.py').read_bytes()).hexdigest()
        if engine!=constants['CONTROL_WEEK_ENGINE_SHA256']:raise ValueError('Financial source and deployed workflow disagree')
    else:engine=constants['CONTROL_WEEK_ENGINE_SHA256']
    return constants['PAPER_CONFIG'],engine,source_hash


def validation_result_can_be_ranked(row):
    """A missing result is neither zero profit nor a losing strategy."""
    return (row.get('status')=='COMPLETED' and row.get('common_data_verified') is True
            and row.get('execution_scope') in ('EXACT_OBSERVED_REPLAY','EXPLICIT_APPROXIMATE_SCENARIO')
            and row.get('trial_registered') is True and row.get('selection_on_final_test') is False)


def add_trial(path,trial):
    path=Path(path)
    rows=[json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []
    previous='0'*64
    for row in rows:
        digest=row['record_sha256'];body={k:v for k,v in row.items() if k!='record_sha256'}
        if body.get('previous_record_sha256')!=previous or hashlib.sha256(canonical(body)).hexdigest()!=digest:
            raise ValueError('Trial ledger integrity failure')
        previous=digest
    if any(r['trial_id']==trial['trial_id'] for r in rows):
        raise ValueError('Trial identifier already registered')
    if trial.get('selection_on_final_test') is not False:
        raise ValueError('Final evaluation data must not select a configuration')
    row={**trial,'previous_record_sha256':previous}
    row['record_sha256']=hashlib.sha256(canonical(row)).hexdigest()
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('ab') as f:f.write(canonical(row)+b'\n')
    return row


def write(path,value):Path(path).write_bytes(canonical(value)+b'\n')


def build(now):
    OUT.mkdir(parents=True,exist_ok=True)
    config,engine,source_hash=frozen_references()
    # Existing studies are evidence of prior inspection, not a fresh holdout.
    legacy={name:source_hash(name) for name in ('research/protocol.json','research/results.json',
            'research/tariff/protocol-sber.json','research/agent-audit-v081/results.json')}
    passport={'id':'S0','name':'Donchian H1 with H1/H4 EMA alignment',
        'frozen_at_utc':now.isoformat(),'source_repository':'ataruua-tech/-Br-gold',
        'deployed_commit':'dc5c9ed72c2b34577c5724c18574322df6912e4d',
        'signal_workflow_blob':'38e147112ad48c3959b4dff6838fbec88d66f576',
        'controller_workflow_blob':'dd216ba8ac19e9e52fa9c2b6a5b340ff591cb0dd',
        'financial_engine_sha256':engine,
        'financial_source_sha256':{name:source_hash(name) for name in ('paper_engine.py','paper_week.py')},
        'data_source':'apim.moex.com only; no public ISS live fallback',
        'universe':{'assets':['BR','GOLD'],'contract':'Maximum current VOLTODAY among eligible real contracts',
            'minimum_calendar_days_to_expiry':9,'historical_selection_requires':'Point-in-time eligible universe and VOLTODAY; nearest expiry is only a labelled approximation'},
        'warmup':{'calendar_lookback_days':85,'minimum_closed_h1':220,'minimum_complete_h4':50,
            'h4':'Four consecutive H1 in Moscow 00/04/08/12/16/20 hour blocks; incomplete blocks omitted'},
        'entry':{'decision':'Only the latest required closed H1; no intrabar confirmed signal',
            'LONG':['close > max(high of previous 20 H1), current H1 excluded','EMA20 > EMA50 on both H1 and completed H4',
                'Wilder ADX14 >= 22','H1 volume >= 0.6 * median(volume of previous 20 H1)'],
            'SHORT':'All directional inequalities reversed; same ADX and volume gates',
            'quote':'Positive BID/OFFER, spread <=5 ticks, independent entry quote no older than 90 seconds',
            'drift':'Entry including 2 adverse ticks must be within 0.5 ATR14 of signal H1 close',
            'identity':'Real contract/asset/expiry must match the source report',
            'uid':'contract:BUY_or_SELL:H1_begin_msk; one processing attempt per portfolio, no hindsight re-entry'},
        'levels':{'reference':'Signal H1 close, not a guaranteed fill',
            'stop':'Reference - direction * 1.5 ATR14, rounded to the actual tick',
            'target':'Reference + direction * 2 * rounded reference-to-stop distance',
            'actual_virtual_entry':'Fresh OFFER for LONG / BID for SHORT plus 2 adverse ticks',
            'realized_reward_risk':'May differ from reference 2R because the delayed entry price changes'},
        'position_size':{'capital_rub':100000,'accounts_risk_per_trade':[0.01,0.02],
            'risk_includes':'Stop distance, modeled adverse exit ticks, entry/exit commissions',
            'integer_contracts':True,'initial_margin_cap_fraction':0.5,
            'volume_cap':'1% of median volume of previous 20 H1','one_open_position_per_asset':True,
            'drawdown_entry_halt_fraction':0.2,'never_size_on_unconfirmed_open_profit':True},
        'costs':{'broker_per_side_notional_fraction':config['broker_fraction'],
            'tariff_scope':'User tariff as a fixed current-cost scenario; not a reconstruction of past broker debits',
            'exchange':'Observed BUYSELLFEE; historical value must not be replaced silently by current metadata',
            'slippage_adverse_ticks_per_side':2,'margin':'Observed exchange INITIALMARGIN; broker-specific uplift unknown',
            'valuation':'Minute price increments at last available indicative USD/RUB, BR multiplier10 / GOLD multiplier1; exact clearing not reconstructed'},
        'exit':{'protection':'On eligible complete M1, adverse 2 ticks and commissions',
            'same_full_minute_stop_and_target':'Stop first, explicitly a conservative model',
            'partial_entry_minute_touch':'Unresolved ordering -> REVIEW_REQUIRED and halt; no invented exit',
            'gaps':'Stop may fill beyond stop at adverse opening price',
            'maximum_calendar_hold_days':5,'roll':'Exit beginning at expires minus 8 calendar days',
            'publication':'M1 end +18 min must precede the controller observation',
            'notification':'Exit report can arrive later than the modeled minute touch; not an observed broker fill'},
        'clock':{'timezone':'Europe/Moscow','h1_label':'BEGIN of H1, not its end',
            'required_last_h1':'floor_hour(now_MSK -18min) -1h','publication_buffer_minutes':18,
            'schedule':'Controller cron19,39,55; early starts wait until :18:02. H1 cron25, Telegram cron50 are additional triggers/fallbacks',
            'delivery':'Direct controller relay after calculation and ledger checkpoints; not held until the ChatGPT hourly report',
            'github_queue_delay':'Variable, no exact arrival-time SLA',
            'historical_example':{'h1_begin_msk':'2026-10-27T13:00:00+03:00','h1_end_msk':'2026-10-27T14:00:00+03:00',
                'telegram_server_acceptance_msk':'2026-10-27T14:19:23+03:00','message_type':'NO_SIGNAL summary, not a trading signal',
                'user_receive_or_read_time':'UNKNOWN','proof_controller_run':40495155875},
            'confirmed_latency_distribution':'Not established. One summary is not a LONG/SHORT delivery-latency estimate'},
        'trading_hours':'No fixed assumed market-open schedule. Require current valid candles/quotes. A missing H1 is a data error, not proof of a closed market.',
        'live_week':{'id':'1d88347c8df1a0db74d49667','started_utc':'2026-10-27T09:18:32.255656+00:00',
            'ends_utc':'2026-11-03T09:18:32.255656+00:00','do_not_reset_or_mix_with_alternatives':True,
            'deadline_exit':'New entries stop at deadline; closing waits for required M1 coverage and fresh quote. No fictitious deadline fill'},
        'known_gaps':['Historical executable BID/OFFER and quote availability','Historical exchange economics and complete contract-selection universe',
            'Historical source/runner/Telegram availability','Confirmed historical session calendar','Broker uplift, actual fills and exact clearing'],
        'profitability_status':'UNCONFIRMED'}
    candidates=[
        {'id':'S0','name':'Donchian H1/H4','mechanism':'trend_breakout','implementation':'FROZEN_DEPLOYED',
            'definition':'Exact passport and deployed signal blob; no parameter improvements in baseline verification'},
        {'id':'S1','name':'Возврат к средней цене','mechanism':'statistical_mean_reversion','implementation':'SPECIFIED_NOT_IMPLEMENTED',
            'inputs':['H1 OHLCV'],
            'entry':'Using the prior20 H1 close mean and sample std: LONG if close < mean-2std and ADX14<18; SHORT symmetrically',
            'exit':'Target is the frozen prior20 mean; 1.5ATR protective stop, maximum24 hours; reject if delayed entry is already past target'},
        {'id':'S2','name':'Диапазон открытия сессии','mechanism':'session_opening_range','implementation':'SPECIFIED_NOT_IMPLEMENTED',
            'inputs':['M1/H1 OHLCV','confirmed historical session calendar'],
            'entry':'First complete H1 of the confirmed main session defines high/low. Later closed H1 above/below it triggers LONG/SHORT; one trade per asset/session',
            'exit':'1.5ATR stop,2R target or session-end quote, whichever occurs first; missing calendar/exit quote blocks inference'},
        {'id':'S3','name':'Временной эффект внутри дня','mechanism':'time_of_day_seasonality','implementation':'SPECIFIED_NOT_IMPLEMENTED',
            'inputs':['M1/H1 OHLCV','Moscow clock'],
            'entry':'Training-only mean next-hour return by asset and Moscow hour; at least60 training observations in a cell. Trade its sign only if absolute forecast >1.5 times estimated round-trip costs',
            'exit':'First available eligible quote60 minutes after entry, or1.5ATR stop/2R target; hourly coefficients frozen for each subsequent test fold'},
        {'id':'S4','name':'Ротация BR/GOLD по относительной силе','mechanism':'cross_asset_allocation','implementation':'SPECIFIED_NOT_IMPLEMENTED',
            'inputs':['Both assets aligned H1/daily OHLCV','confirmed trading-date completeness'],
            'entry':'At first eligible H1 decision of a trading date, rank prior20 completed daily log-return sums divided by their std. LONG the strongest if positive; otherwise SHORT the weakest. One selected asset, no borrowed extra leverage',
            'exit':'Rebalance at the next trading-date decision, with1.5ATR stop/2R target; do not manufacture a stationary BR/GOLD price spread'},
        {'id':'S5','name':'Давление объёма минутных свечей','mechanism':'signed_candle_volume_proxy','implementation':'SPECIFIED_NOT_IMPLEMENTED',
            'inputs':['Reconciled M1 OHLCV'],
            'entry':'For a closed H1 calculate Q=sum(sign(M1 close-open)*M1 volume)/sum(M1 volume). LONG if Q>=0.5, SHORT if Q<=-0.5, H1 volume>=prior20 median',
            'exit':'First available eligible quote60 minutes after entry, or1.5ATR stop/2R target',
            'interpretation':'Candle-derived proxy only. Not actual aggressor buy/sell flow, order book or executable spread'}]
    protocol={'id':'BR_GOLD_SIX_STRATEGIES_V1','frozen_at_utc':now.isoformat(),
        'objective':'Falsify or support an edge after real constraints; do not prove a predetermined winner',
        'strategy_families':6,'candidates':candidates,'candidate_spec_version':1,
        'baseline_verification':'Run the deployed S0 financial rules unchanged, separately at1% and2%; label any unavailable exact input',
        'common_comparison':{'capital_rub':100000,'primary_per_trade_risk_fraction':0.01,
            'maximum_combined_initial_planned_stop_risk_fraction':0.02,'initial_margin_cap_fraction':0.5,
            'integer_contracts':True,'risk_2_percent':'Sensitivity only; not a primary selection criterion',
            'risk_5_or_10_percent':'Optional stress only; never counts as a strategy improvement',
            'baseline_common_wrapper':'Any aggregate-risk admission difference from frozen S0 is reported separately; never presented as an unchanged live baseline',
            'same_for_every_strategy':['Source, common eligible contracts and verified overlap period','Information availability and execution-clock convention',
                'Broker and exchange fees, spread/slippage scenarios','Margin, integer contracts, joint risk cap and FX convention',
                'Data-quality and missing-input treatment'],
            'strategy_native_exits':'Different by hypothesis and predeclared, not selected from test results'},
        'execution_tracks':{'agent':'Independent observed execution quote after decision; actual recorded controller times where available',
            'human':'First executable quote after Telegram server acceptance plus reaction-time scenario; never fill at signal close',
            'human_reaction_seconds':[0,30,60,120],
            'historical_delay_minutes':[18,20,25,30,40,60],
            'unknown_historical_delivery':'Do not copy a single no-signal-summary delay into all past trading-signal timestamps',
            'missing_BBO':'Only explicitly approximate price scenarios; exact live-equivalent return remains null'},
        'chronological_testing':{'historical_start':'2023-01-01','historical_end_exclusive':'2026-10-26',
            'historical_status':'ALREADY_INSPECTED; retrospective analysis only',
            'initial_training':['2023-01-01','2025-01-01'],
            'walk_forward':'Expanding training; next calendar quarter evaluation starting2025. Fit/scaling/threshold selection uses training only',
            'purge_and_embargo':'At least5 calendar days at fold boundaries, extended for actual label/position horizons; indicator warmup uses past inputs only',
            'final_independent_test':'New data strictly after chosen configuration/code are locked. No untouched historical holdout is claimed',
            'prospective_horizon':'Predeclare90 calendar days at the final model lock; report sample size and uncertainty, extend only by a preregistered rule',
            'interim_rule':'Any parameter change after inspection starts a new trial and a new independent evaluation period'},
        'search_accounting':{'new_registered_family_specs':6,'prior_trials_total':'UNKNOWN',
            'legacy_evidence_sha256':legacy,
            'rule':'Log every family, parameter configuration, fit, failed run, cost/delay scenario and inspected dataset; do not reset the count to six',
            'optimization':'No parameter grid until unchanged-baseline and data-quality checks complete; a later bounded grid must be committed before scoring',
            'inference':'Report all trials; dependence-aware resampling, conservative multiple-comparison adjustment where justified; do not claim a valid total-search p-value with unknown legacy trial count',
            'reference':'https://scholarworks.wmich.edu/math_pubs/40/'},
        'metrics':['Net return and net expectancy after costs','Maximum drawdown and exposure','Profit factor and trade count',
            'By year/quarter and BR/GOLD contribution','Latency/spread/slippage/margin sensitivity','Skipped whole-contract trades and data failures',
            'Agent versus human difference','Dependence, overlap and uncertainty; no assumed independence of strategies'],
        'decision':'PASS/FAIL/INCONCLUSIVE/BLOCKED_DATA. Missing result is not zero return. No winner if evidence is insufficient.',
        'operational_safety':{'live_week_unchanged':True,'alternatives_live_enabled':False,'real_orders_enabled':False,
            'real_money_transition':'Separate explicit user decision; never automatic'},
        'data_status':{'historical_acquisition_run_id':40494996964,'historical_complete':False,
            'current_strategy_return_confirmed':False,'comparison_complete':False,'exact_historical_return':None}}
    write(OUT/'baseline-passport.json',passport)
    write(OUT/'study-protocol.json',protocol)
    with (OUT/'comparison-status.csv').open('w') as f:
        writer=csv.writer(f);writer.writerow(['strategy','mechanism','implementation','status','agent_net_return','human_net_return','max_drawdown','trades'])
        for c in candidates:writer.writerow([c['id'],c['mechanism'],c['implementation'],'BLOCKED_PENDING_VERIFIED_DATA','','','',''])
    ledger=OUT/'trial-ledger.jsonl'
    if not ledger.exists():
        for c in candidates:add_trial(ledger,{'trial_id':c['id']+'-spec-v1','strategy_id':c['id'],
            'registered_at_utc':now.isoformat(),'status':'SPECIFICATION_ONLY','selection_on_final_test':False,
            'definition_sha256':hashlib.sha256(canonical(c)).hexdigest(),'results_inspected':False})
    write(OUT/'manifest.json',{'protocol_sha256':file_sha(OUT/'study-protocol.json'),
        'passport_sha256':file_sha(OUT/'baseline-passport.json'),'files':{
            p.name:file_sha(p) for p in OUT.iterdir() if p.is_file() and p.name!='manifest.json'}})
    return passport,protocol


if __name__=='__main__':
    if (OUT/'study-protocol.json').exists():raise SystemExit('Already frozen; use a new version instead of overwriting the protocol')
    build(datetime.now(timezone.utc))
    print(OUT)
