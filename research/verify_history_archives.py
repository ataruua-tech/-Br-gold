"""Offline integrity and M1/H1 audit of the frozen official acquisition archives.

An empty window remains unresolved here; this audit never invents a calendar,
fills bars or changes the original acquisition manifests.
"""
import argparse
import gzip
import hashlib
import json
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_acquisition_definitions import load_acquisition

ac = load_acquisition()
bg = ac.bg
acquisition_validate_candles = ac.acquisition_validate_candles
acquisition_crosscheck = ac.acquisition_crosscheck

EXPECTED = {
    2023: 'fdbd8f024c12ab110c0d9f15a1aabd107039bde066b0410054a9b7939abf578c',
    2024: '5a99d6eee7f98d7e76a1dffe86dcfeea4b523be14c587b22f8c3000905d4c04c',
    2025: 'd8eff7373c9bcaafb570fbd24a44d84709353112396f90c62bd299e86b003253',
    2026: 'b8344fbe990dd1ae069ceef3ae0709ca12375a7205a26f1360e3ba34b66b1ffb',
}


def sha(body):
    return hashlib.sha256(body).hexdigest()


def audit_year(base, year):
    p = base / str(year)
    archive = p / 'download.zip'
    assert sha(archive.read_bytes()) == EXPECTED[year], 'ZIP digest mismatch'
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None, 'ZIP CRC mismatch'
        for entry in z.infolist():
            path = Path(entry.filename)
            assert not path.is_absolute() and '..' not in path.parts
            assert ((entry.external_attr >> 16) & 0o170000) != 0o120000
            if not entry.is_dir():
                assert z.read(entry) == (p / path).read_bytes(), 'Extracted file differs from ZIP'
    manifest = json.loads((p / 'manifest.json').read_text())
    assert manifest['source'] == 'apim.moex.com' and manifest['year'] == year
    assert manifest['orders_enabled'] is False and manifest['signals_generated'] is False
    assert manifest['exact_agent_replay_ready'] is False
    requests = [json.loads(s) for s in (p / 'requests.jsonl').read_text().splitlines()]
    assert len(requests) == manifest['requests']
    assert [r['request_number'] for r in requests] == list(range(1, len(requests) + 1))
    request_errors = []
    fx_source = []
    for r in requests:
        assert urlsplit(r['url']).hostname == 'apim.moex.com'
        if r['status'] != 'RECEIVED':
            request_errors.append(r)
            continue
        body = gzip.decompress((p / r['file']).read_bytes())
        assert sha(body) == r['response_sha256'], 'Response hash mismatch'
        if '/indicativerates/' in r['url']:
            fx_source.extend(bg.table_rows(json.loads(body), 'securities'))
    fx = json.loads(gzip.decompress((p / 'USD-RUB-rates.json.gz').read_bytes()))
    assert fx and fx == fx_source, 'FX differs from archived official responses'
    counts = {1: 0, 60: 0}
    matched = 0
    failed = []
    for window in manifest['history']:
        if window['status'] != 'ACQUIRED':
            failed.append(window)
            continue
        bars = {}
        for item in window['files']:
            interval = 1 if '-M1.' in item['file'] else 60
            body = gzip.decompress((p / item['file']).read_bytes())
            assert sha(body) == item['uncompressed_sha256'], 'Normalized file hash mismatch'
            rows = json.loads(body)
            assert len(rows) == item['rows'] and len({r['begin'] for r in rows}) == len(rows)
            assert rows == sorted(rows, key=lambda r: bg.parse_iss_time(r['begin']))
            start = date.fromisoformat(window['first']) if interval == 1 else min(bg.parse_iss_time(r['begin']).date() for r in rows)
            end = date.fromisoformat(window['end_exclusive'])
            assert acquisition_validate_candles(rows, interval, start, end) == rows
            bars[interval] = rows
            counts[interval] += len(rows)
        cross = acquisition_crosscheck(bars[1], bars[60])
        assert cross == window['crosscheck'], 'M1/H1 check differs from manifest'
        covered = {bg.parse_iss_time(r['begin']).replace(minute=0) for r in bars[1]}
        missing = [r['begin'] for r in bars[60] if date.fromisoformat(window['first']) <= bg.parse_iss_time(r['begin']).date() < date.fromisoformat(window['end_exclusive']) and bg.parse_iss_time(r['begin']) not in covered and float(r['volume']) > 0]
        assert cross['matching_h1'] and not cross['mismatched_h1'] and not cross['m1_hours_missing_source_h1'] and not missing
        matched += cross['matching_h1']
    return {'year': year, 'zip_sha256': EXPECTED[year], 'integrity': 'PASSED',
            'acquisition_status': manifest['status'], 'request_count': len(requests),
            'request_errors': request_errors, 'contract_windows': len(manifest['history']),
            'verified_nonempty_windows': len(manifest['history']) - len(failed),
            'm1_rows': counts[1], 'h1_rows_including_warmup': counts[60],
            'reconciled_h1': matched, 'fx_rows': len(fx), 'unresolved_windows': failed,
            'calendar_coverage_verified': False, 'exact_agent_replay_ready': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, default=ROOT / 'research/point-in-time')
    parser.add_argument('--output', type=Path, default=ROOT / 'research/six-strategy-v1/archive-quality.json')
    args = parser.parse_args()
    result = {'verified_at_utc': datetime.now(timezone.utc).isoformat(),
              'source_run_id': 40494996964, 'period': ['2023-01-01', '2026-10-26 exclusive'],
              'years': [audit_year(args.base, year) for year in EXPECTED],
              'exact_agent_replay_ready': False,
              'unconfirmed_exact_inputs': ['Historical executable BBO', 'Historical margin, exchange fees and full contract-selection universe', 'Historical source/runner/Telegram availability', 'Confirmed trading calendar']}
    result['scenario_data_ready'] = all(not y['unresolved_windows'] and not y['request_errors'] for y in result['years'])
    result['status'] = 'CANDLES_VERIFIED_FOR_SCENARIOS' if result['scenario_data_ready'] else 'BLOCKED_PENDING_EMPTY_WINDOW_VERIFICATION'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
