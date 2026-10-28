import ast
import re
import textwrap
from pathlib import Path
from load_acquisition_definitions import acquisition_body

ROOT = Path(__file__).resolve().parents[1]


def build(resume_id, resume_sha256):
    if not isinstance(resume_id, int) or resume_id <= 0 or not re.fullmatch('[0-9a-f]{64}', resume_sha256):
        raise ValueError('A confirmed GitHub artifact ID and SHA256 are required')
    pieces = [acquisition_body(), 'import base64\nimport io\nimport zipfile\nfrom collections import Counter, defaultdict\nac = sys.modules[__name__]']
    for filename, names in [
        ('audit_required_input_artifacts.py', {'NoAuthRedirect', 'github_get', 'emit_record'}),
        ('collect_full_universe.py', {'verified_zip', 'cached_payload', 'merge_rows'}),
        ('resume_full_universe.py', {'checked_archive'}),
        ('audit_data_sufficiency.py', None),
    ]:
        nodes = ast.parse((ROOT / 'research' / filename).read_text()).body
        pieces.extend(ast.unparse(n) for n in nodes if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and (names is None or n.name in names)
                      or names is None and isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SUFFICIENCY_REFERENCES' for t in n.targets))
    pieces.append("SUFFICIENCY_REFERENCES['resume'] = " + repr((resume_id, resume_sha256)))
    script = '\n\n'.join(pieces) + '\nsys.exit(sufficiency_main())\n'
    compile(script, '<data-sufficiency-audit>', 'exec')
    header = '''# Offline archive reconciliation only: no MOEX requests, no fits or Telegram.
name: AlgoPack Research Data Sufficiency
on:
  push:
    branches: [main]
    paths: ['.github/workflows/algopack-research-sufficiency.yml']
  workflow_dispatch:
permissions:
  contents: read
  actions: read
concurrency:
  group: algopack-research-data-audit
  cancel-in-progress: false
jobs:
  verify:
    if: github.repository == 'ataruua-tech/-Br-gold'
    runs-on: ubuntu-latest
    timeout-minutes: 25
    steps:
      - name: Reconcile verified archives and quantify actual data limitations
        env:
          GH_TOKEN: ${{ github.token }}
          PYTHONUNBUFFERED: '1'
        run: |
'''
    footer = '''      - name: Preserve complete sufficiency assessment
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: algopack-research-sufficiency-${{ github.run_id }}-${{ github.run_attempt }}
          path: sufficiency-evidence/
          if-no-files-found: error
          retention-days: 90
'''
    return header + textwrap.indent("python3 - <<'PY'\n" + script + 'PY\n', '          ') + footer


if __name__ == '__main__':
    import sys
    (ROOT / '.github/workflows/algopack-research-sufficiency.yml').write_text(build(int(sys.argv[1]), sys.argv[2]))
