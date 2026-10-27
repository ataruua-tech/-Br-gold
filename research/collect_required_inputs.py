"""Acquire remaining research inputs via APIM. Never infer executable quotes.

This is acquisition and coverage inspection only, with no strategy fitting.
"""
import math
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from load_acquisition_definitions import load_acquisition

ac = load_acquisition()


def input_rows(client, path, section, query):
    rows, fingerprints, offset = [], set(), 0
    for _ in range(1000):
        payload = client.request(path, {**query, 'start': offset})
        part = ac.bg.table_rows(payload, section)
        if not part:
            return rows
        digest = ac.hashlib.sha256(ac.acquisition_bytes(part)).hexdigest()
        if digest in fingerprints:
            raise ValueError('Repeated page; complete acquisition not established')
        fingerprints.add(digest)
        rows.extend(part)
        offset += len(part)
    raise ac.AcquisitionStop('Pagination budget exceeded')


def validate_panel(rows, contract, first, end, metric):
    unique = {}
    duplicate_count = 0
    for row in rows:
        stamp = ac.bg.parse_iss_time(row['tradedate'] + 'T' + row['tradetime'])
        if not first <= stamp.date() < end or row['secid'] != contract['secid'] or row['asset_code'] != contract['asset']:
            raise ValueError('Returned panel outside requested contract/asset/date range')
        for key in ('vol', 'trades', 'val', 'im', 'mid_price', 'spread_l1'):
            value = row.get(key)
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ValueError('Invalid panel numeric field ' + key)
        if row.get('SYSTIME'):
            ac.bg.parse_iss_time(row['SYSTIME'])
        key = (row['secid'], row['tradedate'], row['tradetime'])
        if key in unique:
            if unique[key] != row:
                raise ValueError('Conflicting panel duplicate')
            duplicate_count += 1
        unique[key] = row
    result = [unique[k] for k in sorted(unique)]
    coverage = {'metric': metric, 'rows': len(result), 'identical_duplicates_removed': duplicate_count,
                'first': result[0]['tradedate'] + 'T' + result[0]['tradetime'] if result else None,
                'last': result[-1]['tradedate'] + 'T' + result[-1]['tradetime'] if result else None,
                'historical_executable_BBO': False, 'calendar_completeness_verified': False}
    if metric == 'tradestats':
        coverage['im_positive_rows'] = sum(isinstance(r.get('im'), (int, float)) and r['im'] > 0 for r in result)
        coverage['im_null_rows'] = sum(r.get('im') is None for r in result)
        coverage['im_zero_rows'] = sum(r.get('im') == 0 for r in result)
    if metric == 'obstats':
        coverage['spread_nonnull_rows'] = sum(r.get('spread_l1') is not None for r in result)
        coverage['interpretation'] = 'Five-minute aggregate mid and spread; not an observed executable bid/offer at a decision time'
    return result, coverage


def catalogue_probe(client):
    report = []
    specs = []
    for year in range(2023, 2028):
        query = {'year': year, 'from': f'{year}-01-01', 'till': f'{year}-12-31', 'iss.meta': 'off'}
        for name, path in [('off-days', '/iss/calendars/futures.json'),
                           ('session-schedule', '/iss/calendars/futures/session.json'),
                           ('contract-catalogue', '/iss/calendars/futures/securities.json')]:
            specs.append((f'{name}-{year}', path, query))
    for code, day in [('BRG3', '2023-01-31'), ('BRG4', '2024-01-31'),
                      ('BRG5', '2025-01-31'), ('GDH5', '2025-03-20')]:
        specs.append((f'daily-history-{code}', '/iss/history/engines/futures/markets/forts/boards/rfud/securities/' + code + '.json',
                      {'from': day, 'till': day, 'iss.meta': 'off'}))
        specs.append((f'contract-description-{code}', '/iss/securities/' + code + '.json', {'iss.meta': 'off'}))
    for asset in ['BR', 'GOLD']:
        specs.append((f'universe-filter-{asset}', '/iss/datashop/algopack/fo/tradestats.json',
                      {'from': '2025-01-31', 'till': '2025-01-31', 'asset_code': asset, 'iss.meta': 'off'}))
    specs.append(('daily-market-universe', '/iss/history/engines/futures/markets/forts/securities.json',
                  {'date': '2025-01-31', 'iss.meta': 'off'}))
    for name, path, query in specs:
        record = {'name': name, 'path': path, 'query': query}
        try:
            payload = client.request(path, query, optional=True)
            record['file'] = client.save(name + '.json.gz', payload)
            blocks = {}
            for key, block in payload.items():
                if isinstance(block, dict) and isinstance(block.get('columns'), list) and isinstance(block.get('data'), list):
                    values = ac.bg.table_rows(payload, key)
                    blocks[key] = {'columns': block['columns'], 'rows_in_this_page': len(values), 'first_rows': values[:2], 'last_rows': values[-2:]}
                    if key == 'data' and name.startswith('universe-filter'):
                        blocks[key]['returned_assets'] = sorted(set(str(r.get('asset_code')) for r in values))
            record.update(status='RECEIVED_NOT_YET_ADJUDICATED', blocks=blocks)
        except ac.AcquisitionStop:
            raise
        except Exception as exc:
            record.update(status='UNAVAILABLE', error=ac.bg.safe_error(exc))
        report.append(record)
        client.save('catalogue-progress.json.gz', report)
    return report


def acquire_panels(client, contracts, year):
    report = []
    start = date(year, 1, 1)
    study_end = min(date(year + 1, 1, 1), date(2026, 10, 26))
    for contract in contracts:
        first = max(start, date.fromisoformat(contract['active_start']))
        end = min(study_end, date.fromisoformat(contract['active_end']) + timedelta(days=7),
                  date.fromisoformat(contract['expires']) + timedelta(days=1))
        if first >= end:
            continue
        for metric in ('tradestats', 'obstats'):
            record = {'contract': contract['secid'], 'asset': contract['asset'], 'metric': metric,
                      'first': first.isoformat(), 'end_exclusive': end.isoformat()}
            try:
                path = '/iss/datashop/algopack/fo/' + metric + '/' + contract['secid'] + '.json'
                rows = input_rows(client, path, 'data', {'from': first.isoformat(), 'till': (end - timedelta(days=1)).isoformat(), 'iss.meta': 'off'})
                rows, coverage = validate_panel(rows, contract, first, end, metric)
                record.update(status='ACQUIRED_ROWS' if rows else 'SOURCE_EMPTY', coverage=coverage,
                              file=client.save(contract['secid'] + '-' + metric + '.json.gz', rows))
            except ac.AcquisitionStop:
                raise
            except Exception as exc:
                record.update(status='ERROR', error=ac.bg.safe_error(exc))
            report.append(record)
            client.save('panels-progress.json.gz', report)
    return report


def required_inputs_main(contracts, directory=Path('required-input-evidence')):
    report = {'source': 'apim.moex.com', 'orders_enabled': False, 'strategy_changed': False,
              'fitting_or_backtesting_performed': False, 'all_required_inputs_ready': False,
              'exact_agent_replay_ready': False, 'mode': os.environ.get('INPUT_MODE', 'catalogue'),
              'year': int(os.environ.get('INPUT_YEAR', '2026')), 'errors': [], 'records': [],
              'scope': 'Calendar capability and frozen study-window aggregates; full historical contract-selection universe is not established by these panels',
              'started_at_utc': datetime.now(timezone.utc).isoformat()}
    client = ac.Acquisition(directory, spacing=1.0, max_requests=5000)
    try:
        if not os.environ.get('MOEX_ALGOPACK_TOKEN', '').strip():
            raise ac.AcquisitionStop('Missing protected MOEX_ALGOPACK_TOKEN')
        if report['mode'] == 'catalogue':
            report['records'] = catalogue_probe(client)
        elif report['mode'] == 'panels' and report['year'] in (2023, 2024, 2025, 2026):
            report['records'] = acquire_panels(client, contracts, report['year'])
        else:
            raise ValueError('Unknown bounded input-acquisition mode/year')
        if any(r['status'] == 'ERROR' for r in report['records']):
            report['errors'].append('Some panel requests failed; inspect partial files')
        report['status'] = 'ACQUISITION_FINISHED_REQUIRES_COVERAGE_REVIEW' if not report['errors'] else 'PARTIAL_ERROR'
    except Exception as exc:
        report['status'] = 'BLOCKED_OR_INCOMPLETE'
        report['errors'].append(ac.bg.safe_error(exc))
    report['requests'] = client.count
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'manifest.json').write_bytes(ac.acquisition_bytes(report))
    print(ac.acquisition_bytes({'status': report['status'], 'mode': report['mode'], 'year': report['year'],
                               'requests': client.count, 'records': len(report['records']),
                               'all_required_inputs_ready': False, 'errors': report['errors']}).decode())
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(required_inputs_main(ac.FROZEN_CONTRACTS))
