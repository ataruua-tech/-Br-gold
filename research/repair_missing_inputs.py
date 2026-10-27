"""Resume seven interrupted APIM panels from verified archived page offsets."""
import gzip
import hashlib
import io
import json
import os
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from load_acquisition_definitions import load_acquisition
from audit_required_input_artifacts import github_get, emit_record

ac = load_acquisition()
AUDIT_RUN = 40549452734
AUDIT_ARTIFACT_ID = 12753479500
AUDIT_SHA256 = '11b43305a4aaae9c7c7c15259188128f1ec19b989b53ade3f5a7d0577091e312'


def append_pages(client, record, rows, max_pages=200):
    path = '/iss/datashop/algopack/fo/' + record['metric'] + '/' + record['contract'] + '.json'
    query = {'from': record['first'], 'till': (date.fromisoformat(record['end_exclusive']) - timedelta(days=1)).isoformat(), 'iss.meta': 'off'}
    def identity(row):
        stamp = ac.bg.parse_iss_time(row['tradedate'] + 'T' + row['tradetime'])
        if row['secid'] != record['contract'] or row['asset_code'] != record['asset'] or not date.fromisoformat(record['first']) <= stamp.date() < date.fromisoformat(record['end_exclusive']):
            raise ValueError('Response outside requested contract/date')
        return (row['secid'], row['tradedate'], row['tradetime']), stamp
    prefix = [identity(row) for row in rows]
    if len(set(key for key, _ in prefix)) != len(prefix) or any(a[1] > b[1] for a, b in zip(prefix, prefix[1:])):
        raise ValueError('Archived prefix is duplicated or out of time order')
    offset = len(rows)
    if offset:
        boundary = max(0, offset - 1000)
        doc = client.request(path, {**query, 'start': boundary})
        overlap = ac.bg.table_rows(doc, 'data')
        if ac.acquisition_bytes(overlap) != ac.acquisition_bytes(rows[boundary:offset]):
            raise ValueError('Archived prefix boundary changed; no incompatible snapshots merged')
    seen = {key for key, _ in prefix}
    last_stamp = prefix[-1][1] if prefix else None
    for _ in range(max_pages):
        doc = client.request(path, {**query, 'start': offset})
        part = ac.bg.table_rows(doc, 'data')
        cursor = ac.bg.table_rows(doc, 'data.cursor') if 'data.cursor' in doc else []
        if not part:
            if cursor and isinstance(cursor[0].get('TOTAL'), int) and cursor[0]['TOTAL'] > offset:
                raise ValueError('Empty page before declared total')
            return True
        if cursor and cursor[0].get('INDEX') != offset:
            raise ValueError('Cursor ignored or inconsistent')
        for row in part:
            key, stamp = identity(row)
            if key in seen:
                raise ValueError('Duplicate or repeated panel page')
            if last_stamp is not None and stamp < last_stamp:
                raise ValueError('Panel rows out of time order')
            seen.add(key)
            last_stamp = stamp
        rows.extend(part)
        offset += len(part)
    raise ac.AcquisitionStop('Bounded missing-page budget exhausted')


def probe_metadata(client):
    probes = [
        ('session-date-2025', '/iss/calendars/futures/session.json', {'date': '2025-01-31'}),
        ('session-trade-session-date-2025', '/iss/calendars/futures/session.json', {'trade_session_date': '2025-01-31'}),
        ('all-tradestats-historical-date', '/iss/datashop/algopack/fo/tradestats.json', {'date': '2025-01-31', 'start': 0}),
        ('asset-ticker-BR', '/iss/datashop/algopack/fo/tradestats/BR.json', {'from': '2025-01-31', 'till': '2025-01-31', 'start': 0}),
        ('asset-ticker-GOLD', '/iss/datashop/algopack/fo/tradestats/GOLD.json', {'from': '2025-01-31', 'till': '2025-01-31', 'start': 0}),
        ('contract-catalogue-2028', '/iss/calendars/futures/securities.json', {'from': '2028-01-01', 'till': '2028-12-31'}),
    ]
    result = []
    for name, path, query in probes:
        item = {'name': name, 'path': path, 'query': query}
        try:
            payload = client.request(path, {**query, 'iss.meta': 'off'}, optional=True)
            item['file'] = client.save(name + '.json.gz', payload)
            item['blocks'] = {}
            for key, block in payload.items():
                if isinstance(block, dict) and isinstance(block.get('columns'), list) and isinstance(block.get('data'), list):
                    rows = ac.bg.table_rows(payload, key)
                    dates = sorted({str(r.get('tradedate', r.get('trade_session_date', ''))) for r in rows})
                    item['blocks'][key] = {'columns': block['columns'], 'rows': len(rows), 'dates': dates, 'first_rows': rows[:2], 'last_rows': rows[-2:]}
            item['status'] = 'RECEIVED_NEEDS_SEMANTIC_REVIEW'
        except ac.AcquisitionStop:
            raise
        except Exception as exc:
            item.update(status='UNAVAILABLE', error=ac.bg.safe_error(exc))
        result.append(item)
        emit_record({'type': 'METADATA_PROBE', 'record': item}, 'probe-' + name)
    return result


def repair_main(directory=Path('repair-evidence')):
    directory.mkdir(parents=True, exist_ok=True)
    report = {'source': 'apim.moex.com', 'source_audit_run': AUDIT_RUN,
              'source_audit_artifact_sha256': AUDIT_SHA256, 'orders_enabled': False,
              'strategy_testing_performed': False, 'all_required_inputs_ready': False,
              'exact_agent_replay_ready': False, 'repairs': [], 'errors': []}
    client = ac.Acquisition(directory, spacing=1.0, max_requests=400)
    try:
        if not os.environ.get('MOEX_ALGOPACK_TOKEN', '').strip():
            raise ac.AcquisitionStop('Missing protected MOEX_ALGOPACK_TOKEN')
        body = github_get('/repos/ataruua-tech/-Br-gold/actions/artifacts/' + str(AUDIT_ARTIFACT_ID) + '/zip', archive=True)
        if hashlib.sha256(body).hexdigest() != AUDIT_SHA256:
            raise ValueError('Source audit artifact ZIP hash mismatch')
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            if z.testzip() is not None:
                raise ValueError('Source audit archive CRC failure')
            manifest = json.loads(z.read('manifest.json'))
            for artifact in manifest['artifacts']:
                for record in artifact.get('offline_recovery', []):
                    if record['complete_page_sequence']:
                        continue
                    if artifact['year'] != 2023:
                        raise ValueError('Unexpected missing-range year')
                    name = 'recovered/2023/' + record['contract'] + '-' + record['metric'] + '.json.gz'
                    raw = gzip.decompress(z.read(name))
                    if hashlib.sha256(raw).hexdigest() != record['recovered_payload_sha256']:
                        raise ValueError('Archived prefix payload hash mismatch')
                    rows = json.loads(raw)
                    if len(rows) != record['next_missing_offset']:
                        raise ValueError('Prefix length does not match missing offset')
                    item = {k: record[k] for k in ('contract', 'asset', 'metric', 'first', 'end_exclusive')}
                    item['prefix_rows_reused'] = len(rows)
                    for attempt in range(2):
                        try:
                            append_pages(client, record, rows)
                            item['status'] = 'COMPLETE_PAGE_SEQUENCE'
                            break
                        except ac.AcquisitionStop:
                            raise
                        except RuntimeError as exc:
                            if attempt == 1:
                                item.update(status='INCOMPLETE', error=ac.bg.safe_error(exc))
                        except Exception as exc:
                            item.update(status='INCOMPLETE', error=ac.bg.safe_error(exc))
                            break
                    item['file'] = client.save(record['contract'] + '-' + record['metric'] + '.json.gz', rows)
                    item['rows'] = len(rows)
                    item['invalid_spread_rows'] = sum(r.get('spread_l1') is None or isinstance(r.get('spread_l1'), (float, int)) and r['spread_l1'] < 0 for r in rows) if record['metric'] == 'obstats' else 0
                    item['invalid_quote_values_preserved_and_not_for_execution'] = True
                    if item.get('status') != 'COMPLETE_PAGE_SEQUENCE':
                        report['errors'].append(record['contract'] + '/' + record['metric'] + ': ' + item.get('error', 'Incomplete'))
                    report['repairs'].append(item)
                    client.save('repair-progress.json.gz', report['repairs'])
                    emit_record({'type': 'MISSING_RANGE_REPAIR', 'record': item}, 'repair-' + record['contract'] + '-' + record['metric'])
        report['metadata_probes'] = probe_metadata(client)
        report['status'] = 'MISSING_RANGES_REPAIRED_REQUIRES_DATA_REVIEW' if not report['errors'] and len(report['repairs']) == 7 else 'PARTIAL_ERROR'
    except Exception as exc:
        report['status'] = 'BLOCKED_OR_INCOMPLETE'
        report['errors'].append(ac.bg.safe_error(exc))
    report['requests'] = client.count
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    (directory / 'manifest.json').write_bytes(ac.acquisition_bytes(report))
    emit_record({'type': 'MISSING_RANGE_REPAIR_STATUS', 'status': report['status'], 'repairs': len(report['repairs']), 'requests': report['requests'], 'errors': report['errors']}, 'repair-final')
    return 1 if report['status'] in ('BLOCKED_OR_INCOMPLETE', 'PARTIAL_ERROR') else 0


if __name__ == '__main__':
    raise SystemExit(repair_main())
