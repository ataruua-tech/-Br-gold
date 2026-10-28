import ast
import textwrap
from pathlib import Path
from load_acquisition_definitions import acquisition_body

ROOT = Path(__file__).resolve().parents[1]


def build():
    pieces = [acquisition_body(), 'import base64\nimport io\nimport zipfile\nac = sys.modules[__name__]']
    for filename, selected in [
        ('audit_required_input_artifacts.py', {'NoAuthRedirect', 'github_get', 'emit_record'}),
        ('collect_full_universe.py', {'verified_zip', 'cached_payload', 'merge_rows'}),
        ('resume_full_universe.py', None),
    ]:
        nodes = ast.parse((ROOT / 'research' / filename).read_text()).body
        pieces.extend(ast.unparse(n) for n in nodes if
                      isinstance(n, (ast.FunctionDef, ast.ClassDef)) and (selected is None or n.name in selected)
                      or selected is None and isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'RESUME_REFERENCES' for t in n.targets))
    script = '\n\n'.join(pieces) + '\nsys.exit(resume_main())\n'
    compile(script, '<sixteen-record-resume>', 'exec')
    header = '''# Resume only 16 incomplete records; keep 222 completed records in their verified archive.
name: AlgoPack Research Resume Sixteen
on:
  push:
    branches: [main]
    paths: ['.github/workflows/algopack-research-resume-sixteen.yml']
  workflow_dispatch:
permissions:
  contents: read
  actions: read
concurrency:
  group: algopack-research-full-universe
  cancel-in-progress: false
jobs:
  resume:
    if: github.repository == 'ataruua-tech/-Br-gold'
    runs-on: ubuntu-latest
    timeout-minutes: 45
    steps:
      - name: Verify checkpoint and resume only incomplete records
        env:
          GH_TOKEN: ${{ github.token }}
          MOEX_ALGOPACK_TOKEN: ${{ secrets.MOEX_ALGOPACK_TOKEN }}
          PYTHONUNBUFFERED: '1'
        run: |
'''
    footer = '''      - name: Preserve resume data, raw pages, hashes and errors
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: algopack-research-resume-sixteen-${{ github.run_id }}-${{ github.run_attempt }}
          path: universe-resume-evidence/
          if-no-files-found: error
          retention-days: 90
'''
    return header + textwrap.indent("python3 - <<'PY'\n" + script + 'PY\n', '          ') + footer


if __name__ == '__main__':
    (ROOT / '.github/workflows/algopack-research-resume-sixteen.yml').write_text(build())
