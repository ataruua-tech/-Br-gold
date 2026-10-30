"""Package local native test evidence; output an offline transfer copy in CI log."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import zipfile

p=argparse.ArgumentParser()
p.add_argument("--output", type=Path, required=True)
a=p.parse_args()
files=[x for x in a.output.rglob("*") if x.is_file() and x.name!="portable-evidence.zip" and x.suffix not in (".cs", ".dll", ".exe", ".pdb") and "McpSettings" not in x.name]
manifest={str(x.relative_to(a.output)).replace("\\","/"):{"bytes":x.stat().st_size,"sha256":hashlib.sha256(x.read_bytes()).hexdigest()} for x in files}
(a.output/"evidence-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
files.append(a.output/"evidence-manifest.json")
archive=a.output/"portable-evidence.zip"
with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for x in files:
        z.write(x, str(x.relative_to(a.output)))
raw=archive.read_bytes()
print("BR_CONTROL_ARCHIVE "+json.dumps({"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}))
# Explicitly marked transport; complete evidence also retained by upload-artifact.
# Only this job's research files, never environment/credentials or broker settings.
if len(raw)<=5000000:
    print("BR_CONTROL_B64_BEGIN")
    b64=base64.b64encode(raw).decode("ascii")
    for i in range(0,len(b64),6000): print(b64[i:i+6000])
    print("BR_CONTROL_B64_END")
