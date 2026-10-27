import ast
import textwrap
from pathlib import Path
from load_acquisition_definitions import acquisition_body

ROOT = Path(__file__).resolve().parents[1]


def build():
    audit = ast.parse((ROOT / 'research/audit_required_input_artifacts.py').read_text()).body
    helpers = '\n\n'.join(ast.unparse(n) for n in audit if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in ('NoAuthRedirect', 'github_get', 'emit_record'))
    nodes = ast.parse((ROOT / 'research/collect_full_universe.py').read_text()).body
    definitions = '\n\n'.join(ast.unparse(n) for n in nodes if isinstance(n, ast.FunctionDef) or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SOURCE_ARCHIVES' for t in n.targets))
    script = acquisition_body() + '\nimport base64\nimport io\nimport zipfile\nac = sys.modules[__name__]\n' + helpers + '\n' + definitions + '\nsys.exit(full_universe_main())\n'
    compile(script, '<full-universe-data-acquisition>', 'exec')
    header = '''# Fill missing listed-contract TradeStats/H1 spans, reusing verified archives.
name: AlgoPack Research Full Universe
on:
  push:
    branches: [main]
    paths: ['.github/workflows/algopack-research-full-universe.yml']
  workflow_dispatch:
permissions:
  contents: read
  actions: read
concurrency:
  group: algopack-research-full-universe
  cancel-in-progress: false
jobs:
  collect:
    if: github.repository == 'ataruua-tech/-Br-gold'
    runs-on: ubuntu-latest
    timeout-minutes: 120
    steps:
      - name: Reuse caches and acquire missing full-universe ranges
        env:
          GH_TOKEN: ${{ github.token }}
          MOEX_ALGOPACK_TOKEN: ${{ secrets.MOEX_ALGOPACK_TOKEN }}
          PYTHONUNBUFFERED: '1'
        run: |
'''
    footer = '''      - name: Preserve full scope, source pages, coverage and errors
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: algopack-research-full-universe-${{ github.run_id }}-${{ github.run_attempt }}
          path: universe-evidence/
          if-no-files-found: error
          retention-days: 90
'''
    return header + textwrap.indent("python3 - <<'PY'\n" + script + 'PY\n', '          ') + footer


if __name__ == '__main__':
    destination = ROOT / '.github/workflows/algopack-research-full-universe.yml'
    destination.write_text(build())
    print(destination)
