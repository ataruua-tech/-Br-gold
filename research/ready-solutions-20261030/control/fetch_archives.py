"""Retrieve the already acquired GitHub artifacts; never contacts MOEX."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
from prepare_data import ARCHIVES

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

p=argparse.ArgumentParser()
p.add_argument("--output", type=Path, required=True)
a=p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
token=os.environ["ARCHIVE_READ_TOKEN"]
opener=urllib.request.build_opener(NoRedirect())
for year,(artifact,expected) in ARCHIVES.items():
    path=a.output/f"source_{year}.zip"
    if not path.exists():
        request=urllib.request.Request(f"https://api.github.com/repos/ataruua-tech/-Br-gold/actions/artifacts/{artifact}/zip", headers={"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"})
        try:
            with opener.open(request, timeout=60) as r:
                raw=r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (301,302,303,307,308):
                raise RuntimeError(f"Artifact retrieval failed HTTP {e.code}") from None
            location=e.headers["Location"]
            # Never forward GitHub Authorization to signed storage; never persist URL.
            with urllib.request.urlopen(location, timeout=180) as r:
                raw=r.read()
        path.write_bytes(raw)
    raw=path.read_bytes()
    actual=hashlib.sha256(raw).hexdigest()
    assert actual==expected, (year,actual,expected)
    print(json.dumps({"year":year,"artifact":artifact,"sha256":actual,"bytes":len(raw)}))
