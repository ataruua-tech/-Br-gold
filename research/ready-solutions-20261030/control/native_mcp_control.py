"""Four fixed OsEngine control runs through its native tester MCP API.

No strategy/indicator/order matching engine is implemented here. This controller
only imports existing H1 files, sets frozen parameters, starts native TesterLight,
and preserves responses. It never connects a broker or calls order-entry APIs.
Requires an already obtained official OsEngine binary directory and BRX6.txt /
GDZ6.txt data. Optional Audit* subclasses add read-only native event observation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import traceback
import urllib.error
import urllib.request


STRATEGIES = {
    "A": ("IntersectionOfSuperTrends", {
        "Length Fast SP": 10, "Fast SP Deviation": 1,
        "Type Fast Price": "Median", "Length Slow SP": 50,
        "Slow SP Deviation": 1, "Type Slow Price": "Median",
    }),
    "B": ("CountertrendBollingerRSI", {
        "Bollinger Length": 21, "Bollinger Deviation": 1,
        "RSI Length": 14, "Stop Value": 1,
    }),
}
ALLOWED = frozenset({
    "ping", "terminal_get_status", "terminal_stop", "bot_get_list",
    "bot_create", "bot_get_params", "bot_set_params", "bot_get_sources",
    "bot_get_config_tab_simple", "bot_set_config_tab_simple",
    "bot_get_position_support", "bot_set_position_support",
    "tester_data_get_config", "tester_data_set_config", "tester_get_securities",
    "tester_execution_get_config", "tester_execution_set_config",
    "tester_portfolio_get_config", "tester_portfolio_set_config",
    "tester_charges_get_config", "tester_charges_set_config",
    "tester_start", "tester_get_status", "tester_get_report", "tester_stop",
})


def save(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path: Path):
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


class Mcp:
    def __init__(self, port: int, transcript: Path):
        self.url = f"http://localhost:{port}/api/v2/mcp"
        self.session = None
        self.sequence = 0
        self.transcript = transcript
        # A public documented test default, not a user credential.
        self.headers = {"X-Api-Key": "osengine-mcp-default-key",
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json"}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def rpc(self, method, params=None, notification=False):
        self.sequence += 1
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notification:
            body["id"] = self.sequence
        headers = dict(self.headers)
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers)
        stamp = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            with self.opener.open(request, timeout=90) as response:
                if response.headers.get("Mcp-Session-Id"):
                    self.session = response.headers["Mcp-Session-Id"]
                raw = response.read().decode("utf-8-sig")
                status = response.status
            if raw.startswith("event:") or raw.startswith("data:"):
                messages = [json.loads(line[5:].strip()) for line in raw.splitlines()
                            if line.startswith("data:") and line[5:].strip()]
                result = next((x for x in messages if x.get("id") == body.get("id")), {})
            else:
                result = json.loads(raw) if raw else {}
            record = {"at_utc": stamp, "request": body, "http_status": status, "response": result}
            with self.transcript.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            if "error" in result:
                raise RuntimeError(json.dumps(result["error"], ensure_ascii=False))
            return result.get("result", {})
        except Exception as exc:
            with self.transcript.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"at_utc": stamp, "request": body,
                                         "exception": str(exc)}, ensure_ascii=False) + "\n")
            raise

    def initialize(self):
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "br-gold-isolated-control", "version": "1"}})
        if not self.session:
            raise RuntimeError("Native MCP did not return a session id")
        self.rpc("notifications/initialized", notification=True)

    def call(self, name, arguments=None):
        if name not in ALLOWED:
            raise ValueError(f"Not allowed by tester-only controller: {name}")
        arguments = arguments or {}
        if "server_type" in arguments and arguments["server_type"] != "Tester":
            raise ValueError("Only Tester server is permitted")
        payload = self.rpc("tools/call", {"name": name, "arguments": arguments})
        if payload.get("isError"):
            raise RuntimeError(json.dumps(payload, ensure_ascii=False))
        text = "\n".join(x.get("text", "") for x in payload.get("content", []) if x.get("type") == "text")
        if not text:
            return payload
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


def source_rows(path):
    rows = [line for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    stamps = [dt.datetime.strptime(",".join(line.split(",")[:2]), "%Y%m%d,%H%M%S") for line in rows]
    if not rows or stamps != sorted(set(stamps)):
        raise ValueError(f"Empty, duplicated or unsorted input: {path}")
    return {"rows": len(rows), "first_begin_msk": stamps[0].isoformat(),
            "last_begin_msk": stamps[-1].isoformat(),
            "last_information_available_msk": (stamps[-1] + dt.timedelta(hours=1)).isoformat(),
            "sha256": digest(path)}


def prepare_runtime(base, runtime, bot_name):
    if runtime.exists():
        raise FileExistsError(f"Refusing to overwrite an earlier attempt: {runtime}")
    # User configuration, data and logs must never be inherited.
    excluded = {"Engine", "Data", "Logs", "Log", "Audit", "ControlAudit", "Settings"}
    shutil.copytree(base, runtime, ignore=lambda folder, names: [n for n in names if n in excluded])
    engine = runtime / "Engine"
    engine.mkdir(exist_ok=True)
    (engine / "ServerMaster.txt").write_text("False\n", encoding="utf-8")
    days = "True@" * 7
    period = "False@0:0:0:0@0:0:0:0@" * 5 + "@@@@@"
    (engine / (bot_name + "nonTradePeriod.txt")).write_text(
        days + "\n" + "\n".join([period] * 8) + "\n", encoding="utf-8")


def run_one(args, code, symbol, ordinal):
    run_id = f"{code}_{symbol}"
    destination = args.output_dir / run_id
    destination.mkdir(parents=True, exist_ok=False)
    runtime = args.runtime_root / run_id
    name = f"Control_{code}_{symbol}"
    strategy, specific = STRATEGIES[code]
    if args.audit:
        strategy = "Audit" + strategy
    prepare_runtime(args.engine_dir, runtime, name)
    port = args.port + ordinal
    (runtime / "Engine" / "McpSettings.txt").write_text(
        f"{port}\nosengine-mcp-default-key\nTrue\nFalse\n127.0.0.1|any;::1|any\n", encoding="utf-8")
    # Each run imports only its own dated contract; no joining or substitution.
    data_folder = runtime / "ControlData"
    data_folder.mkdir()
    source = args.data_dir / (symbol + ".txt")
    shutil.copy2(source, data_folder / source.name)
    data_info = source_rows(source)
    params = {"Regime": "On", "Order type": "Market", "Slippage %": 0,
              "Volume type": "Contracts", "Volume": 1, **specific}
    frozen = {"run_id": run_id, "strategy": strategy, "instance_name": name,
              "params": params, "data": data_info, "source_commit": args.source_commit,
              "all_observed_bars_enabled": True, "native_order_execution": "Touch",
              "slippage_simple": 0, "slippage_stop": 0,
              "commission": "NOT_MODELLED_AUTHOR_CONTROL", "capital_rub_evaluation": False,
              "personal_execution": "NOT_RUN", "monetary_result": "NOT_ESTABLISHED",
              "engine_dll_sha256": digest(runtime / "OsEngine.dll"),
              "script_sha256": digest(runtime / "Custom" / "Robots" / (strategy + ".cs"))}
    save(destination / "parameters.before-run.json", frozen)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    stdout = (destination / "native.stdout.log").open("w", encoding="utf-8")
    process = subprocess.Popen([str(runtime / "OsEngine.exe"), "-testerlight"],
                               cwd=runtime, stdout=stdout, stderr=subprocess.STDOUT, creationflags=flags)
    mcp = Mcp(port, destination / "mcp-transcript.jsonl")
    status = {"run_id": run_id, "controller_result": "FAILED",
              "COMPILATION": "NOT_RUN", "DATA_IMPORT": "NOT_RUN",
              "RULE_REPRODUCTION": "REQUIRES_EVENT_VERIFICATION",
              "PERSONAL_EXECUTION": "NOT_RUN", "MONETARY_RESULT": "NOT_ESTABLISHED"}
    try:
        deadline = time.monotonic() + args.start_timeout
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"Native process exited during startup: {process.returncode}")
            try:
                mcp.initialize()
                terminal = mcp.call("terminal_get_status")
                if "IsTester" not in json.dumps(terminal):
                    raise RuntimeError("The native terminal is not in IsTester mode")
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(2)
        save(destination / "terminal.json", terminal)
        save(destination / "initial-bots.json", mcp.call("bot_get_list"))
        data_config = {"source_type": "Folder", "folder_path": str(data_folder),
                       "type_tester_data": "Candle", "date_from": data_info["first_begin_msk"],
                       "date_to": data_info["last_information_available_msk"],
                       "delete_trades_from_memory": True}
        mcp.call("tester_data_set_config", data_config)
        deadline = time.monotonic() + 120
        while True:
            securities = mcp.call("tester_get_securities")
            if any(x.get("name") == source.name for x in securities.get("securities", [])):
                break
            if time.monotonic() > deadline:
                raise RuntimeError("The requested dated contract was not imported")
            time.sleep(2)
        save(destination / "securities.json", securities)
        save(destination / "data-config.json", mcp.call("tester_data_get_config"))
        status["DATA_IMPORT"] = "NATIVE_SECURITY_LOADED_ROWS_REQUIRE_OBSERVER_VERIFICATION"
        mcp.call("tester_execution_set_config", {"slippage_to_simple_order": 0,
                   "slippage_to_stop_order": 0, "order_execution_type": "Touch", "non_trade_periods": []})
        mcp.call("tester_portfolio_set_config", {"start_portfolio": 100000,
                                                "portfolio_calculation_enabled": False})
        mcp.call("tester_charges_set_config", {"dividends_is_on": False,
                                              "taxes_is_on": False, "margin_regime": "Off"})
        created = mcp.call("bot_create", {"strategy_name": strategy, "name": name})
        save(destination / "created-bot.json", created)
        if created.get("name") != name:
            raise RuntimeError(f"Unexpected native robot identity: {created}")
        status["COMPILATION"] = "NATIVE_ROSLYN_SCRIPT_CREATED"
        save(destination / "parameters.initial.json", mcp.call("bot_get_params", {"bot_id": name}))
        updated = mcp.call("bot_set_params", {"bot_id": name, "parameters": params})
        save(destination / "parameter-update.json", updated)
        if updated.get("not_found_count", 0) != 0 or updated.get("updated_count") != len(params):
            raise RuntimeError(f"Frozen parameters were not all applied: {updated}")
        sources = mcp.call("bot_get_sources", {"bot_id": name})
        save(destination / "sources.json", sources)
        if sources.get("count") != 1 or sources["sources"][0].get("type") != "Simple":
            raise RuntimeError("Expected exactly one native Simple tab")
        tab = sources["sources"][0]["name"]
        target = {"bot_id": name, "tab_name": tab}
        configuration = mcp.call("bot_set_config_tab_simple", {**target,
            "server_type": "Tester", "server_full_name": "Tester", "portfolio_name": "GodMode",
            "emulator_is_on": False, "security_name": source.name, "security_class": "TestClass",
            "time_frame": "Hour1", "events_is_on": True, "candle_create_method_type": "Simple",
            "build_non_trading_candles": False, "commission_type": "None", "commission_value": 0})
        save(destination / "tab-config.json", configuration)
        # Disable external/manual automatic exit additions. Robot B's own trailing
        # stop logic remains untouched; these are separate platform facilities.
        save(destination / "position-support.initial.json", mcp.call("bot_get_position_support", target))
        support = mcp.call("bot_set_position_support", {**target, "stop_is_on": False,
            "profit_is_on": False, "second_to_open_is_on": False, "second_to_close_is_on": False,
            "setback_to_open_is_on": False, "setback_to_close_is_on": False,
            "double_exit_is_on": False})
        save(destination / "position-support.json", support)
        effective_params = mcp.call("bot_get_params", {"bot_id": name})
        save(destination / "parameters.effective.json", effective_params)
        observed_params = {row["name"]: row.get("value") for row in effective_params.get("parameters", [])}
        if any(observed_params.get(key) != value for key, value in params.items()):
            raise RuntimeError("Read-back of effective parameters differs from frozen values")
        save(destination / "execution-config.json", mcp.call("tester_execution_get_config"))
        time.sleep(10)
        deadline = time.monotonic() + 60
        while True:
            try:
                mcp.call("tester_start", {"fast_forward": True})
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(3)
        print(f"STARTED {run_id}", flush=True)
        deadline = time.monotonic() + args.test_timeout
        started = time.monotonic()
        previous = None
        while True:
            native_status = mcp.call("tester_get_status")
            progress = native_status.get("progress_percent", native_status.get("progressPercent", 0))
            now = native_status.get("time_now", native_status.get("timeNow"))
            if (progress, now) != previous:
                print(f"PROGRESS {run_id} {progress}% {now}", flush=True)
                previous = (progress, now)
            if native_status.get("regime") == "Pause" and float(progress or 0) >= 99.999:
                break
            if process.poll() is not None:
                raise RuntimeError("Native tester exited during the run")
            if time.monotonic() > deadline:
                raise TimeoutError(f"Native tester did not complete: {native_status}")
            time.sleep(2)
        time.sleep(2)  # allow native asynchronous position event dispatch to finish
        save(destination / "final-status.json", native_status)
        save(destination / "native-report.json", mcp.call("tester_get_report"))
        status["controller_result"] = "NATIVE_RUN_FINISHED"
        status["elapsed_test_seconds"] = round(time.monotonic() - started, 3)
    except Exception as exc:
        status["error"] = str(exc)
        (destination / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        try:
            mcp.call("terminal_stop")
        except Exception as exc:
            status["shutdown_mcp_error"] = str(exc)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()  # Only the isolated child started by this controller.
            process.wait(timeout=10)
            status["forced_child_shutdown"] = True
        stdout.close()
        for folder in ("Audit", "ControlAudit", "Logs", "Log"):
            if (runtime / folder).is_dir():
                shutil.copytree(runtime / folder, destination / folder)
        # Save only our isolated engine settings, excluding even the public MCP key.
        shutil.copytree(runtime / "Engine", destination / "native-settings",
                        ignore=lambda folder, names: [n for n in names if n == "McpSettings.txt"])
        save(destination / "controller-status.json", status)
    print(json.dumps(status, ensure_ascii=False), flush=True)
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--source-commit", default="09b0eefc7ac6cf5a73e461cc3a81ded9c34cd54b")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--port", type=int, default=16500)
    parser.add_argument("--start-timeout", type=int, default=180)
    parser.add_argument("--test-timeout", type=int, default=180)
    args = parser.parse_args()
    args.engine_dir = args.engine_dir.resolve()
    args.data_dir = args.data_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    args.runtime_root = args.runtime_root.resolve()
    if os.name != "nt":
        raise RuntimeError("This controller requires Windows and native OsEngine")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.runtime_root.mkdir(parents=True, exist_ok=True)
    results = []
    for i, (code, symbol) in enumerate((('A', 'BRX6'), ('A', 'GDZ6'), ('B', 'BRX6'), ('B', 'GDZ6'))):
        try:
            results.append(run_one(args, code, symbol, i))
        except Exception as exc:
            results.append({"run_id": f"{code}_{symbol}", "controller_result": "SETUP_FAILED", "error": str(exc)})
    save(args.output_dir / "controller-summary.json", results)
    raise SystemExit(0 if all(x["controller_result"] == "NATIVE_RUN_FINISHED" for x in results) else 1)


if __name__ == "__main__":
    main()
