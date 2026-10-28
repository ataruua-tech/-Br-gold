"""Audit correction: close the ORB before clearing, not after a holiday's last trade."""
import hashlib
import json
import pickle
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from freeze_six_strategy_study import add_trial,canonical
from scenario_features_v2 import ROOT,protocol,minute,timestamp,day_start
from scenario_engine_v2 import fold_windows,make_setups,execution_trade,price_book,monetary_gate
from run_scenarios_v2 import bootstrap_mean,summarize_trades,scalar_trade

OUT=ROOT/'research/scenario-study-v2'
RUN='s2-session-correction-v21'


def correction_sources():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['research/correct_s2_session_v21.py','research/scenario_engine_v2.py']}


def register():
    spec={'id':RUN,'type':'TECHNICAL_PROTOCOL_CORRECTION_NOT_PARAMETER_OPTIMIZATION','previous_outcomes_inspected':True,'reason':'One original primary S2 timed exit crossed into the next calendar date. A formal19:00 session bound must not imply an executable19:00 quote during clearing/holiday closure.',
        'session_exit_rule':'One minute before end of regular continuous main trading:18:49MSK before23March2026 (18:50 clearing start),18:59MSK from23March2026 (19:00 main-session end). First actual positive-volume M1 at/after request; crossing into a new calendar date is UNRESOLVED, never a valid overnight ORB exit.',
        'entry_cancel':'Entry at or after the declared intraday exit deadline is cancelled; no new timing parameter is chosen by profit',
        'known_calendar_limit':'Regular-regime assumption only; unverified shortened sessions may still block inference',
        'primary_sources':['https://www.moex.com/n49786','https://www.moex.com/n98363'],
        'unchanged':'Five other families, same original datasets,32 scenarios, price costs, chronological folds, screening criteria and economic gate',
        'old_results':'Retain scenario-v2-run001 and its original code commit/e1cdf71c39aea4be489d375914a7581d2040751a; never overwrite old trial records',
        'score_cells':32,'legacy_trial_count':'UNKNOWN','implementation_sha256':correction_sources(),'registered_at_utc':datetime.now(timezone.utc).isoformat()}
    path=OUT/'session-correction-v21.json'
    if path.exists():raise ValueError('Already registered')
    path.write_bytes(canonical(spec)+b'\n')
    add_trial(ROOT/'research/six-strategy-v1/trial-ledger.jsonl',{'trial_id':RUN,'registered_at_utc':spec['registered_at_utc'],'status':'CORRECTION_REGISTERED_BEFORE_RESCORING','selection_on_final_test':False,'results_inspected':False,'previous_version_results_inspected':True,'implementation_sha256':correction_sources(),'definition_sha256':hashlib.sha256(canonical(spec)).hexdigest(),'score_cells':32})
    print(json.dumps(spec))


def run():
    spec=json.loads((OUT/'session-correction-v21.json').read_text())
    if spec['implementation_sha256']!=correction_sources():raise ValueError('Unregistered correction source')
    out=OUT/'results'/RUN;out.mkdir(parents=True,exist_ok=True)
    cache=OUT/'dataset-cache.pkl';meta=json.loads((OUT/'dataset-cache-manifest.json').read_text())
    if hashlib.sha256(cache.read_bytes()).hexdigest()!=meta['cache_sha256']:raise ValueError('Unverified data cache')
    with cache.open('rb') as stream:dataset,evidence=pickle.load(stream)
    p=protocol();folds=fold_windows();cells=[];primary_trades=[];primary_curve=[]
    days=sorted({str(t.date()) for d in dataset for t in d.h1.index if minute('2025-01-01')<=minute(str(t))<minute('2026-10-26')})
    for scenario in p['scenario_design']:
        accepted_all=[];generation=Counter();fold_results=[]
        for fold in folds:
            setups,counts=make_setups(dataset,'S2',scenario,fold,evidence['calendar_exceptions'])
            generated=[];failures=Counter()
            for setup in setups:
                d=day_start(setup['signal_end'])
                setup['session_exit_deadline']=d+(1129 if d<minute('2026-03-23') else 1139)
                trade,error=execution_trade(dataset[setup['data_index']],setup,scenario,fold)
                if error:failures[error]+=1
                else:
                    if trade['reason']=='TIMED_EXIT':trade['reason']='INTRADAY_SESSION_EXIT'
                    generated.append(trade)
            metrics,accepted,_=price_book(generated,fold['first'],fold['end'],'S2')
            metrics.update(fold=fold['id'],execution_failures=dict(failures));fold_results.append(metrics)
            accepted_all.extend(accepted);generation.update(counts);generation.update(failures)
        metrics,accepted,curve=price_book(accepted_all,folds[0]['first'],folds[-1]['end'],'S2')
        unresolved=sum(v for k,v in generation.items() if k.startswith('UNRESOLVED'))
        row={'strategy':'S2','scenario':scenario['id'],'track':scenario['track'],'version':RUN,'status':'INCOMPLETE_RESOLVED_SUBSET_ONLY' if unresolved else 'COMPLETED_EXPLICIT_PRICE_SCENARIO','execution_scope':'EXPLICIT_APPROXIMATE_SCENARIO','exact_S0_replay_status':'BLOCKED_DATA','metrics':metrics,'generation':dict(generation),'folds':fold_results,'years':{str(y):summarize_trades([t for t in accepted if timestamp(t['entry_time']).startswith(str(y))]) for y in (2025,2026)},'assets':{a:summarize_trades([t for t in accepted if t['asset']==a]) for a in ('BR','GOLD')},'unresolved_setups':unresolved,'monetary':monetary_gate({})}
        if unresolved:
            row['resolved_subset_diagnostic']=dict(metrics)
            for field in ('net_R','mean_net_R','profit_factor','max_drawdown_R'):row['metrics'][field]=None
        if scenario['id']==p['primary_agent']:
            row['bootstrap']=bootstrap_mean(accepted,days)
            primary_trades=[scalar_trade(t) for t in accepted];primary_curve=curve
        cells.append(row)
        print(json.dumps({'scenario':scenario['id'],'trades':metrics['trades'],'net_R':metrics['net_R'],'unresolved':unresolved}),flush=True)
    result={'run_id':RUN,'status':'CORRECTION_FINISHED','specification':spec,'source_data_hash':evidence['dataset_source_hash'],'cells':cells,'completed_at_utc':datetime.now(timezone.utc).isoformat(),'monetary_status':'BLOCKED_DATA','other_five_results_unchanged':True}
    (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    (out/'primary-trades.json').write_text(json.dumps(primary_trades,allow_nan=False)+'\n')
    (out/'primary-curve.json').write_text(json.dumps(primary_curve,allow_nan=False)+'\n')
    add_trial(ROOT/'research/six-strategy-v1/trial-ledger.jsonl',{'trial_id':RUN+'-completed','registered_at_utc':result['completed_at_utc'],'status':'TECHNICAL_CORRECTION_COMPLETED','selection_on_final_test':False,'results_inspected':True,'score_cells':32,'results_sha256':hashlib.sha256((out/'results.json').read_bytes()).hexdigest()})


if __name__=='__main__':
    import sys
    register() if '--register' in sys.argv else run()
