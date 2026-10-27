"""Build a standalone, bounded APIM check using the pinned collector definitions."""
import ast
import textwrap
from pathlib import Path

from load_acquisition_definitions import acquisition_body

ROOT = Path(__file__).resolve().parents[1]


def script():
    nodes = ast.parse((ROOT / 'research/verify_empty_windows.py').read_text()).body
    definitions = [n for n in nodes if isinstance(n, ast.FunctionDef) or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'WINDOWS' for t in n.targets)]
    content = acquisition_body() + '\nac = sys.modules[__name__]\n' + '\n\n'.join(ast.unparse(n) for n in definitions) + '\nsys.exit(verify_empty_windows())\n'
    compile(content, '<empty-window-check>', 'exec')
    return content


def build():
    header = '''# Read-only verification. No orders, Telegram or live strategy changes.
name: AlgoPack Research Empty Windows
on:
  push:
    branches: [main]
    paths: ['.github/workflows/algopack-research-empty-windows.yml']
  workflow_dispatch:
permissions:
  contents: read
concurrency:
  group: algopack-research-empty-windows
  cancel-in-progress: false
jobs:
  verify:
    if: github.repository == 'ataruua-tech/-Br-gold'
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - name: Verify three source-empty M1 and H1 windows
        env:
          MOEX_ALGOPACK_TOKEN: ${{ secrets.MOEX_ALGOPACK_TOKEN }}
          PYTHONUNBUFFERED: '1'
        run: |
'''
    footer = '''      - name: Preserve official responses and limitations
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: algopack-research-empty-windows-${{ github.run_id }}-${{ github.run_attempt }}
          path: empty-window-evidence/
          if-no-files-found: error
          retention-days: 90
'''
    return header + textwrap.indent("python3 - <<'PY'\n" + script() + 'PY\n', '          ') + footer


if __name__ == '__main__':
    destination = ROOT / '.github/workflows/algopack-research-empty-windows.yml'
    destination.write_text(build())
    print(destination)
