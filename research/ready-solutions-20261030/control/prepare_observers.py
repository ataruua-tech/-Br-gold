"""Append observation-only subclasses to exact, private native Custom copies."""
import argparse
import hashlib
import json
from pathlib import Path

def sha(data):
    return hashlib.sha256(data).hexdigest()

def blob_sha(data):
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()

parser=argparse.ArgumentParser()
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args=parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
robots=args.source / "project/OsEngine/bin/Debug/Custom/Robots"
template=Path(__file__).with_name("observer-template.txt").read_text(encoding="utf-8")
manifest=[]
for name, namespace, expected in [
    ("IntersectionOfSuperTrends", "OsEngine.Robots.MyBots", "3c43decdbcd4ebd55e6a3fb2ffe60009d1421415"),
    ("CountertrendBollingerRSI", "OsEngine.Robots", "a5915e3639d28089c5c0effc6179838cd35f5734"),
]:
    source=robots/(name+".cs")
    raw=source.read_bytes()
    # checkout must use autocrlf=false: exact bytes are evidence, not a normalized rewrite.
    assert blob_sha(raw)==expected, (source, blob_sha(raw), expected)
    (args.output/(name+".original.cs")).write_bytes(raw)
    appended=template.replace("__ORIGINAL__", name).replace("__NAMESPACE__", namespace).encode("utf-8")
    observed=raw+appended
    destination=robots/("Audit"+name+".cs")
    destination.write_bytes(observed)
    assert observed[:len(raw)]==raw
    manifest.append({"source":name,"original_git_blob":expected,"original_sha256":sha(raw),"observer_sha256":sha(observed),"original_prefix_unchanged":True,"observer_only":True})
(args.output/"observer-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
print(json.dumps(manifest))
