"""Run only the preregistered finite comparison; all market inputs are archived."""
from __future__ import annotations
import argparse
import csv
import gzip
import hashlib
import json
import pickle
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from freeze_six_strategy_study import add_trial
from scenario_features_v2 import ROOT, protocol, load_dataset, minute, timestamp
from scenario_engine_v2 import fold_windows, fit_hour_model, make_setups, execution_trade, price_book, monetary_gate

OUT = ROOT/'research/scenario-study-v2'
SOURCE_FILES = ['research/scenario_features_v2.py','research/scenario_engine_v2.py','research/run_scenarios_v2.py']


def implementation_hashes():
    return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCE_FILES}


def validate_registered_run(run_id):
    records = [json.loads(s) for s in (ROOT/'research/six-strategy-v1/trial-ledger.jsonl').read_text().splitlines()]
    registered = next((r for r in records if r['trial_id']==run_id),None)
    if registered is None or registered.get('implementation_sha256')!=implementation_hashes() or registered.get('results_inspected') is not False:
        raise ValueError('Scoring implementation/run must be registered with current source hashes first')


def scalar_trade(t):
    return {k:v for k,v in t.items() if k not in ('mark_times','marks_R')}


def summarize_trades(trades):
    net=sum(t['net_R'] for t in trades)
    gain=sum(max(t['net_R'],0) for t in trades);loss=-sum(min(t['net_R'],0) for t in trades)
    return {'trades':len(trades),'net_R':float(net) if trades else None,'mean_net_R':float(net/len(trades)) if trades else None,'profit_factor':float(gain/loss) if loss else None}


def bootstrap_mean(trades, observed_days, seed=20261028):
    if len(trades)<50 or len(observed_days)<40:return {'status':'INSUFFICIENT_OBSERVATIONS','lower90_mean_R':None}
    days={d:i for i,d in enumerate(sorted(set(observed_days)|{timestamp(t['exit_time'])[:10] for t in trades}))}
    pnl=np.zeros(len(days));counts=np.zeros(len(days))
    for t in trades:
        i=days[timestamp(t['exit_time'])[:10]];pnl[i]+=t['net_R'];counts[i]+=1
    rng=np.random.default_rng(seed);n=len(days);block=20
    starts=rng.integers(0,max(n-block+1,1),size=(2000,(n+block-1)//block))
    ix=(starts[:,:,None]+np.arange(block)).reshape(2000,-1)[:,:n]
    totals=pnl[ix].sum(axis=1);denom=counts[ix].sum(axis=1)
    means=totals[denom>0]/denom[denom>0]
    return {'status':'CONDITIONAL_RETROSPECTIVE_BLOCK_BOOTSTRAP','block_observed_MSK_dates':20,'replicates':2000,'lower90_mean_R':float(np.quantile(means,.05)),'upper90_mean_R':float(np.quantile(means,.95)),'global_search_significance':'NOT_ESTABLISHED_UNKNOWN_LEGACY_TRIAL_COUNT','seed':seed}


def main(run_id):
    validate_registered_run(run_id)
    p=protocol()
    out=OUT/'results'/run_id
    out.mkdir(parents=True,exist_ok=True)
    cache=OUT/'dataset-cache.pkl'
    cache_meta=OUT/'dataset-cache-manifest.json'
    feature_hash=implementation_hashes()['research/scenario_features_v2.py']
    if cache.exists() and cache_meta.exists():
        m=json.loads(cache_meta.read_text())
        if m['feature_source_sha256']!=feature_hash or hashlib.sha256(cache.read_bytes()).hexdigest()!=m['cache_sha256']:
            raise ValueError('Unverified feature cache; do not reuse')
        with cache.open('rb') as stream:dataset,evidence=pickle.load(stream)
    else:
        print(json.dumps({'stage':'VERIFY_AND_PREPARE_PRICE_ARCHIVES'}),flush=True)
        dataset,evidence=load_dataset()
        with cache.open('wb') as stream:pickle.dump((dataset,evidence),stream,protocol=5)
        cache_meta.write_text(json.dumps({'feature_source_sha256':feature_hash,'cache_sha256':hashlib.sha256(cache.read_bytes()).hexdigest(),'source_hash':evidence['dataset_source_hash']},indent=2)+'\n')
    (out/'input-evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({'stage':'DATA_READY','contracts':len(dataset),'source_hash':evidence['dataset_source_hash']}),flush=True)
    exceptions=evidence['calendar_exceptions']
    folds=fold_windows();cells=[];fit_cache={};primary_trades={};primary_curves={};all_trades_count=0
    observed_days=sorted({str(t.date()) for d in dataset for t in d.h1.index if minute('2025-01-01')<=minute(str(t))<minute('2026-10-26')})
    with (out/'S3-training-fits.jsonl').open('w') as fits:
        for scenario in p['scenario_design']:
            scenario_rows=[]
            for strategy in ['S0','S1','S2','S3','S4','S5']:
                retained=[];generation=Counter();fold_results=[]
                for fold in folds:
                    model=None
                    if strategy=='S3':
                        key=(scenario['delay_minutes'],scenario['reaction_seconds'],fold['id'])
                        if key not in fit_cache:
                            model,audit=fit_hour_model(dataset,scenario,fold,exceptions)
                            fit_cache[key]=(model,audit)
                            fits.write(json.dumps(audit,allow_nan=False)+'\n');fits.flush()
                        else:model,audit=fit_cache[key]
                    setups,counts=make_setups(dataset,strategy,scenario,fold,exceptions,model)
                    generated=[];failures=Counter()
                    for setup in setups:
                        trade,error=execution_trade(dataset[setup['data_index']],setup,scenario,fold)
                        if error:failures[error]+=1
                        else:generated.append(trade)
                    metrics,accepted,_=price_book(generated,fold['first'],fold['end'],strategy)
                    metrics['fold']=fold['id'];metrics['execution_failures']=dict(failures)
                    fold_results.append(metrics)
                    retained.extend(accepted);generation.update(counts);generation.update(failures)
                metrics,accepted,curve=price_book(retained,folds[0]['first'],folds[-1]['end'],strategy)
                unresolved=sum(v for k,v in generation.items() if k.startswith('UNRESOLVED'))
                status='INCOMPLETE_RESOLVED_SUBSET_ONLY' if unresolved else 'COMPLETED_EXPLICIT_PRICE_SCENARIO'
                row={'strategy':strategy,'scenario':scenario['id'],'track':scenario['track'],'status':status,'execution_scope':'EXPLICIT_APPROXIMATE_SCENARIO','exact_S0_replay_status':'BLOCKED_DATA','metrics':metrics,'generation':dict(generation),'folds':fold_results,'years':{str(y):summarize_trades([t for t in accepted if timestamp(t['entry_time']).startswith(str(y))]) for y in (2025,2026)},'assets':{a:summarize_trades([t for t in accepted if t['asset']==a]) for a in ('BR','GOLD')},'unresolved_setups':unresolved,'monetary':monetary_gate({})}
                if unresolved:
                    row['resolved_subset_diagnostic']=dict(metrics)
                    for field in ('net_R','mean_net_R','profit_factor','max_drawdown_R'):row['metrics'][field]=None
                if scenario['id']==p['primary_agent']:
                    row['bootstrap']=bootstrap_mean(accepted,observed_days)
                    primary_trades[strategy]=[scalar_trade(t) for t in accepted]
                    primary_curves[strategy]=curve
                all_trades_count+=len(accepted)
                cells.append(row);scenario_rows.append({'strategy':strategy,'status':status,'trades':metrics['trades'],'net_R':metrics['net_R'],'unresolved':unresolved})
                (out/'cells-progress.json').write_text(json.dumps(cells,allow_nan=False)+'\n')
            print(json.dumps({'stage':'SCENARIO_COMPLETE','scenario':scenario['id'],'cells':scenario_rows}),flush=True)
    indexed={(r['strategy'],r['scenario']):r for r in cells}
    comparison=[]
    for strategy in ['S0','S1','S2','S3','S4','S5']:
        base=indexed[(strategy,p['primary_agent'])];human=indexed[(strategy,p['primary_human'])]
        stress=indexed[(strategy,'stress-r0')];late=indexed[(strategy,'d40-r60-s2-l2-f1')]
        requirements={
            '>=50trades':base['metrics']['trades']>=50,
            'positive2025':(base['years']['2025']['net_R'] or 0)>0,
            'positive2026':(base['years']['2026']['net_R'] or 0)>0,
            'positive_human':(human['metrics']['net_R'] or 0)>0,
            'positive_cost_stress':(stress['metrics']['net_R'] or 0)>0,
            'positive_delay40_human60':(late['metrics']['net_R'] or 0)>0,
            'bootstrap_lower90>0':(base.get('bootstrap',{}).get('lower90_mean_R') or 0)>0,
            'no_unresolved_exits':all(r['unresolved_setups']==0 for r in (base,human,stress,late)),
        }
        comparison.append({'strategy':strategy,'price_screen':'PASS' if all(requirements.values()) else 'FAIL_OR_INCONCLUSIVE','failed_requirements':[k for k,v in requirements.items() if not v],'primary':base,'human':human['metrics'],'stress':stress['metrics'],'delay40_human':late['metrics'],'monetary_status':'BLOCKED_DATA','net_pnl_rub':None,'net_return_pct':None,'live_promotion':'NOT_ELIGIBLE_BLOCKED_ECONOMICS'})
    passing=[r for r in comparison if r['price_screen']=='PASS']
    candidate=min(passing,key=lambda r:(-min(r[x]['mean_net_R'] if x!='primary' else r[x]['metrics']['mean_net_R'] for x in ('primary','human','stress','delay40_human')),r['primary']['metrics']['max_drawdown_R']))['strategy'] if passing else None
    result={'run_id':run_id,'protocol_sha256':hashlib.sha256((OUT/'protocol.json').read_bytes().strip()).hexdigest(),'implementation_sha256':implementation_hashes(),'source_data_hash':evidence['dataset_source_hash'],'status':'COMPARISON_FINISHED_PRICE_SCENARIOS_ONLY','monetary_status':'BLOCKED_DATA','exact_agent_return_confirmed':False,'cells':cells,'comparison':comparison,'price_logic_candidate':candidate,'live_candidate':None,'optimization':'NOT_RUN_NO_FINANCIALLY_ELIGIBLE_CANDIDATE','new_seven_day_pilot':'NOT_STARTED','previous_week_unchanged':True,'legacy_trial_count':'UNKNOWN','training_fits':len(fit_cache),'accepted_trade_instances_across_sensitivity_cells':all_trades_count,'finished_at_utc':datetime.now(timezone.utc).isoformat()}
    (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    (out/'primary-trades.json').write_text(json.dumps(primary_trades,allow_nan=False)+'\n')
    (out/'primary-curves.json').write_text(json.dumps(primary_curves,allow_nan=False)+'\n')
    fields=['strategy','price_screen','agent_trades','agent_net_R','agent_mean_net_R','agent_profit_factor','agent_max_drawdown_R','human_net_R','stress_net_R','delay40_human_net_R','net_pnl_rub','net_return_pct','monetary_status','failed_requirements']
    with (out/'comparison.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for row in comparison:
            m=row['primary']['metrics']
            writer.writerow({'strategy':row['strategy'],'price_screen':row['price_screen'],'agent_trades':m['trades'],'agent_net_R':m['net_R'],'agent_mean_net_R':m['mean_net_R'],'agent_profit_factor':m['profit_factor'],'agent_max_drawdown_R':m['max_drawdown_R'],'human_net_R':row['human']['net_R'],'stress_net_R':row['stress']['net_R'],'delay40_human_net_R':row['delay40_human']['net_R'],'net_pnl_rub':None,'net_return_pct':None,'monetary_status':'BLOCKED_DATA','failed_requirements':'; '.join(row['failed_requirements'])})
    add_trial(ROOT/'research/six-strategy-v1/trial-ledger.jsonl',{'trial_id':run_id+'-completed','registered_at_utc':result['finished_at_utc'],'status':'COMPLETED_EXPLICIT_PRICE_SCENARIOS','selection_on_final_test':False,'results_inspected':True,'result_sha256':hashlib.sha256((out/'results.json').read_bytes()).hexdigest(),'score_cells':len(cells),'training_fits':len(fit_cache),'exact_replay_confirmed':False})
    print(json.dumps({'stage':'FINISHED','out':str(out),'cells':len(cells),'price_candidate':candidate,'monetary_status':'BLOCKED_DATA'}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run-id',required=True)
    main(parser.parse_args().run_id)
