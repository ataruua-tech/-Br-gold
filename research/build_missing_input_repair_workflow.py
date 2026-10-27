import ast
import textwrap
from pathlib import Path
from load_acquisition_definitions import acquisition_body

ROOT = Path(__file__).resolve().parents[1]


def build():
    audit_nodes = ast.parse((ROOT / 'research/audit_required_input_artifacts.py').read_text()).body
    helpers = '\n\n'.join(ast.unparse(n) for n in audit_nodes if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in ('github_get', 'NoAuthRedirect', 'emit_record'))
    repair_nodes = ast.parse((ROOT / 'research/repair_missing_inputs.py').read_text()).body
    repair = '\n\n'.join(ast.unparse(n) for n in repair_nodes if isinstance(n, ast.FunctionDef) or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ('AUDIT_RUN', 'AUDIT_ARTIFACT_ID', 'AUDIT_SHA256') for t in n.targets))
    script = acquisition_body() + '\nimport base64\nimport io\nimport zipfile\nac = sys.modules[__name__]\n' + helpers + '\n' + repair + '\nsys.exit(repair_main())\n'
    compile(script, '<missing-input-repair>', 'exec')
    header = '''# Resume only interrupted APIM ranges from verified archived prefixes.
name: AlgoPack Research Repair Missing Inputs
on:
  push:
    branches: [main]
    paths: ['.github/workflows/algopack-research-repair-inputs.yml']
  workflow_dispatch:
permissions:
  contents: read
  actions: read
concurrency:
  group: algopack-research-repair-inputs
  cancel-in-progress: false
jobs:
  repair:
    if: github.repository == 'ataruua-tech/-Br-gold'
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - name: Resume exact missing pages and verify metadata capabilities
        env:
          GH_TOKEN: ${{ github.token }}
          MOEX_ALGOPACK_TOKEN: ${{ secrets.MOEX_ALGOPACK_TOKEN }}
          PYTHONUNBUFFERED: '1'
        run: |
'''
    footer = '''      - name: Preserve repaired inputs, source hashes and errors
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: algopack-research-repaired-inputs-${{ github.run_id }}-${{ github.run_attempt }}
          path: repair-evidence/
          if-no-files-found: error
          retention-days: 90
'''
    return header + textwrap.indent("python3 - <<'PY'\n" + script + 'PY\n', '          ') + footer


if __name__ == '__main__':
    target = ROOT / '.github/workflows/algopack-research-repair-inputs.yml'
    target.write_text(build())
    print(target)
