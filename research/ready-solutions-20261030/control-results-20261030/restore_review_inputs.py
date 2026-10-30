"""Restore only archived evidence needed by reconcile_execution.py, offline."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
root=Path(__file__).parent
manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))['files']
for name,meta in manifest.items():
    data=(root/name).read_bytes()
    assert len(data)==meta['bytes'] and hashlib.sha256(data).hexdigest()==meta['sha256'],name
a.output.mkdir(parents=True,exist_ok=False)
(a.output/'prepared').mkdir()
for symbol in ('BRX6','GDZ6'):
    (a.output/'prepared'/(symbol+'-raw.json')).write_bytes(gzip.decompress((root/'data'/(symbol+'-H1.json.gz')).read_bytes()))
for run in ('A_BRX6','A_GDZ6','B_BRX6','B_GDZ6'):
    dest=a.output/'runs'/run/'ControlAudit'
    dest.mkdir(parents=True)
    (dest/'native-events.jsonl').write_bytes(gzip.decompress((root/'journals'/run/'native-events.jsonl.gz').read_bytes()))
print('Verified hashes and restored four native logs plus two source series; no native rerun or network access.')
