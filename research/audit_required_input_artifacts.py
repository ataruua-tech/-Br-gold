"""Verify acquisition archives inside GitHub, where archive URLs remain fresh.

Only downloads and audits research data. No broker, Telegram or strategy fitting.
"""
import base64
import gzip
import hashlib
import io
import json
import os
import pathlib
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, timedelta

SOURCE_RUN = 40529837847
SOURCE_COMMIT = 'a25b25cc86fa688a7dd553b72fa3015661ad61eb'
EXPECTED = {
    12746175473: 'ce800a4708c440a408a7159d892004bf8b74f341e8f59d27b5f1c22fbbc9bc1d',
    12746843724: '19da1f8ce54a11b8e82ba9aab96b43555efeae219b93e3f686f485755389b24f',
    12746864412: '6626bf3ce942d6ec588288b617f77d772667ae9fa2e888f523be601baeabd2cb',
    12750065009: 'a64eeb2b988001c22237be4c8b752c32c474a7bd0703362b815c56a88bcfb1c6',
    12750371606: 'ce81eb1e8b1c2866086b928418e26fc9adc8d53f76ce9f2b592bc9448cb0de89',
}


def emit_record(value, key):
    """GitHub annotations truncate messages at 4096 bytes; send bounded parts."""
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    encoded = base64.b64encode(raw).decode()
    parts = [encoded[i:i + 2400] for i in range(0, len(encoded), 2400)]
    for index, part in enumerate(parts):
        message = {'type': 'RESEARCH_REPORT_PART', 'key': key, 'index': index,
                   'parts': len(parts), 'base64': part}
        print('::notice::' + json.dumps(message, separators=(',', ':')), flush=True)


def rebuild_panel_from_pages(z, requests, record):
    """Recover retained rows without a new market-data request; keep bad quotes."""
    path = '/iss/datashop/algopack/fo/' + record['metric'] + '/' + record['contract'] + '.json'
    till = (date.fromisoformat(record['end_exclusive']) - timedelta(days=1)).isoformat()
    pages, failed = {}, []
    for request in requests:
        parsed = urllib.parse.urlsplit(request['url'])
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path != path or query.get('from') != [record['first']] or query.get('till') != [till]:
            continue
        offset = int(query['start'][0])
        if request['status'] != 'RECEIVED':
            failed.append(offset)
            continue
        doc = json.loads(gzip.decompress(z.read(request['file'])))['data']
        if any(len(r) != len(doc['columns']) for r in doc['data']):
            raise ValueError('Malformed retained panel page')
        pages[offset] = [dict(zip(doc['columns'], row)) for row in doc['data']]
    rows, expected, complete = [], 0, False
    for offset, page in sorted(pages.items()):
        if offset != expected:
            break
        rows.extend(page)
        if not page:
            complete = True
            break
        expected += len(page)
    seen = set()
    quarantine = []
    for row in rows:
        key = (row['secid'], row['tradedate'], row['tradetime'])
        if key in seen:
            raise ValueError('Duplicate row in raw-page recovery')
        seen.add(key)
        if record['metric'] == 'obstats':
            mid, spread = row.get('mid_price'), row.get('spread_l1')
            reasons = []
            if not isinstance(mid, (int, float)) or not math.isfinite(mid) or mid <= 0:
                reasons.append('INVALID_OR_MISSING_MID')
            if not isinstance(spread, (int, float)) or not math.isfinite(spread):
                reasons.append('INVALID_OR_MISSING_SPREAD')
            elif spread < 0:
                reasons.append('NEGATIVE_SPREAD')
            if reasons:
                quarantine.append({'secid': row['secid'], 'tradedate': row['tradedate'],
                                   'tradetime': row['tradetime'], 'mid_price': mid,
                                   'spread_l1': spread, 'reasons': reasons})
    info = {'contract': record['contract'], 'asset': record['asset'], 'metric': record['metric'],
            'first': record['first'], 'end_exclusive': record['end_exclusive'], 'raw_rows_retained': len(rows),
            'complete_page_sequence': complete, 'next_missing_offset': None if complete else expected,
            'failed_offsets': failed, 'unusable_quote_rows': len(quarantine),
            'bad_values_not_clamped_or_imputed': True, 'historical_executable_BBO': False,
            'quarantine_examples': quarantine[:8]}
    return rows, quarantine, info


class NoAuthRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def github_get(path, archive=False):
    if not path.startswith('/repos/ataruua-tech/-Br-gold/'):
        raise ValueError('Unexpected repository endpoint')
    token = os.environ.get('GH_TOKEN', '')
    if not token:
        raise ValueError('Missing ephemeral GitHub Actions token')
    request = urllib.request.Request('https://api.github.com' + path, headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'BR-GOLD-Research-Data-Audit'})
    opener = urllib.request.build_opener(NoAuthRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(96 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        if not archive or exc.code != 302:
            raise RuntimeError('GitHub HTTP ' + str(exc.code)) from None
        location = exc.headers.get('Location', '')
        parsed = urllib.parse.urlsplit(location)
        if parsed.scheme != 'https' or not (parsed.hostname or '').endswith(('.blob.core.windows.net', '.githubusercontent.com', '.actions.githubusercontent.com')):
            raise ValueError('Unapproved artifact redirect destination') from None
        # A NEW unauthenticated request: the GitHub token never follows a redirect.
        with opener.open(urllib.request.Request(location), timeout=60) as response:
            body = response.read(96 * 1024 * 1024 + 1)
    if len(body) > 96 * 1024 * 1024:
        raise ValueError('Archive exceeds bounded size')
    return body if archive else json.loads(body)


def audit_archive(body, artifact_id, expected_sha256):
    if hashlib.sha256(body).hexdigest() != expected_sha256:
        raise ValueError('ZIP digest mismatch')
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        if z.testzip() is not None:
            raise ValueError('ZIP CRC failure')
        if sum(e.file_size for e in z.infolist()) > 1024 * 1024 * 1024:
            raise ValueError('Uncompressed archive exceeds bounded size')
        for e in z.infolist():
            q = pathlib.PurePosixPath(e.filename)
            if q.is_absolute() or '..' in q.parts or ((e.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError('Unsafe archive member')
        manifest = json.loads(z.read('manifest.json'))
        if manifest['source'] != 'apim.moex.com' or manifest['orders_enabled'] is not False or manifest['fitting_or_backtesting_performed'] is not False:
            raise ValueError('Unexpected acquisition source/mode')
        requests = [json.loads(s) for s in z.read('requests.jsonl').decode().splitlines()]
        if len(requests) != manifest['requests'] or [r['request_number'] for r in requests] != list(range(1, len(requests) + 1)):
            raise ValueError('Request transcript is incomplete')
        request_errors = []
        for request in requests:
            if urllib.parse.urlsplit(request['url']).hostname != 'apim.moex.com':
                raise ValueError('Non-APIM data source')
            if request['status'] == 'RECEIVED':
                raw = gzip.decompress(z.read(request['file']))
                if hashlib.sha256(raw).hexdigest() != request['response_sha256']:
                    raise ValueError('Raw response hash mismatch')
            else:
                request_errors.append({'url': request['url'], 'error': request.get('error')})
        totals = {'tradestats_rows': 0, 'obstats_rows': 0, 'im_positive_rows': 0, 'im_null_rows': 0}
        for record in manifest['records']:
            item = record.get('file')
            if not item:
                continue
            raw = gzip.decompress(z.read(item['file']))
            if hashlib.sha256(raw).hexdigest() != item['uncompressed_sha256']:
                raise ValueError('Normalized payload hash mismatch')
            data = json.loads(raw)
            if item['rows'] is not None and len(data) != item['rows']:
                raise ValueError('Normalized row count mismatch')
            if record.get('metric'):
                keys = [(r['secid'], r['tradedate'], r['tradetime']) for r in data]
                if len(set(keys)) != len(keys):
                    raise ValueError('Normalized duplicate panel rows')
                totals[record['metric'] + '_rows'] += len(data)
                if record['metric'] == 'tradestats':
                    totals['im_positive_rows'] += sum(isinstance(r.get('im'), (int, float)) and r['im'] > 0 for r in data)
                    totals['im_null_rows'] += sum(r.get('im') is None for r in data)
        summary = {'artifact_id': artifact_id, 'zip_sha256': expected_sha256, 'integrity': 'PASSED',
                   'mode': manifest['mode'], 'year': manifest['year'], 'acquisition_status': manifest['status'],
                   'requests': len(requests), 'request_errors': request_errors, 'totals': totals,
                   'acquisition_errors': [r for r in manifest['records'] if r['status'] in ('ERROR', 'UNAVAILABLE')],
                   'source_empty_panels': [r for r in manifest['records'] if r['status'] == 'SOURCE_EMPTY'],
                   'exact_agent_replay_ready': False}
        return summary, manifest


def recover_and_extract(body, manifest, directory):
    results = []
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        requests = [json.loads(s) for s in z.read('requests.jsonl').decode().splitlines()]
        if manifest['mode'] == 'panels':
            target = directory / 'recovered' / str(manifest['year'])
            target.mkdir(parents=True, exist_ok=True)
            for record in manifest['records']:
                if record['status'] != 'ERROR':
                    continue
                rows, quarantine, info = rebuild_panel_from_pages(z, requests, record)
                filename = record['contract'] + '-' + record['metric']
                content = json.dumps(rows, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode()
                (target / (filename + '.json.gz')).write_bytes(gzip.compress(content, mtime=0))
                (target / (filename + '-quarantine.json')).write_text(json.dumps(quarantine, ensure_ascii=False, indent=2))
                info['recovered_payload_sha256'] = hashlib.sha256(content).hexdigest()
                results.append(info)
                emit_record({'type': 'RECOVERED_PANEL', 'year': manifest['year'], 'record': info}, 'recovered-' + str(manifest['year']) + '-' + filename)
        else:
            for record in manifest['records']:
                if not record.get('file'):
                    continue
                payload = json.loads(gzip.decompress(z.read(record['file']['file'])))
                data = {}
                if record['name'].startswith('off-days-'):
                    block = payload['off_days']
                    data['off_days'] = [dict(zip(block['columns'], row)) for row in block['data']]
                elif record['name'].startswith('contract-catalogue-'):
                    block = payload['forts']
                    data['contracts'] = [dict(zip(block['columns'], row)) for row in block['data'] if dict(zip(block['columns'], row)).get('asset_code') in ('BR', 'GOLD')]
                elif record['name'].startswith('contract-description-'):
                    block = payload['description']
                    data['description'] = [dict(zip(block['columns'], row)) for row in block['data']]
                    block = payload['boards']
                    data['boards'] = [dict(zip(block['columns'], row)) for row in block['data']]
                if data:
                    data.update(name=record['name'], source_payload_sha256=record['file']['uncompressed_sha256'])
                    (directory / (record['name'] + '-full.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2))
                    emit_record({'type': 'CATALOGUE_FULL_DATA', 'record': data}, 'full-' + record['name'])
    return results


def main():
    directory = pathlib.Path('input-audit-evidence')
    directory.mkdir(exist_ok=True)
    report = {'source_run_id': SOURCE_RUN, 'source_commit': SOURCE_COMMIT, 'all_required_inputs_ready': False,
              'strategy_testing_performed': False, 'artifacts': [], 'errors': []}
    try:
        prefix = '/repos/ataruua-tech/-Br-gold/actions/'
        run = github_get(prefix + 'runs/' + str(SOURCE_RUN))
        if run['head_sha'] != SOURCE_COMMIT or run['path'] != '.github/workflows/algopack-research-required-inputs.yml':
            raise ValueError('Unreviewed acquisition source')
        available = github_get(prefix + 'runs/' + str(SOURCE_RUN) + '/artifacts?per_page=100')['artifacts']
        for artifact in available:
            artifact_id = artifact['id']
            if artifact_id not in EXPECTED:
                continue
            expected = EXPECTED[artifact_id]
            if artifact.get('digest') != 'sha256:' + expected:
                raise ValueError('GitHub metadata digest changed')
            body = github_get(prefix + 'artifacts/' + str(artifact_id) + '/zip', archive=True)
            summary, manifest = audit_archive(body, artifact_id, expected)
            recovery = recover_and_extract(body, manifest, directory)
            summary['offline_recovery'] = recovery
            report['artifacts'].append(summary)
            (directory / (artifact['name'] + '-manifest.json')).write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
            emit_record({'type': 'RESEARCH_INPUT_AUDIT', 'summary': summary}, 'summary-' + str(artifact_id))
            if manifest['mode'] == 'catalogue':
                for record in manifest['records']:
                    emit_record({'type': 'RESEARCH_CATALOGUE_RECORD', 'record': record}, 'catalogue-' + record['name'])
        if {r['artifact_id'] for r in report['artifacts']} != set(EXPECTED):
            report['errors'].append('Not all five expected archives available and verified')
    except Exception as exc:
        report['errors'].append(type(exc).__name__ + ': ' + str(exc).replace(os.environ.get('GH_TOKEN') or '\0', '[REDACTED]'))
    report['status'] = 'AUDIT_FINISHED_REQUIRES_DATA_REVIEW' if not report['errors'] else 'AUDIT_INCOMPLETE'
    (directory / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    emit_record({'type': 'RESEARCH_AUDIT_STATUS', 'status': report['status'], 'errors': report['errors']}, 'final-status')
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
