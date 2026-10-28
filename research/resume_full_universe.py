"""Resume only the sixteen incomplete contract/year/metric records, never fit strategies."""
import gzip
import hashlib
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from load_acquisition_definitions import load_acquisition
from audit_required_input_artifacts import emit_record
from collect_full_universe import verified_zip, cached_payload, merge_rows

ac = load_acquisition()
RESUME_REFERENCES = {
    'universe': (12762953714, '24a2b93e33672546f35b154ab903eb47907439edb896a8bfbc0ec8c98e3104e9'),
    'panels2026': (12746864412, '6626bf3ce942d6ec588288b617f77d772667ae9fa2e888f523be601baeabd2cb'),
    'prices2026': (12735884135, 'b8344fbe990dd1ae069ceef3ae0709ca12375a7205a26f1360e3ba34b66b1ffb'),
    'prior_resume': (12792924107, '36f2728b9b35f2e77b8782d6de66c1b5e77e0c9e892a0656738bcf49916b5da3'),
}


def single_official_fetch(url):
    """One bounded attempt, retaining useful network diagnostics without secrets."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'apim.moex.com' or parsed.username or parsed.port not in (None, 443):
        raise ValueError('Only the authenticated official gateway is permitted')
    token = os.environ.get('MOEX_ALGOPACK_TOKEN', '').strip()
    if not token:
        raise ac.AcquisitionStop('Missing protected MOEX_ALGOPACK_TOKEN')
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'User-Agent': 'BR-GOLD-Research-Resume/1.0'})
    try:
        with ac.bg.safe_urlopen(request, timeout=20) as response:
            payload = json.load(response)
        if not isinstance(payload, dict):
            raise ValueError('Official API returned non-object JSON')
        return payload
    except urllib.error.HTTPError as exc:
        raise RuntimeError('MOEX HTTP ' + str(exc.code)) from None
    except (OSError, ValueError) as exc:
        reason = getattr(exc, 'reason', exc)
        raise RuntimeError('MOEX network/JSON ' + type(reason).__name__ + ': ' + ac.bg.safe_error(reason)[:220]) from None


def checked_archive(z):
    manifest = json.loads(z.read('manifest.json'))
    requests = [json.loads(line) for line in z.read('requests.jsonl').decode().splitlines()]
    if manifest['source'] != 'apim.moex.com' or manifest['strategy_testing_performed'] is not False or len(requests) != manifest['requests']:
        raise ValueError('Unexpected source manifest or incomplete transcript')
    for item in requests:
        if item['status'] == 'RECEIVED':
            raw = gzip.decompress(z.read(item['file']))
            if hashlib.sha256(raw).hexdigest() != item['response_sha256']:
                raise ValueError('Retained raw page hash mismatch')
    completed, failed = [], []
    for row in manifest['records']:
        if row['status'] == 'COMPLETE_QUERY_SPANS':
            values = cached_payload(z, row['file'])
            if len(values) != row['rows']:
                raise ValueError('Complete record count mismatch')
            merge_rows(values, row['metric'], {'secid': row['contract'], 'asset': row['asset']}, date.fromisoformat(row['first']), date.fromisoformat(row['end_exclusive']))
            completed.append(row)
        else:
            failed.append(row)
    if len(completed) != 222 or len(failed) != 16:
        raise ValueError('Unexpected checkpoint; do not widen a sixteen-record repair')
    return manifest, requests, completed, failed


def query_prefix(z, transcript, path, table, query):
    pages = {}
    for item in transcript:
        parsed = urllib.parse.urlsplit(item['url'])
        q = urllib.parse.parse_qs(parsed.query)
        if parsed.path != path or any(q.get(k) != [str(v)] for k, v in query.items()):
            continue
        if item['status'] != 'RECEIVED':
            continue
        raw = gzip.decompress(z.read(item['file']))
        if hashlib.sha256(raw).hexdigest() != item['response_sha256']:
            raise ValueError('Prefix raw hash mismatch')
        payload = json.loads(raw)
        part = ac.bg.table_rows(payload, table)
        offset = int(q.get('start', ['0'])[0])
        if offset in pages and pages[offset] != part:
            raise ValueError('Retained source revisions conflict')
        pages[offset] = part
    rows, offset, boundary = [], 0, None
    while offset in pages:
        part = pages[offset]
        if not part:
            return rows, True, boundary
        boundary = (offset, part)
        rows.extend(part)
        offset += len(part)
    return rows, False, boundary


def resume_pages(client, path, table, query, rows, complete, boundary):
    if complete:
        return rows
    def request(offset):
        for attempt in range(2):
            try:
                return client.request(path, {**query, 'start': offset, 'iss.meta': 'off'})
            except ac.AcquisitionStop:
                raise
            except RuntimeError:
                if attempt:
                    raise
                ac.time.sleep(2)
    if boundary is not None:
        doc = request(boundary[0])
        if ac.bg.table_rows(doc, table) != boundary[1]:
            raise ValueError('Archived boundary changed; incompatible snapshots not merged')
    offset, fingerprints = len(rows), set()
    for _ in range(250):
        doc = request(offset)
        part = ac.bg.table_rows(doc, table)
        cursor = ac.bg.table_rows(doc, table + '.cursor') if table + '.cursor' in doc else []
        if not part:
            if cursor and isinstance(cursor[0].get('TOTAL'), int) and cursor[0]['TOTAL'] > offset:
                raise ValueError('Empty page before advertised total')
            return rows
        if cursor and cursor[0].get('INDEX') != offset:
            raise ValueError('Pagination cursor ignored')
        fingerprint = hashlib.sha256(ac.acquisition_bytes(part)).hexdigest()
        if fingerprint in fingerprints:
            raise ValueError('Repeated page')
        fingerprints.add(fingerprint)
        rows.extend(part)
        offset += len(part)
    raise ac.AcquisitionStop('Bounded resume-page budget exhausted')


def old_cached_values(zips, row):
    z = zips['panels2026' if row['metric'] == 'tradestats' else 'prices2026']
    manifest = json.loads(z.read('manifest.json'))
    values, spans = [], []
    records = manifest['records'] if row['metric'] == 'tradestats' else manifest['history']
    for item in records:
        if item['contract'] != row['contract']:
            continue
        if row['metric'] == 'tradestats':
            if item.get('metric') != 'tradestats' or item['status'] not in ('ACQUIRED_ROWS', 'SOURCE_EMPTY'):
                continue
            file = item['file']
        else:
            if item['status'] != 'ACQUIRED':
                continue
            file = next(f for f in item['files'] if '-H1.' in f['file'])
        values.extend(cached_payload(z, file))
        spans.append((item['first'], item['end_exclusive']))
    if row['cached_query_spans_reused'] and not spans:
        raise ValueError('Required original cached values were not found')
    return values


def prior_completed(z, failed):
    manifest = json.loads(z.read('manifest.json'))
    allowed = {(r['year'], r['contract'], r['metric']) for r in failed}
    result = {}
    for row in manifest['repairs']:
        key = (row['year'], row['contract'], row['metric'])
        if key not in allowed or key in result:
            raise ValueError('Prior resume record outside the remaining scope or duplicated')
        if row['status'] == 'COMPLETE_QUERY_SPANS':
            values = cached_payload(z, row['file'])
            checked = merge_rows(values, row['metric'], {'secid': row['contract'], 'asset': row['asset']}, date.fromisoformat(row['first']), date.fromisoformat(row['end_exclusive']))
            if len(checked) != len(values) or len(values) != row['rows']:
                raise ValueError('Prior completed resume record is inconsistent')
            result[key] = (row, values)
    return result


def resume_main(directory=Path('universe-resume-evidence')):
    directory.mkdir(parents=True, exist_ok=True)
    client = ac.Acquisition(directory, fetch=single_official_fetch, spacing=1.0, max_requests=1500)
    report = {'source': 'apim.moex.com', 'orders_enabled': False, 'strategy_testing_performed': False,
              'all_required_inputs_ready': False, 'exact_agent_replay_ready': False,
              'source_archives': RESUME_REFERENCES, 'repairs': [], 'errors': []}
    zips = {}
    try:
        if not os.environ.get('MOEX_ALGOPACK_TOKEN', '').strip():
            raise ac.AcquisitionStop('Missing protected MOEX_ALGOPACK_TOKEN')
        zips = {name: verified_zip(ref) for name, ref in RESUME_REFERENCES.items()}
        z = zips['universe']
        source_manifest, transcript, completed, failed = checked_archive(z)
        report['complete_records_reused_without_market_requests'] = len(completed)
        report['target_records'] = len(failed)
        report['catalogue_contracts'] = source_manifest['catalogue_contracts']
        catalogue = json.loads(gzip.decompress(z.read('full-contract-catalogue.json.gz')))
        by_code = {c['secid']: c for c in catalogue}
        prior = prior_completed(zips['prior_resume'], failed)
        report['previously_restored_records_reused_without_market_requests'] = len(prior)
        consecutive_failures = 0
        for source_row in failed:
            row = {k: source_row[k] for k in ('year', 'contract', 'asset', 'metric', 'first', 'end_exclusive', 'new_query_spans', 'cached_query_spans_reused')}
            try:
                key = (row['year'], row['contract'], row['metric'])
                if key in prior:
                    original, values = prior[key]
                    row.update(original)
                    row['file'] = client.save(str(row['year']) + '-' + row['contract'] + '-' + row['metric'] + '.json.gz', values)
                    row['previously_restored_record_reused'] = True
                    report['repairs'].append(row)
                    emit_record({'type': 'UNIVERSE_RESUMED_RECORD', 'record': row}, 'resume-' + row['contract'] + '-' + row['metric'])
                    continue
                values = old_cached_values(zips, row)
                row['original_rows_reused'] = len(values)
                row['raw_prefix_rows_reused'] = 0
                for first, end in row['new_query_spans']:
                    path = '/iss/datashop/algopack/fo/tradestats/' + row['contract'] + '.json' if row['metric'] == 'tradestats' else '/iss/engines/futures/markets/forts/boards/rfud/securities/' + row['contract'] + '/candles.json'
                    query = {'from': first, 'till': (date.fromisoformat(end) - timedelta(days=1)).isoformat()}
                    if row['metric'] == 'h1':
                        query['interval'] = 60
                    table = 'data' if row['metric'] == 'tradestats' else 'candles'
                    prefix, complete, boundary = query_prefix(z, transcript, path, table, query)
                    row['raw_prefix_rows_reused'] += len(prefix)
                    part = resume_pages(client, path, table, query, prefix, complete, boundary)
                    for value in part:
                        stamp = ac.bg.parse_iss_time(value['begin'] if row['metric'] == 'h1' else value['tradedate'] + 'T' + value['tradetime'])
                        if not date.fromisoformat(first) <= stamp.date() < date.fromisoformat(end):
                            raise ValueError('Source ignored requested date range')
                    values.extend(part)
                values = merge_rows(values, row['metric'], by_code[row['contract']], date.fromisoformat(row['first']), date.fromisoformat(row['end_exclusive']))
                row.update(status='COMPLETE_QUERY_SPANS', rows=len(values), file=client.save(str(row['year']) + '-' + row['contract'] + '-' + row['metric'] + '.json.gz', values))
                consecutive_failures = 0
            except ac.AcquisitionStop:
                raise
            except Exception as exc:
                row.update(status='INCOMPLETE', error=ac.bg.safe_error(exc))
                report['errors'].append(row['contract'] + '/' + row['metric'] + ': ' + row['error'])
                consecutive_failures += 1
            report['repairs'].append(row)
            client.save('resume-progress.json.gz', report['repairs'])
            emit_record({'type': 'UNIVERSE_RESUMED_RECORD', 'record': row}, 'resume-' + row['contract'] + '-' + row['metric'])
            if consecutive_failures >= 2:
                raise ac.AcquisitionStop('Two consecutive failed scopes; circuit open, no repeated outage hammering')
        report['status'] = 'SIXTEEN_RECORDS_RESTORED_REQUIRES_SUFFICIENCY_AUDIT' if len(report['repairs']) == 16 and not report['errors'] else 'PARTIAL_ERROR'
    except Exception as exc:
        report['status'] = 'BLOCKED_OR_INCOMPLETE'
        report['errors'].append(ac.bg.safe_error(exc))
    finally:
        for z in zips.values():
            z.close()
    report['requests'] = client.count
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    (directory / 'manifest.json').write_bytes(ac.acquisition_bytes(report))
    emit_record({'type': 'UNIVERSE_RESUME_STATUS', **report}, 'resume-final')
    return 0 if report['status'] == 'SIXTEEN_RECORDS_RESTORED_REQUIRES_SUFFICIENCY_AUDIT' else 1


if __name__ == '__main__':
    raise SystemExit(resume_main())
