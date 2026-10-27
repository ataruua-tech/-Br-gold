"""Bounded follow-up of three source-empty windows; no calendar inference."""
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from load_acquisition_definitions import load_acquisition

ac = load_acquisition()

WINDOWS = (
    ('BRF5', '2025-01-01', '2025-01-02'),
    ('BRF6', '2026-01-01', '2026-01-04'),
    ('BRZ6', '2026-10-25', '2026-10-26'),
)


def verify_empty_windows(directory=Path('empty-window-evidence'), fetch=None):
    client = ac.Acquisition(directory, fetch=fetch, max_requests=60)
    report = {'source': 'apim.moex.com', 'original_run_id': 40494996964,
              'orders_enabled': False, 'strategy_changed': False,
              'calendar_coverage_verified': False,
              'exact_agent_replay_ready': False, 'windows': [], 'errors': []}
    for contract, first, end in WINDOWS:
        row = {'contract': contract, 'first': first, 'end_exclusive': end}
        try:
            first_date, end_date = date.fromisoformat(first), date.fromisoformat(end)
            minutes = client.candles(contract, 1, first_date, end_date)
            hours = client.candles(contract, 60, first_date, end_date)
            row['files'] = [client.save(contract + '-M1.json.gz', minutes),
                            client.save(contract + '-H1.json.gz', hours)]
            row.update(m1_rows=len(minutes), h1_rows=len(hours))
            if not minutes and not hours:
                row['status'] = 'SOURCE_EMPTY_BOTH_INTERVALS'
                row['interpretation'] = 'Observed empty API responses only; neither a confirmed closed session nor a missing zero-volume candle'
            elif minutes and hours:
                row['crosscheck'] = ac.acquisition_crosscheck(minutes, hours)
                c = row['crosscheck']
                covered = {ac.bg.parse_iss_time(r['begin']).replace(minute=0) for r in minutes}
                missing = [r['begin'] for r in hours if ac.bg.parse_iss_time(r['begin']) not in covered and float(r['volume']) > 0]
                row['nonempty_h1_missing_all_m1'] = missing
                row['status'] = 'REACQUIRED_RECONCILED' if c['matching_h1'] and not c['mismatched_h1'] and not c['m1_hours_missing_source_h1'] and not missing else 'COVERAGE_ERROR'
            else:
                row['status'] = 'COVERAGE_ERROR'
            if row['status'] == 'COVERAGE_ERROR':
                report['errors'].append(contract + ': incompatible M1/H1 coverage')
        except Exception as exc:
            row.update(status='ERROR', error=ac.bg.safe_error(exc))
            report['errors'].append(contract + ': ' + row['error'])
            if isinstance(exc, ac.AcquisitionStop):
                report['windows'].append(row)
                break
        report['windows'].append(row)
    report['status'] = 'WINDOW_RESPONSES_VERIFIED_CALENDAR_UNCONFIRMED' if len(report['windows']) == len(WINDOWS) and not report['errors'] else 'BLOCKED_DATA'
    report['requests'] = client.count
    report['verified_at_utc'] = datetime.now(timezone.utc).isoformat()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'manifest.json').write_bytes(ac.acquisition_bytes(report))
    print(json.dumps(report, ensure_ascii=False))
    return 1 if report['status'] == 'BLOCKED_DATA' else 0


if __name__ == '__main__':
    raise SystemExit(verify_empty_windows())
