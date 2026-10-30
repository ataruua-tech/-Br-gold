"""Minimal lossless converter for two named H1 series; no trading engine/network."""
import argparse
from datetime import datetime, timedelta
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import zipfile

ARCHIVES = {
    2025: (12734831380, "d8eff7373c9bcaafb570fbd24a44d84709353112396f90c62bd299e86b003253"),
    2026: (12735884135, "b8344fbe990dd1ae069ceef3ae0709ca12375a7205a26f1360e3ba34b66b1ffb"),
}

def sha(data):
    return hashlib.sha256(data).hexdigest()

def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

def prepare(archives, output):
    output.mkdir(parents=True, exist_ok=True)
    (output / "candles").mkdir(exist_ok=True)
    manifest = {"archives": [], "series": {}, "calendar_complete": False, "no_stitching": True}
    for year, (artifact, expected) in ARCHIVES.items():
        path = archives / f"source_{year}.zip"
        raw_zip = path.read_bytes()
        actual = sha(raw_zip)
        assert actual == expected, (path, actual, expected)
        manifest["archives"].append({"year": year, "artifact": artifact, "sha256": actual, "bytes": len(raw_zip)})
    with zipfile.ZipFile(archives / "source_2026.zip") as z:
        original_manifest = json.loads(z.read("manifest.json"))
        for symbol in ("BRX6", "GDZ6"):
            entry = next(r for r in original_manifest["history"] if r["contract"] == symbol)
            name = symbol + "-H1.json.gz"
            file_meta = next(r for r in entry["files"] if r["file"] == name)
            raw = gzip.decompress(z.read(name))
            assert sha(raw) == file_meta["uncompressed_sha256"]
            rows = json.loads(raw, parse_float=Decimal)
            assert len(rows) == file_meta["rows"]
            previous = None
            lines, gaps, times = [], [], []
            for row in rows:
                t = datetime.fromisoformat(row["begin"])
                assert t.minute == t.second == 0
                assert t < datetime(2026, 10, 24)
                assert previous is None or t > previous
                o, h, l, c, v = [Decimal(str(row[k])) for k in ("open", "high", "low", "close", "volume")]
                assert all(x.is_finite() for x in (o, h, l, c, v))
                assert 0 < l <= min(o, c) <= max(o, c) <= h and v >= 0
                if previous and (t - previous).total_seconds() > 3600:
                    gaps.append({"previous_begin": previous.isoformat(), "next_begin": t.isoformat(), "seconds": int((t-previous).total_seconds()), "cause": "Not established: session/calendar/no trades/source coverage"})
                line = t.strftime("%Y%m%d,%H%M%S,") + ",".join(str(x) for x in (o,h,l,c,v))
                parts = line.split(",")
                assert datetime.strptime(parts[0]+parts[1], "%Y%m%d%H%M%S") == t
                assert [Decimal(x) for x in parts[2:]] == [o,h,l,c,v]
                lines.append(line)
                times.append({"begin": row["begin"], "raw_end": row.get("end"), "information_available_at_msk": (t+timedelta(hours=1)).isoformat()+"+03:00"})
                previous = t
            data = ("\n".join(lines)+"\n").encode("ascii")
            (output / "candles" / (symbol + ".txt")).write_bytes(data)
            (output / (symbol + "-raw.json")).write_bytes(raw)
            write_json(output / (symbol + "-timing.json"), times)
            write_json(output / (symbol + "-gaps.json"), gaps)
            manifest["series"][symbol] = {"rows": len(rows), "first_begin": rows[0]["begin"], "last_begin": rows[-1]["begin"], "last_information_at_msk": times[-1]["information_available_at_msk"], "input_sha256": sha(raw), "native_text_sha256": sha(data), "round_trip_checked_rows": len(rows), "adjacent_gaps_over_hour": len(gaps), "source_window": entry, "processed_all_source_h1_including_warmup": True}
        write_json(output / "original-manifest-2026.json", original_manifest)
    write_json(output / "data-import-manifest.json", manifest)
    print(json.dumps({s:{k:v[k] for k in ("rows","first_begin","last_begin","adjacent_gaps_over_hour")} for s,v in manifest["series"].items()}))
    return manifest

if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--archives", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a=p.parse_args()
    prepare(a.archives, a.output)
