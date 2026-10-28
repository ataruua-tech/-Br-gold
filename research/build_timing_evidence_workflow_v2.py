import ast
import textwrap
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def build():
    pieces=['import base64,hashlib,io,json,os,urllib.error,urllib.parse,urllib.request,zipfile\nfrom pathlib import Path']
    for filename,names in [('audit_required_input_artifacts.py',{'NoAuthRedirect','github_get','emit_record'}),('verify_timing_evidence_v2.py',None)]:
        nodes=ast.parse((ROOT/'research'/filename).read_text()).body
        pieces.extend(ast.unparse(n) for n in nodes if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and (names is None or n.name in names)
                      or names is None and isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TIMING_ARTIFACTS' for t in n.targets))
    script='\n\n'.join(pieces)+'\ntiming_main()\n'
    compile(script,'<readonly-timing-evidence>','exec')
    header='''name: AlgoPack Research Timing Evidence
on:
  push:
    branches: [main]
    paths: ['.github/workflows/algopack-research-timing-evidence.yml']
  workflow_dispatch:
permissions:
  contents: read
  actions: read
jobs:
  audit:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Verify existing archived timing observations
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
'''
    footer='''      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: research-timing-evidence-${{ github.run_id }}
          path: timing-evidence/
          retention-days: 90
'''
    return header+textwrap.indent("python3 - <<'PY'\n"+script+'PY\n','          ')+footer


if __name__=='__main__':(ROOT/'.github/workflows/algopack-research-timing-evidence.yml').write_text(build())
