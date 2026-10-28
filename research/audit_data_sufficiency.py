"""Join verified acquisitions and quantify data gaps; no signals or return calculation."""
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from audit_required_input_artifacts import emit_record
from collect_full_universe import verified_zip, cached_payload, merge_rows
from resume_full_universe import checked_archive

# The completed resume artifact is supplied at launch only after GitHub confirms its digest.
SUFFICIENCY_REFERENCES = {
    'universe': (12762953714, '24a2b93e33672546f35b154ab903eb47907439edb896a8bfbc0ec8c98e3104e9'),
    'audit': (12753479500, '11b43305a4aaae9c7c7c15259188128f1ec19b989b53ade3f5a7d0577091e312'),
    'repair': (12754887586, 'bd9ce3617ed34fcc863cd941115b3269b84b0da01f82386eaab754452b3f18bd'),
    'panels2023': (12750065009, 'a64eeb2b988001c22237be4c8b752c32c474a7bd0703362b815c56a88bcfb1c6'),
    'panels2024': (12750371606, 'ce81eb1e8b1c2866086b928418e26fc9adc8d53f76ce9f2b592bc9448cb0de89'),
    'panels2025': (12746843724, '19da1f8ce54a11b8e82ba9aab96b43555efeae219b93e3f686f485755389b24f'),
    'panels2026': (12746864412, '6626bf3ce942d6ec588288b617f77d772667ae9fa2e888f523be601baeabd2cb'),
}


def observation_quality(rows, metric):
    keys, previous = set(), None
    out = Counter(rows=0, duplicates=0, out_of_order=0, invalid_mid=0, invalid_spread=0, null_margin=0, positive_margin=0, invalid_margin=0, invalid_volume=0, systime_after_trade_date=0)
    months = defaultdict(Counter)
    for r in rows:
        stamp = r['tradedate'] + 'T' + r['tradetime']
        key = (r['secid'], stamp)
        out['rows'] += 1
        out['duplicates'] += key in keys
        out['out_of_order'] += previous is not None and stamp < previous
        keys.add(key)
        previous = stamp
        vol = r.get('vol')
        out['invalid_volume'] += not isinstance(vol, (int, float)) or not math.isfinite(vol) or vol < 0 if metric == 'tradestats' else 0
        systime = r.get('SYSTIME')
        out['systime_after_trade_date'] += bool(systime and systime[:10] > r['tradedate'])
        if metric == 'obstats':
            mid, spread = r.get('mid_price'), r.get('spread_l1')
            out['invalid_mid'] += not isinstance(mid, (int, float)) or not math.isfinite(mid) or mid <= 0
            out['invalid_spread'] += not isinstance(spread, (int, float)) or not math.isfinite(spread) or spread < 0
        else:
            im = r.get('im')
            status = 'null_margin' if im is None else 'positive_margin' if isinstance(im, (int, float)) and math.isfinite(im) and im > 0 else 'invalid_margin'
            out[status] += 1
            months[r['tradedate'][:7]][status] += 1
    return {'counts': dict(out), 'margin_by_month': {k: dict(v) for k, v in sorted(months.items())}}


def audit_join(zips):
    z = zips['universe']
    source, transcript, completed, failed = checked_archive(z)
    rz = zips['resume']
    resumed = json.loads(rz.read('manifest.json'))
    if resumed['status'] != 'SIXTEEN_RECORDS_RESTORED_REQUIRES_SUFFICIENCY_AUDIT' or resumed['errors']:
        raise ValueError('Resume has not completed; do not declare history ready')
    expected_keys = {(r['year'], r['contract'], r['metric']) for r in failed}
    actual_keys = {(r['year'], r['contract'], r['metric']) for r in resumed['repairs']}
    if expected_keys != actual_keys or len(resumed['repairs']) != 16:
        raise ValueError('Resume records do not match the original sixteen gaps')
    catalogue = json.loads(gzip.decompress(z.read('full-contract-catalogue.json.gz')))
    out = {'verified_complete_records': 0, 'h1_by_year': {}, 'tradestats_by_year': {}, 'margin_by_month': {}, 'contract_metadata_gaps': [r for r in catalogue if not r['historical_listing_dates_verified']], 'records': []}
    yearly = defaultdict(Counter)
    monthly = defaultdict(Counter)
    for row, archive in [(r, z) for r in completed] + [(r, rz) for r in resumed['repairs']]:
        values = cached_payload(archive, row['file'])
        clean = merge_rows(values, row['metric'], {'secid': row['contract'], 'asset': row['asset']}, date.fromisoformat(row['first']), date.fromisoformat(row['end_exclusive']))
        if len(clean) != len(values):
            raise ValueError('Output contains duplicates or rows outside the declared scope')
        item = {k: row[k] for k in ('year', 'contract', 'metric', 'first', 'end_exclusive', 'rows')}
        if row['metric'] == 'tradestats':
            quality = observation_quality(values, row['metric'])
            if any(quality['counts'][k] for k in ('duplicates', 'out_of_order', 'invalid_volume')):
                raise ValueError('Expanded TradeStats chronology/volume quality failed')
            yearly[(row['year'], row['metric'])].update(quality['counts'])
            for month, counts in quality['margin_by_month'].items():
                monthly[month].update(counts)
            item['quality'] = quality['counts']
        else:
            yearly[(row['year'], row['metric'])]['rows'] += len(values)
        out['records'].append(item)
        out['verified_complete_records'] += 1
    for (year, metric), counts in yearly.items():
        out['h1_by_year' if metric == 'h1' else 'tradestats_by_year'][str(year)] = dict(counts)
    out['margin_by_month'] = {month: dict(counts) for month, counts in sorted(monthly.items())}
    return out


def audit_all_obstats(zips):
    summaries = []
    for year in (2023, 2024, 2025, 2026):
        z = zips['panels' + str(year)]
        manifest = json.loads(z.read('manifest.json'))
        for row in manifest['records']:
            if row.get('metric') == 'obstats' and row['status'] in ('ACQUIRED_ROWS', 'SOURCE_EMPTY'):
                summaries.append({'year': year, 'contract': row['contract'], 'source': 'original', **obstats_file_quality(cached_payload(z, row['file']))})
    audit = zips['audit']
    for artifact in json.loads(audit.read('manifest.json'))['artifacts']:
        for row in artifact.get('offline_recovery', []):
            if row['metric'] == 'obstats' and row['complete_page_sequence']:
                raw = gzip.decompress(audit.read('recovered/' + str(artifact['year']) + '/' + row['contract'] + '-obstats.json.gz'))
                if hashlib.sha256(raw).hexdigest() != row['recovered_payload_sha256']:
                    raise ValueError('Recovered OBStats hash mismatch')
                summaries.append({'year': artifact['year'], 'contract': row['contract'], 'source': 'raw-recovery', **obstats_file_quality(json.loads(raw))})
    repair = zips['repair']
    for row in json.loads(repair.read('manifest.json'))['repairs']:
        if row['metric'] == 'obstats' and row['status'] == 'COMPLETE_PAGE_SEQUENCE':
            summaries.append({'year': 2023, 'contract': row['contract'], 'source': 'missing-range-repair', **obstats_file_quality(cached_payload(repair, row['file']))})
    totals = defaultdict(Counter)
    for row in summaries:
        totals[str(row['year'])].update(row['counts'])
    return {'all_retained_complete_panels_included': True, 'executable_BBO': False, 'spread_l1_unit': 'basis_points; five-minute aggregate', 'panels': summaries, 'by_year': {k: dict(v) for k, v in totals.items()}}


def obstats_file_quality(rows):
    result = observation_quality(rows, 'obstats')
    if result['counts']['duplicates'] or result['counts']['out_of_order']:
        raise ValueError('OBStats chronology/duplicate error')
    return result


def sufficiency_main(directory=Path('sufficiency-evidence')):
    directory.mkdir(parents=True, exist_ok=True)
    out = {'source': 'apim.moex.com archived acquisitions only', 'new_market_requests': 0, 'strategy_testing_performed': False, 'orders_enabled': False, 'exact_agent_replay_ready': False, 'all_required_inputs_ready': False, 'source_archives': SUFFICIENCY_REFERENCES, 'errors': []}
    zips = {}
    try:
        zips = {name: verified_zip(ref) for name, ref in SUFFICIENCY_REFERENCES.items()}
        out['full_universe'] = audit_join(zips)
        out['obstats'] = audit_all_obstats(zips)
        out['status'] = 'ACQUISITIONS_VERIFIED_EXACT_REPLAY_BLOCKED_BY_INPUT_LIMITS'
        out['exact_replay_gaps'] = [
            'Executable historical bid/offer and original quote availability are not supplied by five-minute OBStats',
            'Historical API publication, GitHub runner and Telegram acceptance do not exist for an agent not running then',
            'Calendar-date versus trading-date and intraday volume counters are not identical across the March2026 regime change',
            'Missing historical collateral/economics cannot be filled with present metadata or assumed zeros',
            'M1 coverage remains the frozen original contract windows; expanded H1/TradeStats do not expand M1 coverage',
            'Historical session API returned the current date; dated official schedules provide only partial independent corroboration',
        ]
    except Exception as exc:
        out['status'] = 'TECHNICAL_AUDIT_BLOCKED'
        out['errors'].append(str(exc))
    finally:
        for z in zips.values(): z.close()
    out['verified_at_utc'] = datetime.now(timezone.utc).isoformat()
    (directory / 'data-sufficiency.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False))
    emit_record({'type': 'DATA_SUFFICIENCY_AUDIT', **out}, 'sufficiency-final')
    return 1 if out['errors'] else 0


if __name__ == '__main__': raise SystemExit(sufficiency_main())
