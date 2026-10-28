"""Causal price inputs for the registered scenario study; never sends orders."""
from __future__ import annotations
import ast
import gzip
import hashlib
import json
import math
import statistics
import sys
import textwrap
import types
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from load_acquisition_definitions import load_acquisition

ROOT = Path(__file__).resolve().parents[1]
SIGNAL_BLOB = '38e147112ad48c3959b4dff6838fbec88d66f576'
PROTOCOL_SHA = 'c53b28df16ba5c978cecf1d85252d66ea5fbca3eac4e1df78cfc305b258c3fc8'


def frozen_signal_definitions():
    raw = (ROOT / '.github/workflows/algopack-h1.yml').read_bytes()
    if hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() != SIGNAL_BLOB:
        raise ValueError('Signal code differs from frozen S0')
    source = textwrap.dedent(raw.decode().split("python3 - <<'PY'\n", 1)[1].split('\n          PY\n', 1)[0])
    nodes = [n for n in ast.parse(source).body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in {'Candle','ema','atr_adx','aggregate_h4','indicator_snapshot'}]
    module = types.ModuleType('_scenario_frozen_signals')
    sys.modules[module.__name__] = module
    module.__dict__.update(datetime=datetime, timedelta=timedelta, dataclass=dataclass, statistics=statistics, math=math, Any=object)
    exec(compile(ast.Module(body=nodes,type_ignores=[]), '<frozen-signal-definitions>', 'exec'), module.__dict__)
    return module


def minute(value):
    return int(np.datetime64(value.replace(' ', 'T'), 'm').astype('int64'))


def timestamp(value):
    return str(np.datetime64(int(value), 'm'))


def day_start(value):
    return value - value % 1440


def protocol():
    raw = (ROOT / 'research/scenario-study-v2/protocol.json').read_bytes().strip()
    if hashlib.sha256(raw).hexdigest() != PROTOCOL_SHA:
        raise ValueError('Unregistered scenario protocol')
    return json.loads(raw)


def calendar_exceptions():
    evidence = json.loads((ROOT / 'research/data-audit/complete-records.json').read_text())['records']
    exceptions = {}
    for item in evidence:
        if item['type'] == 'CATALOGUE_FULL_DATA' and 'off_days' in item['record']:
            for row in item['record']['off_days']:
                exceptions[row['tradedate']] = row['is_traded']
    return exceptions


def regular_main_date(value, exceptions):
    d = date.fromisoformat(value[:10])
    return d.weekday() < 5 and exceptions.get(d.isoformat(), 1) != 0


def prior_main_dates(value, count, exceptions):
    d, found = date.fromisoformat(value[:10]), []
    while len(found) < count:
        d -= timedelta(days=1)
        if regular_main_date(d.isoformat(), exceptions): found.append(d.isoformat())
    return list(reversed(found))


def checked_normalized(path, entry):
    raw = gzip.decompress(path.read_bytes())
    if hashlib.sha256(raw).hexdigest() != entry['uncompressed_sha256']:
        raise ValueError('Normalized source hash mismatch: ' + str(path))
    rows = json.loads(raw)
    if len(rows) != entry['rows']:
        raise ValueError('Normalized source count mismatch')
    return rows


def merge_price_rows(frames):
    values = {}
    for rows in frames:
        for row in rows:
            key = row['begin']
            payload = tuple(row[k] for k in ('open','high','low','close','volume'))
            if key in values and values[key] != payload:
                raise ValueError('Revised price snapshots conflict at ' + key)
            values[key] = payload
    keys = sorted(values)
    numbers = np.asarray([values[k] for k in keys], dtype=float)
    if not len(numbers): return pd.DataFrame(columns=['open','high','low','close','volume'])
    if not np.isfinite(numbers).all() or (numbers[:,:4] <= 0).any() or (numbers[:,4] < 0).any():
        raise ValueError('Invalid OHLCV')
    if (numbers[:,1] < numbers[:,[0,2,3]].max(axis=1)).any() or (numbers[:,2] > numbers[:,[0,1,3]].min(axis=1)).any():
        raise ValueError('Invalid OHLC ordering')
    return pd.DataFrame(numbers, index=pd.DatetimeIndex(keys), columns=['open','high','low','close','volume'])


def prepare_features(frame, frozen, exceptions):
    """Reset indicators on each MSK date's85-day source window; map only completed H4."""
    if frame.empty: return frame.copy()
    output = frame.copy()
    for column in ['ema20','ema50','atr','adx','h4_ema20','h4_ema50','h1_count','h4_count']:
        output[column] = np.nan
    times = frame.index
    date_values = times.normalize()
    for day in date_values.unique():
        first = times.searchsorted(day - pd.Timedelta(days=85))
        stop = times.searchsorted(day + pd.Timedelta(days=1))
        subset = frame.iloc[first:stop]
        bars = [frozen.Candle(t.to_pydatetime(), *r) for t,r in zip(subset.index, subset.to_numpy())]
        closes = [c.close for c in bars]
        ema20 = np.array(frozen.ema(closes,20),dtype=float)
        ema50 = np.array(frozen.ema(closes,50),dtype=float)
        atr, adx = [np.array(x,dtype=float) for x in frozen.atr_adx(bars)]
        h4 = frozen.aggregate_h4(bars)
        h4_end = np.array([np.datetime64(c.begin+timedelta(hours=4),'m') for c in h4])
        e4f = np.array(frozen.ema([c.close for c in h4],20),dtype=float)
        e4s = np.array(frozen.ema([c.close for c in h4],50),dtype=float)
        selected = np.flatnonzero(subset.index.normalize() == day)
        for i in selected:
            completed = int(np.searchsorted(h4_end, np.datetime64(bars[i].begin+timedelta(hours=1),'m'), side='right'))
            output.loc[subset.index[i], ['ema20','ema50','atr','adx','h4_ema20','h4_ema50','h1_count','h4_count']] = [ema20[i],ema50[i],atr[i],adx[i],e4f[completed-1] if completed else np.nan,e4s[completed-1] if completed else np.nan,i+1,completed]
    prior = frame.shift(1)
    output['donchian_high'] = prior.high.rolling(20,min_periods=20).max()
    output['donchian_low'] = prior.low.rolling(20,min_periods=20).min()
    output['median_volume'] = prior.volume.rolling(20,min_periods=20).median()
    output['prior_mean'] = prior.close.rolling(20,min_periods=20).mean()
    output['prior_std'] = prior.close.rolling(20,min_periods=20).std(ddof=1)
    ranges = frame.high-frame.low
    multiplier = (2*frame.close-frame.high-frame.low).div(ranges.where(ranges>0)).fillna(0)
    output['cmf'] = (multiplier*frame.volume).rolling(20,min_periods=20).sum().div(frame.volume.rolling(20,min_periods=20).sum().replace(0,np.nan))
    output['prior_cmf'] = output.cmf.shift(1)
    daily_returns = {}
    for d, group in frame.groupby(frame.index.normalize()):
        by_hour = {t.hour: r for t,r in group.iterrows()}
        if all(h in by_hour for h in range(10,19)):
            daily_returns[d.date().isoformat()] = math.log(by_hour[18]['close']/by_hour[10]['open'])
    scores = {}
    for d in date_values.unique():
        dates = prior_main_dates(str(d),20,exceptions)
        if all(v in daily_returns for v in dates):
            returns = [daily_returns[v] for v in dates]
            sd = statistics.stdev(returns)
            if sd > 0: scores[d.date().isoformat()] = sum(returns)/sd
    output['relative_score'] = [scores.get(t.date().isoformat(),np.nan) for t in times]
    output['t'] = times.to_numpy(dtype='datetime64[m]').astype('int64')
    output['ready'] = (output.h1_count>=220)&(output.h4_count>=50)&(output.atr>0)&(output.median_volume>0)&np.isfinite(output[['ema20','ema50','h4_ema20','h4_ema50','atr','adx']]).all(axis=1)
    return output


@dataclass
class ContractData:
    meta: dict
    h1: pd.DataFrame
    m1: np.ndarray
    m1_times: np.ndarray


def load_dataset():
    p = protocol()
    frozen = frozen_signal_definitions()
    definitions = load_acquisition().FROZEN_CONTRACTS
    catalogue = next(r['contracts'] for r in json.loads((ROOT/'research/data-audit/full-universe-records.json').read_text()) if r['type']=='FULL_CONTRACT_CATALOGUE')
    official = {r['secid']:r for r in catalogue}
    frames = {}
    source_hashes = {}
    for year in range(2023,2027):
        root = ROOT/'research/point-in-time'/str(year)
        zip_hash = hashlib.sha256((root/'download.zip').read_bytes()).hexdigest()
        if zip_hash != p['historical_data']['yearly_zip_sha256'][str(year)]:
            raise ValueError('Original ZIP digest mismatch')
        manifest = json.loads((root/'manifest.json').read_text())
        for record in manifest['history']:
            if record['status'] != 'ACQUIRED': continue
            for entry in record['files']:
                interval = 'M1' if '-M1.' in entry['file'] else 'H1'
                rows = checked_normalized(root/entry['file'],entry)
                frames.setdefault(record['contract'],{}).setdefault(interval,[]).append(rows)
                source_hashes[str(year)+'/'+entry['file']] = entry['uncompressed_sha256']
    exceptions = calendar_exceptions()
    data = []
    coverage = []
    for definition in definitions:
        code = definition['secid']
        if code not in frames: continue
        meta = dict(definition)
        if official[code]['last_trade'] != meta['expires']:
            raise ValueError('Frozen window expiration differs from official catalogue: '+code)
        hours = merge_price_rows(frames[code]['H1'])
        minutes = merge_price_rows(frames[code]['M1'])
        features = prepare_features(hours,frozen,exceptions)
        m1times = minutes.index.to_numpy(dtype='datetime64[m]').astype('int64')
        values = minutes[['open','high','low','close','volume']].to_numpy()
        data.append(ContractData(meta,features,values,m1times))
        coverage.append({'contract':code,'asset':meta['asset'],'M1':len(minutes),'H1':len(hours),'ready_H1':int(features.ready.sum()),'active_start':meta['active_start'],'active_end':meta['active_end']})
    digest = hashlib.sha256(json.dumps(source_hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return data, {'dataset_source_hash':digest,'files':source_hashes,'coverage':coverage,'calendar_exceptions':exceptions}
