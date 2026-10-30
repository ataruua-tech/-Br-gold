"""Independent reconciliation of native orders/fills against archived candles.
Read-only input. Outputs quote points only, no commissions/cash interpretation.
"""
from collections import defaultdict
from decimal import Decimal as D, ROUND_HALF_EVEN
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import json
import importlib.util
import argparse

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--evidence', type=Path, required=True, help='Extracted portable-evidence.zip root')
parser.add_argument('--output', type=Path, required=True, help='New output directory, preserving earlier attempts')
args=parser.parse_args()
ROOT=args.evidence.resolve()
OUT=args.output.resolve()
OUT.mkdir(parents=True, exist_ok=False)
spec=importlib.util.spec_from_file_location('independent',Path(__file__).parents[1]/'control'/'independent_indicators.py')
indicator=importlib.util.module_from_spec(spec);spec.loader.exec_module(indicator)

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'),parse_float=D)

def stamp(value):
    return value.replace('T',' ')[:19]

def dump(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')

def selected_row(row):
    return {k:row[k] for k in ('index','begin','h1_close_msk','ohlcv','fast','slow','bollinger','rsi','rsi_ready')}

summary=[]
for code,symbol in [('A','BRX6'),('A','GDZ6'),('B','BRX6'),('B','GDZ6')]:
    run=code+'_'+symbol;directory=ROOT/'runs'/run
    audit=next((directory/'ControlAudit').glob('*.jsonl'))
    events=[json.loads(x,parse_float=D) for x in audit.read_text(encoding='utf-8-sig').splitlines()]
    source=ROOT/'prepared'/(symbol+'-raw.json')
    expected=indicator.calculate(read(source))
    bytime={stamp(row['begin']):row for row in expected}
    candles=[e for e in events if e['kind']=='candle_after_source_handler']
    closed=[e for e in events if e['kind']=='position_closed']
    opened={str(e['data']['Number']):e for e in events if e['kind']=='position_opened'}
    fills=defaultdict(list)
    for event in events:
        if event['kind']=='fill':
            fills[str(event['data']['NumberOrderParent'])].append(event)
    errors=[];trades=[];total=D(0);peak=D(0);max_dd=D(0);peak_position=0;max_dd_info=None
    def require(condition,message,position):
        if not condition:errors.append({'position':position,'error':message})
    for close_event in closed:
        position=close_event['data'];number=position['Number'];side=position['Direction']
        require(len(position['OpenOrders'])==len(position['CloseOrders'])==1,'Expected one entry and one exit order',number)
        entry_order=position['OpenOrders'][0];exit_order=position['CloseOrders'][0]
        entry_list=fills[str(entry_order['NumberMarket'])];exit_list=fills[str(exit_order['NumberMarket'])]
        require(len(entry_list)==len(exit_list)==1,'Expected one fill per order',number)
        entry_event=entry_list[0];exit_event=exit_list[0]
        entry_fill=entry_event['data'];exit_fill=exit_event['data']
        require(str(entry_fill['NumberPosition'])==str(exit_fill['NumberPosition'])==str(number),'Order/position identifiers differ',number)
        require(D(str(entry_fill['Volume']))==D(str(exit_fill['Volume']))==1,'Fill quantity not one',number)
        require(entry_fill['Side']==side and exit_fill['Side']!=side,'Fill side mismatch',number)
        require(entry_fill['Price']==position['EntryPrice'] and exit_fill['Price']==position['ClosePrice'],'Fill/position price mismatch',number)
        signal=bytime[stamp(entry_order['TimeCreate'])];idx=signal['index']
        require(signal[code+('_raw_buy' if side=='Buy' else '_raw_sell')],'Entry violates independently computed raw condition',number)
        next_candle=expected[idx+1]
        require(stamp(entry_fill['Time'])==stamp(next_candle['begin']),'Entry time is not next observed H1',number)
        require(D(str(entry_fill['Price']))==next_candle['ohlcv']['open'],'Entry price is not next observed H1 open',number)
        require(entry_event['sequence']>next(e['sequence'] for e in candles if stamp(e['data']['source_begin'])==stamp(signal['begin'])),'Entry fill event preceded source candle observer',number)
        item={'position':number,'side':side,'entry_signal':selected_row(signal),
              'entry_order':entry_order,'entry_fill_event':entry_event,'entry_position_time_open':position['TimeOpen'],
              'entry_is_next_observed_h1_open':True,'exit_order':exit_order,'exit_fill_event':exit_event,
              'stop_at_entry_event':opened[str(number)]['data']['StopOrderIsActive']}
        if code=='A':
            exit_signal=bytime[stamp(exit_order['TimeCreate'])]
            require(exit_signal['A_close_long_condition' if side=='Buy' else 'A_close_short_condition'],'Exit violates independently computed line-order condition',number)
            next_exit=expected[exit_signal['index']+1]
            require(stamp(exit_fill['Time'])==stamp(next_exit['begin']),'Exit time is not next observed H1',number)
            require(D(str(exit_fill['Price']))==next_exit['ohlcv']['open'],'Exit price is not next observed H1 open',number)
            item['exit_signal']=selected_row(exit_signal)
            require(not item['stop_at_entry_event'],'Unexpected initial stop in A',number)
        else:
            item['entry_previous_candle']=selected_row(expected[idx-1])
            snapshots=[]
            for event in candles:
                for observed_position in event['data']['open_positions']:
                    if observed_position['Number']==number and observed_position['State']=='Open' and observed_position['StopOrderIsActive']:
                        snapshots.append((event,observed_position))
            require(bool(snapshots),'B has no observed protective snapshot',number)
            prior_stop=None
            for event,observed_position in snapshots:
                row=bytime[stamp(event['data']['source_begin'])]
                proposed=row['B_proposed_trailing_long' if side=='Buy' else 'B_proposed_trailing_short']
                current=proposed if prior_stop is None else (max(proposed,prior_stop) if side=='Buy' else min(proposed,prior_stop))
                require(D(str(observed_position['StopOrderRedLine']))==current,'Trailing level differs from raw candle recurrence',number)
                prior_stop=current
            first,snapshot=snapshots[0]
            first_row=bytime[stamp(first['data']['source_begin'])]
            item['first_stop_snapshot']={'sequence':first['sequence'],'native_clock':first['native_clock'],
                'source_begin':first_row['begin'],'nominal_close_msk':first_row['h1_close_msk'],
                'raw_level':snapshot['StopOrderRedLine'],'ohlcv':first_row['ohlcv'],
                'nominal_unprotected_seconds_from_fill':int((datetime.fromisoformat(first_row['h1_close_msk'])-datetime.fromisoformat(entry_fill['Time'])).total_seconds())}
            require(not item['stop_at_entry_event'],'B unexpectedly protected at entry',number)
            require(stamp(first_row['begin'])==stamp(entry_fill['Time']),'First B stop is not based on fill-bar close',number)
            exit_row=bytime[stamp(exit_fill['Time'])];stop=D(str(position['StopOrderRedLine']))
            stop_inside=exit_row['ohlcv']['low']<=stop<=exit_row['ohlcv']['high']
            tick=D('.01') if symbol=='BRX6' else D('.1')
            target=(stop/tick).quantize(D(1),rounding=ROUND_HALF_EVEN)*tick if stop_inside else exit_row['ohlcv']['open']
            require(D(str(exit_fill['Price']))==target,'B fill not reconstructed by native stop-inside-bar/otherwise-open rule',number)
            adverse_gap=exit_row['ohlcv']['open']<stop if side=='Buy' else exit_row['ohlcv']['open']>stop
            favorable_vs_gap_open=(D(str(exit_fill['Price']))-exit_row['ohlcv']['open'])*(1 if side=='Buy' else -1)
            item['stop_exit_check']={'stop':stop,'last_observed_stop':prior_stop,'exit_ohlcv':exit_row['ohlcv'],
               'stop_inside_exit_h1_range':stop_inside,'adverse_gap_at_exit_open':adverse_gap,
               'fill_favorable_vs_exit_open_points':favorable_vs_gap_open if adverse_gap else None,
               'last_callback_before_close':[e['data']['source_begin'] for e in candles if e['sequence']<close_event['sequence']][-1]}
            require(exit_order['IsStopOrProfit'] is True,'B exit missing stop flag',number)
        points=(D(str(exit_fill['Price']))-D(str(entry_fill['Price'])))*(1 if side=='Buy' else -1)
        total+=points
        if total>peak:peak=total;peak_position=number
        drawdown=peak-total
        if drawdown>max_dd:max_dd=drawdown;max_dd_info={'peak_position':peak_position,'trough_position':number,'peak_closed_points':peak,'trough_closed_points':total}
        item.update({'gross_quote_points':points,'cumulative_closed_quote_points':total,'closed_curve_drawdown_quote_points':drawdown})
        trades.append(item)
    last_orders={}
    for e in events:
        if e['kind']=='order_update':last_orders[e['data']['NumberUser']]=e
    pending=[e for e in last_orders.values() if e['data']['State']=='Active']
    result={'run_id':run,'closed_trades':len(closed),'fills':sum(len(v) for v in fills.values()),
       'gross_quote_points':total,'max_closed_curve_drawdown_quote_points':max_dd,'drawdown_extrema':max_dd_info,
       'drawdown_excludes_open_trade_path':True,'monetary_result':None,'costs':None,
       'entry_next_observed_h1_open_checks':len(trades),'exit_next_observed_h1_open_checks':len(trades) if code=='A' else 0,
       'pending_active_orders':pending,'errors':errors,'audit_sha256':hashlib.sha256(audit.read_bytes()).hexdigest(),
       'source_h1_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    dump(OUT/(run+'-all-trades.json'),trades);dump(OUT/(run+'-first-three.json'),trades[:3]);summary.append(result)
dump(OUT/'execution-summary.json',summary)
print(json.dumps(summary,ensure_ascii=False,default=str))
