"""Finite M1 price-scenario engine; monetary columns stay blocked without economics."""
from __future__ import annotations
import hashlib
import json
import math
from collections import Counter
from datetime import date, timedelta

import numpy as np
from scenario_features_v2 import minute, timestamp, day_start, regular_main_date


def scenario_clock(signal_end, scenario):
    return signal_end + scenario['delay_minutes'] + (scenario['reaction_seconds']+59)//60


def adverse_round(value, direction, tick):
    return round((math.ceil(value/tick-1e-9) if direction>0 else math.floor(value/tick+1e-9))*tick,8)


def fee(price, scenario):
    return abs(price)*(.00015+.000132)*scenario['fee_multiplier']


def fold_windows():
    result = []
    study_end = minute('2026-10-26T00:00')
    for year in (2025,2026):
        for month in (1,4,7,10):
            start = minute(f'{year}-{month:02d}-01T00:00')
            end = minute(f'{year+1 if month==10 else year}-{1 if month==10 else month+3:02d}-01T00:00')
            end = min(end,study_end)
            result.append({'id':f'{year}Q{(month-1)//3+1}','quarter_start':start,'train_end':start-5*1440,'first':start+5*1440,'end':end-5*1440})
    return result


def valid_decision(data, row, scenario, exceptions, first, end):
    t = scenario_clock(int(row.t)+60,scenario)
    if not first<=t<end: return False
    if not minute(data.meta['active_start'])<=t<minute(data.meta['active_end']): return False
    if minute(data.meta['expires'])-day_start(t)<9*1440: return False
    if not regular_main_date(timestamp(t),exceptions) or not 600<=t%1440<1140: return False
    return bool(row.ready)


def basic_signal(strategy, row, opening_range=None):
    c, d = float(row.close), 0
    if strategy=='S0':
        if row.adx<22 or row.volume<.6*row.median_volume: return None
        if c>row.donchian_high and row.ema20>row.ema50 and row.h4_ema20>row.h4_ema50: d=1
        elif c<row.donchian_low and row.ema20<row.ema50 and row.h4_ema20<row.h4_ema50: d=-1
    elif strategy=='S1':
        if row.adx>=18 or not math.isfinite(row.prior_std) or row.prior_std<=0: return None
        if c<row.prior_mean-2*row.prior_std: d=1
        elif c>row.prior_mean+2*row.prior_std: d=-1
    elif strategy=='S2':
        if opening_range is None: return None
        if c>opening_range[0]: d=1
        elif c<opening_range[1]: d=-1
    elif strategy=='S5':
        if row.volume<row.median_volume or not math.isfinite(row.cmf) or not math.isfinite(row.prior_cmf): return None
        if row.cmf>.05 and row.prior_cmf<=.05: d=1
        elif row.cmf<-.05 and row.prior_cmf>=-.05: d=-1
    if not d:return None
    return {'direction':d,'target_override':float(row.prior_mean) if strategy=='S1' else None}


def delayed_label(data, row, scenario, train_end):
    requested = scenario_clock(int(row.t)+60,scenario)
    k = int(np.searchsorted(data.m1_times,requested))
    while k<len(data.m1_times) and data.m1_times[k]-requested<=5 and data.m1[k,4]<=0:k+=1
    if k>=len(data.m1_times) or data.m1_times[k]-requested>5 or data.m1[k,4]<=0:return None
    j = int(np.searchsorted(data.m1_times,data.m1_times[k]+60))
    while j<len(data.m1_times) and data.m1_times[j]-(data.m1_times[k]+60)<=5 and data.m1[j,4]<=0:j+=1
    if j>=len(data.m1_times) or data.m1_times[j]>train_end or data.m1_times[j]-(data.m1_times[k]+60)>5 or data.m1[j,4]<=0:return None
    return float(data.m1[j,0]/data.m1[k,0]-1),int(data.m1_times[j])


def fit_hour_model(dataset, scenario, fold, exceptions):
    cells = {}
    evidence = []
    for data in dataset:
        subset=data.h1[(data.h1.t>=minute('2023-01-01T00:00')-180)&(data.h1.t<fold['train_end'])]
        for row in subset.itertuples():
            if not valid_decision(data,row,scenario,exceptions,minute('2023-01-01T00:00'),fold['train_end']):continue
            label = delayed_label(data,row,scenario,fold['train_end'])
            if label is None:continue
            key = (data.meta['asset'],(int(row.t)+60)%1440//60)
            cells.setdefault(key,[]).append(label[0])
            evidence.append((data.meta['secid'],int(row.t),label[1],label[0]))
    model = {k:{'mean':float(np.mean(v)),'count':len(v)} for k,v in cells.items() if len(v)>=60}
    raw = json.dumps(evidence,separators=(',',':')).encode()
    audit = {'scenario':scenario['id'],'fold':fold['id'],'train_label_end_max':max((x[2] for x in evidence),default=None),'train_cutoff':fold['train_end'],'label_count':len(evidence),'input_sha256':hashlib.sha256(raw).hexdigest(),'coefficients':{a+':'+str(h):v for (a,h),v in model.items()}}
    if audit['train_label_end_max'] is not None and audit['train_label_end_max']>fold['train_end']:raise AssertionError('Future training label')
    return model,audit


def s4_choices(dataset, scenario, fold, exceptions):
    pairs = {}
    for i,data in enumerate(dataset):
        subset=data.h1[(data.h1.t>=fold['first']-180)&(data.h1.t<fold['end'])]
        for row in subset.itertuples():
            if valid_decision(data,row,scenario,exceptions,fold['first'],fold['end']):
                pairs.setdefault(int(row.t),{})[data.meta['asset']] = (i,row)
    daily = {}
    for t,values in sorted(pairs.items()):
        if set(values)!={'BR','GOLD'}:continue
        d = day_start(t)
        if d not in daily:daily[d]=values
    chosen = {}
    for d,values in daily.items():
        scores = {asset:float(row.relative_score) for asset,(_,row) in values.items()}
        if not all(math.isfinite(s) for s in scores.values()):continue
        strongest = max(scores,key=lambda a:(scores[a],a=='BR'))
        weakest = min(scores,key=lambda a:(scores[a],a!='BR'))
        asset,direction = (strongest,1) if scores[strongest]>0 else (weakest,-1)
        if scores[asset]==0:continue
        i,row=values[asset]
        next_date = date.fromisoformat(timestamp(d)[:10])+timedelta(days=1)
        while not regular_main_date(next_date.isoformat(),exceptions):next_date+=timedelta(days=1)
        next_values = daily.get(minute(next_date.isoformat()))
        next_t = min((int(r.t)+60 for _,r in next_values.values()),default=None) if next_values else None
        chosen[(i,int(row.t))]={'direction':direction,'target_override':None,'next_decision_end':next_t}
    return chosen


def make_setups(dataset, strategy, scenario, fold, exceptions, model=None):
    choices = s4_choices(dataset,scenario,fold,exceptions) if strategy=='S4' else {}
    setups,counts=[],Counter()
    for i,data in enumerate(dataset):
        opening = {}
        subset=data.h1[(data.h1.t>=fold['first']-180)&(data.h1.t<fold['end'])]
        for row in subset.itertuples():
            stamp=int(row.t)
            if stamp%1440==600:opening[day_start(stamp)]=(float(row.high),float(row.low),stamp)
            if not valid_decision(data,row,scenario,exceptions,fold['first'],fold['end']):continue
            counts['eligible_H1']+=1
            if strategy=='S4':s=choices.get((i,stamp))
            elif strategy=='S3':
                coefficient=(model or {}).get((data.meta['asset'],(stamp+60)%1440//60))
                if coefficient is None:counts['training_cell_unready']+=1;continue
                tick=.01 if data.meta['asset']=='BR' else .1
                cost=2*((scenario['spread_ticks']/2+scenario['slippage_ticks'])*tick/row.close+(.00015+.000132)*scenario['fee_multiplier'])
                forecast=coefficient['mean']
                s={'direction':1 if forecast>0 else -1,'target_override':None} if abs(forecast)>1.5*cost else None
            else:
                orb=opening.get(day_start(stamp))
                if strategy=='S2' and (orb is None or orb[2]>=stamp):continue
                s=basic_signal(strategy,row,orb[:2] if orb else None)
            if s is None:continue
            setups.append({'data_index':i,'strategy':strategy,'fold':fold['id'],'signal_begin':stamp,'signal_end':stamp+60,'reference':float(row.close),'atr':float(row.atr),'median_volume':float(row.median_volume),'direction':s['direction'],'target_override':s.get('target_override'),'next_decision_end':s.get('next_decision_end')})
    counts['setups']=len(setups)
    return setups,counts


def execution_trade(data, setup, scenario, fold):
    direction=setup['direction'];asset=data.meta['asset'];tick=.01 if asset=='BR' else .1
    requested=scenario_clock(setup['signal_end'],scenario)
    if requested-setup['signal_end']>78:return None,'SIGNAL_EXPIRED'
    k=int(np.searchsorted(data.m1_times,requested))
    while k<len(data.m1_times) and data.m1_times[k]-requested<=5 and data.m1[k,4]<=0:k+=1
    if k>=len(data.m1_times) or data.m1_times[k]-requested>5:return None,'ENTRY_M1_UNAVAILABLE'
    entered=int(data.m1_times[k]);half=scenario['spread_ticks']*tick/2;slip=scenario['slippage_ticks']*tick
    if not 600<=entered%1440<1140:return None,'OUTSIDE_MAIN_ENTRY_HOURS'
    entry=adverse_round(data.m1[k,0]+direction*(half+slip),direction,tick)
    stop=round(round((setup['reference']-direction*1.5*setup['atr'])/tick)*tick,8)
    target=round(round((setup['target_override'] if setup['target_override'] is not None else setup['reference']+direction*2*abs(setup['reference']-stop))/tick)*tick,8)
    if abs(entry-setup['reference'])>.5*setup['atr']+1e-9:return None,'ENTRY_DRIFT'
    if direction*(entry-stop)<=0 or direction*(target-entry)<=0:return None,'LEVEL_ORDER_CANCELLED'
    if math.floor(.01*setup['median_volume'])<1:return None,'WHOLE_CONTRACT_VOLUME_CAP'
    strategy=setup['strategy']
    native=entered+5*1440
    if strategy=='S1':native=entered+1440
    elif strategy=='S2':native=day_start(entered)+1140
    elif strategy in ('S3','S5'):native=entered+60
    elif strategy=='S4':native=scenario_clock(setup['next_decision_end'],scenario) if setup['next_decision_end'] is not None else fold['end']
    if entered>=native:return None,'SIGNAL_ALREADY_CANCELLED_AT_DEADLINE'
    roll=minute(data.meta['expires'])-8*1440
    deadline=min(native,roll,fold['end'])
    exit_reason='FOLD_END' if deadline==fold['end'] else 'ROLL' if deadline==roll else 'TIMED_EXIT'
    terminal=int(np.searchsorted(data.m1_times,deadline))
    stop_at=min(terminal,len(data.m1_times))
    observed=data.m1[k:stop_at]
    qopen=observed[:,0]-direction*half
    adverse=observed[:,2]-half if direction>0 else observed[:,1]+half
    favorable=observed[:,1]-half if direction>0 else observed[:,2]+half
    stop_touch=(direction*(adverse-stop)<=0)&(observed[:,4]>0)
    target_touch=(direction*(favorable-target)>=0)&(observed[:,4]>0)
    touched=np.flatnonzero(stop_touch|target_touch)
    ambiguous=False
    if len(touched):
        local=int(touched[0]);j=k+local
        if stop_touch[local]:
            raw=min(qopen[local],stop) if direction>0 else max(qopen[local],stop)
            exit_reason='STOP';ambiguous=bool(target_touch[local])
        else:raw=target;exit_reason='TARGET'
        exit_time=int(data.m1_times[j])+1
    elif terminal<len(data.m1_times):
        j=terminal
        while j<len(data.m1_times) and data.m1[j,4]<=0:j+=1
        if j>=len(data.m1_times):return None,'UNRESOLVED_EXIT_COVERAGE'
        raw=float(data.m1[j,0])-direction*half
        exit_time=int(data.m1_times[j])
        if exit_time>fold['end']+5*1440:return None,'UNRESOLVED_EXIT_BOUNDARY'
    else:return None,'UNRESOLVED_EXIT_COVERAGE'
    fill=adverse_round(raw-direction*slip,-direction,tick)
    risk=abs(entry-stop)+slip+fee(entry,scenario)+fee(stop,scenario)
    net=direction*(fill-entry)-fee(entry,scenario)-fee(fill,scenario)
    if not math.isfinite(risk) or risk<=0:return None,'INVALID_RISK'
    mark_stop=j+1 if len(touched) else j
    mark_times=data.m1_times[k:mark_stop]+1
    mark_price=data.m1[k:mark_stop,3]-direction*(half+slip)
    marks=(direction*(mark_price-entry)-fee(entry,scenario)-np.abs(mark_price)*(.00015+.000132)*scenario['fee_multiplier'])/risk
    if len(mark_times) and mark_times[-1]==exit_time:marks[-1]=net/risk
    else:
        mark_times=np.append(mark_times,exit_time);marks=np.append(marks,net/risk)
    return {**setup,'contract':data.meta['secid'],'asset':asset,'entry_time':entered,'exit_time':exit_time,'entry_requested':requested,'entry':float(entry),'stop':stop,'target':target,'exit':float(fill),'risk_price_units':risk,'net_R':float(net/risk),'reason':exit_reason,'both_levels_same_minute':ambiguous,'timed_exit_lateness_minutes':max(0,exit_time-deadline) if exit_reason not in ('STOP','TARGET') else 0,'source_m1_gap_count':int((np.diff(data.m1_times[k:mark_stop])>1).sum()),'mark_times':mark_times,'marks_R':marks,'exact_execution':False},None


def monetary_gate(evidence):
    required=('historical_tick_and_multiplier_confirmed','collateral_at_entry_confirmed','effective_fee_schedule_confirmed','historical_fx_confirmed')
    missing=[k for k in required if evidence.get(k) is not True]
    return {'status':'ELIGIBLE_SCENARIO' if not missing else 'BLOCKED_DATA','missing':missing,'net_return_pct':None,'net_pnl_rub':None}


def price_book(trades,first,end,strategy):
    accepted=[];counts=Counter();occupied={};session_used=set();seen=set()
    for trade in sorted(trades,key=lambda t:(t['entry_time'],t['asset']!='BR',t['contract'],t['signal_begin'])):
        uid=(trade['contract'],trade['signal_begin'],trade['direction'])
        if uid in seen:counts['duplicate_UID']+=1;continue
        seen.add(uid)
        t=trade['entry_time'];asset=trade['asset']
        occupied={a:until for a,until in occupied.items() if until>t}
        if asset in occupied:counts['position_already_open']+=1;continue
        if strategy=='S4' and occupied:counts['rotation_one_total_position']+=1;continue
        daykey=(asset,day_start(t))
        if strategy=='S2' and daykey in session_used:counts['opening_range_one_trade_per_session']+=1;continue
        occupied[asset]=trade['exit_time'];session_used.add(daykey);accepted.append(trade)
    if not accepted:
        return {'trades':0,'net_R':None,'mean_net_R':None,'profit_factor':None,'max_drawdown_R':None,'unresolved':0,'admission_skips':dict(counts)},[],[]
    max_end=max(end,max(t['exit_time'] for t in accepted))
    increments=np.zeros(max_end-first+2,dtype=float)
    daily={}
    for trade in accepted:
        times=trade['mark_times'];marks=trade['marks_R']
        deltas=np.diff(np.r_[0.,marks])
        np.add.at(increments,times-first,deltas)
        day=timestamp(trade['exit_time'])[:10]
        daily[day]=daily.get(day,0)+trade['net_R']
    curve=np.cumsum(increments)
    net=sum(t['net_R'] for t in accepted)
    if not math.isclose(float(curve[-1]),net,abs_tol=1e-8):raise AssertionError('R-book PnL does not reconcile')
    peak=np.maximum.accumulate(np.r_[0.,curve])[1:]
    dd=float(np.max(peak-curve))
    profits=sum(max(t['net_R'],0) for t in accepted);losses=-sum(min(t['net_R'],0) for t in accepted)
    metrics={'trades':len(accepted),'net_R':float(net),'mean_net_R':float(net/len(accepted)),'profit_factor':float(profits/losses) if losses else None,'win_rate_pct':100*sum(t['net_R']>0 for t in accepted)/len(accepted),'max_drawdown_R':dd,'max_drawdown_scope':'Minute mark-to-market R, not percent capital; intra-minute equity path unknown','both_levels_stop_first':sum(t['both_levels_same_minute'] for t in accepted),'entry_delay_median_minutes':float(np.median([t['entry_time']-t['signal_end'] for t in accepted])),'timed_exit_max_lateness_minutes':max(t['timed_exit_lateness_minutes'] for t in accepted),'admission_skips':dict(counts)}
    curve_days=[]
    for t in range(first,max_end+1,1440):
        ix=min(t-first+1439,len(curve)-1)
        curve_days.append({'day':timestamp(t)[:10],'equity_R':float(curve[ix])})
    return metrics,accepted,curve_days
