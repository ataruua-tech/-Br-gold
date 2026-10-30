"""Independently reconcile native observation logs, never simulate order fills."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path

D=Decimal

def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"),parse_float=D)

def save(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")

def stamp(value):
    return value.replace("T"," ")[:19]

def verify(root):
    results=[]
    for code,symbol in (("A","BRX6"),("A","GDZ6"),("B","BRX6"),("B","GDZ6")):
        directory=root/"runs"/f"{code}_{symbol}"
        result={"run_id":f"{code}_{symbol}","PERSONAL_EXECUTION":"NOT_RUN","MONETARY_RESULT":"NOT_ESTABLISHED","RULE_REPRODUCTION":"NOT_VERIFIED"}
        errors=[]
        try:
            status=read(directory/"controller-status.json")
            result.update(status)
            if status["controller_result"]!="NATIVE_RUN_FINISHED":
                raise ValueError(status.get("error",status))
            paths=list((directory/"ControlAudit").glob("*.jsonl"))
            assert len(paths)==1, "Expected exactly one audit log"
            events=[json.loads(line,parse_float=D) for line in paths[0].read_text(encoding="utf-8-sig").splitlines()]
            sequences=[e["sequence"] for e in events]
            if sequences!=list(range(1,len(events)+1)):
                errors.append({"error":"Audit sequence has gaps, duplicates or is out of order"})
            expected=[json.loads(line,parse_float=D) for line in (root/"prepared"/(symbol+"-expected.jsonl")).read_text(encoding="utf-8").splitlines()]
            bytime={stamp(r["begin"]):r for r in expected}
            if len(bytime)!=len(expected):
                raise ValueError("Duplicate expected source times")
            candles=[e for e in events if e["kind"]=="candle_after_source_handler"]
            observed={}
            comparisons=0
            for event in candles:
                data=event["data"]
                begin=stamp(data["source_begin"])
                row=bytime.get(begin)
                if row is None:
                    errors.append({"begin":begin,"error":"Unexpected native candle"})
                    continue
                if begin in observed:
                    errors.append({"begin":begin,"error":"Duplicate finished-candle callback"})
                observed[begin]=data
                if data["count"]!=row["index"]+1:
                    errors.append({"begin":begin,"error":"Candle count differs","count":data["count"],"expected":row["index"]+1})
                if data["regime"]!="On" or data["schedule_allowed"] is not True:
                    errors.append({"begin":begin,"error":"Off or schedule blocked"})
                for key,value in row["ohlcv"].items():
                    actual=D(str(data["candle"][key.capitalize()]))
                    if actual!=D(value): errors.append({"begin":begin,"error":"OHLCV differs","field":key})
                lookup={(i["field"],i["series"]):i for i in data["indicators"]}
                fields=[("_fastSP",2,row["fast"]["center"],D("1e-9")),("_slowSP",2,row["slow"]["center"],D("1e-9"))] if code=="A" else [("_bollinger",0,row["bollinger"]["upper"],D("1e-6")),("_bollinger",1,row["bollinger"]["lower"],D("1e-6")),("_RSI",0,row["rsi"],D("0.01"))]
                for field,index,value,tolerance in fields:
                    actual=D(str(lookup[field,index]["value"]))
                    comparisons+=1
                    if lookup[field,index]["count"]!=data["count"]:
                        errors.append({"begin":begin,"error":"Indicator length differs from candle count","field":field,"series":index})
                    if not actual.is_finite() or abs(actual-D(value))>tolerance:
                        errors.append({"begin":begin,"error":"Indicator mismatch","field":field,"actual":str(actual),"expected":str(value),"tolerance":str(tolerance)})
                    if code=="B" and field=="_bollinger" and row["index"]>0:
                        prior=D(expected[row["index"]-1]["bollinger"]["upper" if index==0 else "lower"])
                        observed_prior=D(str(lookup[field,index]["previous"]))
                        if not observed_prior.is_finite() or abs(observed_prior-prior)>tolerance:
                            errors.append({"begin":begin,"error":"Previous Bollinger value mismatch","series":index,"actual":str(observed_prior),"expected":str(prior)})
                if code=="A":
                    fast=D(str(lookup["_fastSP",2]["value"]));slow=D(str(lookup["_slowSP",2]["value"]));close=D(row["ohlcv"]["close"])
                    buy=data["count"]>=60 and close>fast>slow
                    sell=data["count"]>=60 and close<fast<slow
                else:
                    buy=(data["count"]>=21 and row["index"]>0 and D(expected[row["index"]-1]["ohlcv"]["low"])<D(str(lookup["_bollinger",1]["previous"])) and D(row["ohlcv"]["low"])>D(str(lookup["_bollinger",1]["value"])) and D(str(lookup["_RSI",0]["value"]))<30)
                    sell=(data["count"]>=21 and row["index"]>0 and D(expected[row["index"]-1]["ohlcv"]["high"])>D(str(lookup["_bollinger",0]["previous"])) and D(row["ohlcv"]["high"])<D(str(lookup["_bollinger",0]["value"])) and D(str(lookup["_RSI",0]["value"]))>70)
                if buy!=row[code+"_raw_buy"] or sell!=row[code+"_raw_sell"]:
                    errors.append({"begin":begin,"error":"Exact raw-condition mismatch","actual_buy":buy,"actual_sell":sell})
            missing=[t for t in bytime if t not in observed]
            if missing: errors.append({"error":"Native did not process all imported H1","missing_count":len(missing),"missing":missing})
            if not candles: errors.append({"error":"No processed candles"})
            opened={e["data"]["Number"]:e for e in events if e["kind"]=="position_opened"}
            closed={e["data"]["Number"]:e for e in events if e["kind"]=="position_closed"}
            for kind,unique in (("position_opened",opened),("position_closed",closed)):
                if sum(e["kind"]==kind for e in events)!=len(unique):
                    errors.append({"error":"Duplicate position lifecycle event","kind":kind})
            if set(closed)-set(opened):
                errors.append({"error":"Closed positions missing entry observations","positions":sorted(set(closed)-set(opened))})
            trades=[]
            for number,entry in opened.items():
                pos=entry["data"]
                if D(str(pos["MaxVolume"]))!=1: errors.append({"error":"Not one contract","position":number})
                exit_event=closed.get(number)
                previous=[e for e in candles if e["sequence"]<entry["sequence"]]
                following=[e for e in candles if e["sequence"]>entry["sequence"]]
                # The observer's candle handler runs AFTER the source handler.
                # Synchronous order fills can therefore precede the same-bar
                # observation. Neither adjacent callback proves the signal bar.
                before=previous[-1] if previous else None
                after=following[0] if following else None
                protection=[e for e in candles if e["sequence"]>=entry["sequence"] and any(p["Number"]==number and p["StopOrderIsActive"] for p in e["data"]["open_positions"])]
                first_protection=entry if pos.get("StopOrderIsActive") is True else (protection[0] if protection else None)
                point_pnl=None
                if exit_event:
                    last=exit_event["data"]
                    entry_price=D(str(last["EntryPrice"]));exit_price=D(str(last["ClosePrice"]))
                    if (not entry_price.is_finite() or not exit_price.is_finite() or
                            entry_price<=0 or exit_price<=0 or last["Direction"] not in ("Buy","Sell")):
                        errors.append({"error":"Invalid price or direction in closed position","position":number})
                    else:
                        point_pnl=(exit_price-entry_price)*(1 if last["Direction"]=="Buy" else -1)
                    if exit_event["sequence"]<=entry["sequence"]:
                        errors.append({"error":"Close observation precedes open observation","position":number})
                trades.append({"position":number,"entry":pos,"exit":exit_event["data"] if exit_event else None,"entry_sequence":entry["sequence"],"exit_sequence":exit_event["sequence"] if exit_event else None,"last_candle_callback_before_entry_event":before,"first_candle_callback_after_entry_event":after,"signal_association_status":"NOT_ESTABLISHED_FROM_CALLBACK_ORDER","first_observed_protection_snapshot":first_protection,"protection_timing_status":"OBSERVATION_BOUND_ONLY_NOT_EXACT_STOP_SUBMISSION_TIME","gross_quote_points":point_pnl,"economic_costs":"NOT_MODELLED","status":"CLOSED" if exit_event else "OPEN_UNRESOLVED"})
            invalid_closed=sum(t["status"]=="CLOSED" and t["gross_quote_points"] is None for t in trades)
            result.update({"source_bars":len(expected),"processed_candle_callbacks":len(candles),"unique_processed_bars":len(observed),"indicator_comparisons":comparisons,"entry_positions":len(opened),"closed_positions":len(closed),"open_unresolved":len(set(opened)-set(closed)),"fill_events":sum(e["kind"]=="fill" for e in events),"order_update_events":sum(e["kind"]=="order_update" for e in events),"cancelled_order_updates":sum(e["kind"]=="order_update" and e["data"].get("State")=="Cancel" for e in events),"gross_closed_quote_points":sum((t["gross_quote_points"] or D(0) for t in trades),D(0)),"data_first":expected[0]["begin"],"data_last":expected[-1]["begin"],"audit_sha256":hashlib.sha256(paths[0].read_bytes()).hexdigest(),"native_report_is_not_rub_result":True,"verification_errors":errors})
            if invalid_closed or set(closed)-set(opened):
                result["gross_closed_quote_points"]=None
            data_error_types={"Unexpected native candle","Duplicate finished-candle callback","Candle count differs","OHLCV differs"}
            import_ok=not missing and candles and not any(e["error"] in data_error_types for e in errors)
            result["DATA_IMPORT"]="PASS_ALL_ROWS_OBSERVED" if import_ok else "FAIL_OR_PARTIAL_NATIVE_OBSERVATION"
            result["RULE_REPRODUCTION"]="PASS_NATIVE_INDICATORS_AND_RAW_CONDITIONS_MANUAL_TRACE_REQUIRED" if not errors else "FAIL_OR_PARTIAL_SEE_ERRORS"
            result["ENTRY_EXIT_EXECUTION_RECONCILIATION"]="MANUAL_TRACE_REQUIRED"
            result["PERSONAL_EXECUTION"]="NOT_RUN"
            result["MONETARY_RESULT"]="NOT_ESTABLISHED"
            result["economic_costs_rub"]=None
            result["net_profit_rub"]=None
            result["native_clock_semantics"]="REQUIRES_SEPARATE_CHECK_NOT_ASSUMED_EQUAL_H1_CLOSE"
            save(directory/"trade-traces.json",trades)
            save(directory/"first-three-traces.json",trades[:3])
        except Exception as exc:
            result["verification_exception"]=str(exc)
        save(directory/"verification.json",result)
        results.append(result)
    save(root/"control-summary.json",results)
    print("CONTROL_SUMMARY "+json.dumps(results,ensure_ascii=False,default=str))
    return results

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    verify(a.output)
