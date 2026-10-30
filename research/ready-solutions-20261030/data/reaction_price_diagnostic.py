"""Descriptive trade-price movement after completed H1 boundaries; no backtest."""
import ast
import csv
import gzip
import hashlib
import json
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np

ROOT = Path(__file__).parent
OUT = ROOT / 'reaction_diagnostic'
OUT.mkdir(exist_ok=True)
ARCHIVE = ROOT / 'source_2026.zip'
EXPECTED = 'b8344fbe990dd1ae069ceef3ae0709ca12375a7205a26f1360e3ba34b66b1ffb'
assert hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() == EXPECTED
COLLECTOR = ROOT / 'collector_source_at_run.yml'
source = COLLECTOR.read_text()
assert 'MSK = timezone(timedelta(hours=3))' in source
assert 'Convert official ISS timestamps into naive Moscow exchange time.' in source
literal = re.search(r'^\s+FROZEN_CONTRACTS = (\[.*\])$',source,re.M).group(1)
contracts = {r['secid']:r for r in ast.literal_eval(literal)}
MSK = timezone(timedelta(hours=3))
TICKS = {'BR':0.01, 'GOLD':0.1}  # Reporting assumptions, not verified historical tick schedules.
DELAY_SECONDS = (120,300)

def stamp(raw):
    d = datetime.fromisoformat(raw)
    return d.astimezone(MSK) if d.tzinfo else d.replace(tzinfo=MSK)

def valid(row):
    try:
        a = [float(row[k]) for k in ('open','high','low','close','volume')]
        return (all(np.isfinite(a)) and a[2] > 0 and a[4] >= 0
                and a[2] <= min(a[0],a[3]) <= max(a[0],a[3]) <= a[1])
    except (KeyError,ValueError,TypeError):
        return False

observations = []
outcomes = []
scope = []
verified_members = []
with zipfile.ZipFile(ARCHIVE) as z:
    manifest = json.loads(z.read('manifest.json'))
    for window in manifest['history']:
        code = window['contract']
        frozen = contracts[code]
        lower = max(stamp('2026-01-01'), stamp(frozen['active_start']))
        upper = min(stamp(manifest['study_end_exclusive']), stamp(frozen['active_end']))
        if window.get('status') != 'ACQUIRED':
            scope.append({'contract':code,'asset':frozen['asset'],'status':'SOURCE_WINDOW_NOT_ACQUIRED',
                          'active_start':lower.isoformat(),'active_end_exclusive':upper.isoformat(),
                          'positive_active_interval':lower < upper,'source_error':window.get('error')})
            continue
        lower = max(lower,stamp(window['first']))
        upper = min(upper,stamp(window['end_exclusive']))
        loaded = {}
        for item in window['files']:
            raw = gzip.decompress(z.read(item['file']))
            digest = hashlib.sha256(raw).hexdigest()
            assert digest == item['uncompressed_sha256'], item['file']
            rows = json.loads(raw)
            assert len(rows) == item['rows']
            key = 'M1' if '-M1.' in item['file'] else 'H1'
            loaded[key] = rows
            verified_members.append({'file':item['file'],'rows':len(rows),'uncompressed_sha256':digest})
        minute = {stamp(r['begin']):r for r in loaded['M1']}
        assert len(minute) == len(loaded['M1']), code
        boundaries = []
        for h in loaded['H1']:
            t = stamp(h['begin']) + timedelta(hours=1)
            assert t.minute == 0 and t.second == 0 and t.microsecond == 0, (code,t)
            if lower <= t < upper:
                boundaries.append((t,h))
        assert len({t for t,_ in boundaries}) == len(boundaries)
        scope.append({'contract':code,'asset':frozen['asset'],'status':'INCLUDED',
                      'active_start':lower.isoformat(),'active_end_exclusive':upper.isoformat(),
                      'candidate_H1_boundaries':len(boundaries)})
        for t,h in boundaries:
            for delay in DELAY_SECONDS:
                end = t + timedelta(seconds=delay)
                base = {'asset':frozen['asset'],'contract':code,'month':t.strftime('%Y-%m'),
                        'delay_seconds':delay,'t_msk':t.isoformat(),'end_msk':end.isoformat()}
                reason = None
                if not valid(h) or float(h['volume']) <= 0:
                    reason = 'INVALID_OR_ZERO_VOLUME_PRECEDING_H1'
                elif end >= upper:
                    reason = 'END_OUTSIDE_FROZEN_ACTIVE_WINDOW'
                elif end.date() != t.date():
                    reason = 'CROSSES_MOSCOW_DATE'
                elif t not in minute:
                    reason = 'MISSING_EXACT_START_M1'
                elif end not in minute:
                    reason = 'MISSING_EXACT_END_M1'
                elif not valid(minute[t]) or not valid(minute[end]):
                    reason = 'INVALID_ENDPOINT_OHLCV'
                elif float(minute[t]['volume']) <= 0 or float(minute[end]['volume']) <= 0:
                    reason = 'ZERO_VOLUME_ENDPOINT'
                else:
                    interior = [t + timedelta(minutes=i) for i in range(delay//60+1)]
                    if any(q not in minute for q in interior):
                        reason = 'INTERVENING_M1_GAP'
                    elif any(not valid(minute[q]) for q in interior):
                        reason = 'INVALID_INTERVENING_M1'
                outcomes.append({**base,'status':reason or 'INCLUDED'})
                if reason:
                    continue
                start_price = float(minute[t]['open'])
                end_price = float(minute[end]['open'])
                delta = round(abs(end_price-start_price),10)
                observations.append({**base,'start_trade_open':start_price,'end_trade_open':end_price,
                                     'abs_change_quote_units':delta,
                                     'assumed_tick_size':TICKS[frozen['asset']],
                                     'abs_change_assumed_ticks':round(delta/TICKS[frozen['asset']],8)})

def write_csv(path, rows):
    assert rows
    keys = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)

write_csv(OUT / 'observations.csv', observations)
write_csv(OUT / 'boundary_outcomes.csv', outcomes)
write_csv(OUT / 'contract_scope.csv', scope)

summary = []
for kind, fields in [('asset',['asset','delay_seconds']),
                     ('month',['asset','delay_seconds','month']),
                     ('contract',['asset','delay_seconds','contract']),
                     ('contract_month',['asset','delay_seconds','contract','month'])]:
    groups = defaultdict(list)
    groups_all = defaultdict(list)
    for row in observations:groups[tuple(row[k] for k in fields)].append(row)
    for row in outcomes:groups_all[tuple(row[k] for k in fields)].append(row)
    for key, all_rows in sorted(groups_all.items()):
        rows=groups.get(key,[])
        record={'scope':kind,**dict(zip(fields,key)), 'n_candidates':len(all_rows),
                'n':len(rows),'n_excluded':len(all_rows)-len(rows),
                'exclusion_counts':json.dumps(dict(Counter(r['status'] for r in all_rows if r['status']!='INCLUDED')),sort_keys=True)}
        for suffix,col in [('quote_units','abs_change_quote_units'),('assumed_ticks','abs_change_assumed_ticks')]:
            vals=np.array([r[col] for r in rows],dtype=float)
            for label,q in [('median',.5),('p90',.90),('p95',.95),('max',1.)]:
                record[label+'_'+suffix]=float(np.quantile(vals,q,method='linear')) if len(vals) else None
        summary.append(record)
write_csv(OUT / 'summary.csv',summary)

keys = [(r['asset'],r['delay_seconds'],r['t_msk']) for r in observations]
assert len(set(keys)) == len(keys), 'Overlapping active series double-counted'
metadata={'schema':1,'analysis':'DESCRIPTIVE_TRADE_PRICE_MOVEMENT_NOT_BACKTEST',
          'source_run':40494996964,'source_artifact_id':12735884135,'source_zip_sha256':EXPECTED,
          'collector_source_commit':'5956a722b22e71f342801d01c0fb28e0255684d8',
          'collector_source_blob':'e7ca5b8bf52425594d0cea9329453b808e995b5c',
          'collector_file_sha256':hashlib.sha256(COLLECTOR.read_bytes()).hexdigest(),
          'timezone':'UTC+03:00 Moscow',
          'timezone_evidence':'collector MSK definition at92 and timestamp parser at174–176',
          'population':'Observed completed positive-volume H1 boundaries within intersection of frozen active window and acquired window in2026; all observed sessions, no fabricated calendar',
          'endpoint_definition':'open of exact M1 at t and t+delay; t=H1.begin+60min, not H1.end',
          'continuity':'Every intervening M1 exists and has valid OHLCV; endpoints volume>0; same Moscow date and frozen active contract',
          'tick_status':'ASSUMED_UNVERIFIED_HISTORICAL_SCHEDULE','tick_assumptions':TICKS,
          'quantile_method':'numpy.quantile linear','accepted_observations':len(observations),
          'candidate_outcomes':len(outcomes),'verified_members':verified_members,
          'scope':scope,'output_files':[]}
for name in ['observations.csv','boundary_outcomes.csv','contract_scope.csv','summary.csv']:
    p=OUT/name
    metadata['output_files'].append({'file':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size})
(OUT / 'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
print(json.dumps({'asset_summary':[r for r in summary if r['scope']=='asset'],
                  'scope':scope,'accepted':len(observations)},ensure_ascii=False))
