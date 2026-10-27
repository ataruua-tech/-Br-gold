"""Expand verified caches to full listed contract histories; no strategy fitting."""
import gzip
import hashlib
import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import zipfile
import re

from load_acquisition_definitions import load_acquisition
from audit_required_input_artifacts import github_get, emit_record

ac = load_acquisition()
SOURCE_ARCHIVES = {
    'audit': (12753479500, '11b43305a4aaae9c7c7c15259188128f1ec19b989b53ade3f5a7d0577091e312'),
    'repair': (12754887586, 'bd9ce3617ed34fcc863cd941115b3269b84b0da01f82386eaab754452b3f18bd'),
    'panels2023': (12750065009, 'a64eeb2b988001c22237be4c8b752c32c474a7bd0703362b815c56a88bcfb1c6'),
    'panels2024': (12750371606, 'ce81eb1e8b1c2866086b928418e26fc9adc8d53f76ce9f2b592bc9448cb0de89'),
    'panels2025': (12746843724, '19da1f8ce54a11b8e82ba9aab96b43555efeae219b93e3f686f485755389b24f'),
    'panels2026': (12746864412, '6626bf3ce942d6ec588288b617f77d772667ae9fa2e888f523be601baeabd2cb'),
    'prices2023': (12731587244, 'fdbd8f024c12ab110c0d9f15a1aabd107039bde066b0410054a9b7939abf578c'),
    'prices2024': (12733895222, '5a99d6eee7f98d7e76a1dffe86dcfeea4b523be14c587b22f8c3000905d4c04c'),
    'prices2025': (12734831380, 'd8eff7373c9bcaafb570fbd24a44d84709353112396f90c62bd299e86b003253'),
    'prices2026': (12735884135, 'b8344fbe990dd1ae069ceef3ae0709ca12375a7205a26f1360e3ba34b66b1ffb'),
}


def verified_zip(reference):
    artifact_id, expected = reference
    raw = github_get('/repos/ataruua-tech/-Br-gold/actions/artifacts/' + str(artifact_id) + '/zip', archive=True)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Source ZIP hash mismatch')
    z = zipfile.ZipFile(io.BytesIO(raw))
    if z.testzip() is not None:
        raise ValueError('Source ZIP CRC failure')
    return z


def cached_payload(z, item):
    raw = gzip.decompress(z.read(item['file']))
    if hashlib.sha256(raw).hexdigest() != item['uncompressed_sha256']:
        raise ValueError('Cached normalized payload hash mismatch')
    value = json.loads(raw)
    if item.get('rows') is not None and len(value) != item['rows']:
        raise ValueError('Cached row count mismatch')
    return value


def missing_spans(first, end, covered):
    """Subtract known complete query spans using half-open calendar intervals."""
    position, gaps = first, []
    for start, stop in sorted(covered):
        start, stop = max(first, start), min(end, stop)
        if stop <= start or stop <= position:
            continue
        if start > position:
            gaps.append((position, start))
        position = max(position, stop)
    if position < end:
        gaps.append((position, end))
    return gaps


def pages(client, path, table, query):
    rows, fingerprints, offset = [], set(), 0
    for _ in range(1000):
        for attempt in range(2):
            try:
                payload = client.request(path, {**query, 'start': offset, 'iss.meta': 'off'})
                break
            except ac.AcquisitionStop:
                raise
            except RuntimeError:
                if attempt == 1:
                    raise
                ac.time.sleep(2)
        part = ac.bg.table_rows(payload, table)
        cursor = ac.bg.table_rows(payload, table + '.cursor') if table + '.cursor' in payload else []
        if not part:
            if cursor and isinstance(cursor[0].get('TOTAL'), int) and cursor[0]['TOTAL'] > offset:
                raise ValueError('Empty page before advertised total')
            return rows
        if cursor and cursor[0].get('INDEX') != offset:
            raise ValueError('Ignored pagination cursor')
        digest = hashlib.sha256(ac.acquisition_bytes(part)).hexdigest()
        if digest in fingerprints:
            raise ValueError('Repeated source page')
        fingerprints.add(digest)
        rows.extend(part)
        offset += len(part)
    raise ac.AcquisitionStop('Panel pagination limit reached')


def merge_rows(rows, metric, contract, first, end):
    found = {}
    for row in rows:
        if metric == 'h1':
            stamp = ac.bg.parse_iss_time(row['begin'])
            key = row['begin']
        else:
            stamp = ac.bg.parse_iss_time(row['tradedate'] + 'T' + row['tradetime'])
            if row['secid'] != contract['secid'] or row['asset_code'] != contract['asset']:
                raise ValueError('Panel instrument mismatch')
            if not isinstance(row.get('vol'), (int, float)) or not ac.math.isfinite(row['vol']) or row['vol'] < 0:
                raise ValueError('Invalid volume observation')
            key = row['tradedate'] + 'T' + row['tradetime']
        if not first <= stamp.date() < end:
            continue  # Cached warm-up may intentionally be wider than this output shard.
        if key in found and found[key] != row:
            raise ValueError('Conflicting cached/fresh values; revised snapshots not silently merged')
        found[key] = row
    result = [found[k] for k in sorted(found)]
    if metric == 'h1':
        return ac.acquisition_validate_candles(result, 60, first, end)
    return result


def collect_catalogue(client, audit, repair):
    instruments, cached_descriptions = {}, {}
    for name in audit.namelist():
        if name.startswith('contract-catalogue-') and name.endswith('-full.json'):
            for row in json.loads(audit.read(name))['contracts']:
                if re.fullmatch(r'(BR|GD)[FGHJKMNQUVXZ][0-9]+', row['secid']):
                    instruments[row['secid']] = {'secid': row['secid'], 'asset': row['asset_code'], 'calendar_expiration': row['expiration_date'], 'weekend_session': row['weekend_session']}
        elif name.startswith('contract-description-') and name.endswith('-full.json'):
            data = json.loads(audit.read(name))
            values = {r['name']: r['value'] for r in data['description']}
            cached_descriptions[values['SECID']] = data
    future = json.loads(gzip.decompress(repair.read('contract-catalogue-2028.json.gz')))
    for row in ac.bg.table_rows(future, 'forts'):
        if row['asset_code'] in ('BR', 'GOLD') and re.fullmatch(r'(BR|GD)[FGHJKMNQUVXZ][0-9]+', row['secid']):
            instruments[row['secid']] = {'secid': row['secid'], 'asset': row['asset_code'], 'calendar_expiration': row['expiration_date'], 'weekend_session': row['weekend_session']}
    current = client.request('/iss/engines/futures/markets/forts/boards/rfud/securities.json', {'iss.only': 'securities', 'iss.meta': 'off'})
    for row in ac.bg.table_rows(current, 'securities'):
        if row.get('ASSETCODE') in ('BR', 'GOLD') and re.fullmatch(r'(BR|GD)[FGHJKMNQUVXZ][0-9]+', row['SECID']) and row['SECID'] not in instruments:
            instruments[row['SECID']] = {'secid': row['SECID'], 'asset': row['ASSETCODE'], 'calendar_expiration': row.get('LASTTRADEDATE'), 'weekend_session': None}
    result = []
    for secid, record in sorted(instruments.items()):
        if secid in cached_descriptions:
            values = {r['name']: r['value'] for r in cached_descriptions[secid]['description']}
            record['description_source'] = 'VERIFIED_AUDIT_CACHE'
        else:
            payload = client.request('/iss/securities/' + secid + '.json', {'iss.meta': 'off'})
            values = {r['name']: r['value'] for r in ac.bg.table_rows(payload, 'description')}
            record['description_source'] = client.save('metadata-' + secid + '.json.gz', payload)
        if values['SECID'] != secid or values['ASSETCODE'] != record['asset']:
            raise ValueError('Contract description identity mismatch')
        record.update(first_trade=values['FRSTTRADE'], last_trade=values['LSTTRADE'], lot=values.get('LOTSIZE'), unit=values.get('UNIT'))
        record['expiration_sources_agree'] = record['calendar_expiration'] == record['last_trade']
        result.append(record)
    client.save('full-contract-catalogue.json.gz', result)
    emit_record({'type': 'FULL_CONTRACT_CATALOGUE', 'contracts': result, 'asof_historical_listing_universe_not_claimed': True}, 'full-universe-catalogue')
    return result


def build_cache(zips, repair):
    cache = {}
    for year in (2023, 2024, 2025, 2026):
        z = zips['panels' + str(year)]
        manifest = json.loads(z.read('manifest.json'))
        for row in manifest['records']:
            if row.get('metric') == 'tradestats' and row['status'] in ('ACQUIRED_ROWS', 'SOURCE_EMPTY'):
                cache.setdefault((row['contract'], 'tradestats'), []).append((date.fromisoformat(row['first']), date.fromisoformat(row['end_exclusive']), z, row['file']))
        z = zips['prices' + str(year)]
        manifest = json.loads(z.read('manifest.json'))
        transcript = [json.loads(s) for s in z.read('requests.jsonl').decode().splitlines()]
        for row in manifest['history']:
            if row['status'] != 'ACQUIRED':
                continue
            item = next(f for f in row['files'] if '-H1.' in f['file'])
            starts = []
            for request in transcript:
                parsed = ac.urllib.parse.urlsplit(request['url'])
                query = ac.urllib.parse.parse_qs(parsed.query)
                if '/' + row['contract'] + '/candles.json' in parsed.path and query.get('interval') == ['60']:
                    starts.append(date.fromisoformat(query['from'][0]))
            if not starts:
                raise ValueError('H1 cache lacks source query scope')
            cache.setdefault((row['contract'], 'h1'), []).append((min(starts), date.fromisoformat(row['end_exclusive']), z, item))
    for row in json.loads(repair.read('manifest.json'))['repairs']:
        if row['metric'] == 'tradestats' and row['status'] == 'COMPLETE_PAGE_SEQUENCE':
            cache.setdefault((row['contract'], 'tradestats'), []).append((date.fromisoformat(row['first']), date.fromisoformat(row['end_exclusive']), repair, row['file']))
    return cache


def full_universe_main(directory=Path('universe-evidence')):
    directory.mkdir(parents=True, exist_ok=True)
    client = ac.Acquisition(directory, spacing=1.0, max_requests=5000)
    report = {'source': 'apim.moex.com', 'orders_enabled': False, 'strategy_testing_performed': False,
              'all_required_inputs_ready': False, 'exact_agent_replay_ready': False,
              'cache_reuse': 'Verified complete calendar spans are subtracted before API requests',
              'source_archives': SOURCE_ARCHIVES, 'records': [], 'errors': []}
    zips = {}
    try:
        if not ac.os.environ.get('MOEX_ALGOPACK_TOKEN', '').strip():
            raise ac.AcquisitionStop('Missing protected MOEX_ALGOPACK_TOKEN')
        for name, reference in SOURCE_ARCHIVES.items():
            zips[name] = verified_zip(reference)
        catalogue = collect_catalogue(client, zips['audit'], zips['repair'])
        cache = build_cache(zips, zips['repair'])
        report['catalogue_contracts'] = len(catalogue)
        for year in (2023, 2024, 2025, 2026):
            year_end = min(date(year + 1, 1, 1), date(2026, 10, 26))
            for contract in catalogue:
                last = min(year_end, date.fromisoformat(contract['last_trade']) + timedelta(days=1))
                for metric in ('tradestats', 'h1'):
                    study_first = date(2022, 10, 8) if metric == 'h1' and year == 2023 else date(year, 1, 1)
                    first = max(study_first, date.fromisoformat(contract['first_trade']))
                    if first >= last:
                        continue
                    row = {'year': year, 'contract': contract['secid'], 'asset': contract['asset'], 'metric': metric, 'first': first.isoformat(), 'end_exclusive': last.isoformat()}
                    try:
                        covered, values = [], []
                        for a, b, z, item in cache.get((contract['secid'], metric), []):
                            if a < last and b > first:
                                values.extend(cached_payload(z, item))
                                covered.append((a, b))
                        gaps = missing_spans(first, last, covered)
                        row['cached_query_spans_reused'] = [[a.isoformat(), b.isoformat()] for a, b in covered]
                        row['new_query_spans'] = [[a.isoformat(), b.isoformat()] for a, b in gaps]
                        for a, b in gaps:
                            path = '/iss/datashop/algopack/fo/tradestats/' + contract['secid'] + '.json' if metric == 'tradestats' else '/iss/engines/futures/markets/forts/boards/rfud/securities/' + contract['secid'] + '/candles.json'
                            query = {'from': a.isoformat(), 'till': (b - timedelta(days=1)).isoformat()}
                            if metric == 'h1': query['interval'] = 60
                            part = pages(client, path, 'data' if metric == 'tradestats' else 'candles', query)
                            for value in part:
                                stamp = ac.bg.parse_iss_time(value['begin'] if metric == 'h1' else value['tradedate'] + 'T' + value['tradetime'])
                                if not a <= stamp.date() < b:
                                    raise ValueError('Source ignored requested date span')
                            values.extend(part)
                        values = merge_rows(values, metric, contract, first, last)
                        row.update(status='COMPLETE_QUERY_SPANS', rows=len(values), file=client.save(str(year) + '-' + contract['secid'] + '-' + metric + '.json.gz', values))
                        if metric == 'tradestats':
                            row['im_positive_rows'] = sum(isinstance(v.get('im'), (int, float)) and v['im'] > 0 for v in values)
                            row['im_null_rows'] = sum(v.get('im') is None for v in values)
                            row['systime_later_than_trade_date'] = sum(bool(v.get('SYSTIME')) and v['SYSTIME'][:10] > v['tradedate'] for v in values)
                    except ac.AcquisitionStop:
                        raise
                    except Exception as exc:
                        row.update(status='INCOMPLETE', error=ac.bg.safe_error(exc))
                        report['errors'].append(str(year) + '/' + contract['secid'] + '/' + metric + ': ' + row['error'])
                    report['records'].append(row)
                    client.save('universe-progress.json.gz', report['records'])
                    emit_record({'type': 'UNIVERSE_DATA_COVERAGE', 'record': row}, 'coverage-' + str(year) + '-' + contract['secid'] + '-' + metric)
        report['status'] = 'FULL_LISTED_SCOPE_ACQUIRED_REQUIRES_RECONCILIATION' if not report['errors'] else 'PARTIAL_ERROR'
    except Exception as exc:
        report['status'] = 'BLOCKED_OR_INCOMPLETE'
        report['errors'].append(ac.bg.safe_error(exc))
    finally:
        for z in zips.values(): z.close()
    report['requests'] = client.count
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    (directory / 'manifest.json').write_bytes(ac.acquisition_bytes(report))
    emit_record({'type': 'FULL_UNIVERSE_STATUS', 'status': report['status'], 'requests': client.count, 'records': len(report['records']), 'errors': report['errors']}, 'universe-final')
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(full_universe_main())
