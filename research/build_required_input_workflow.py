"""Standalone data-only workflow. Does not change live financial or signal code."""
import ast
import textwrap
from pathlib import Path
from load_acquisition_definitions import acquisition_body

ROOT = Path(__file__).resolve().parents[1]


def script():
    nodes = ast.parse((ROOT / 'research/collect_required_inputs.py').read_text()).body
    functions = '\n\n'.join(ast.unparse(n) for n in nodes if isinstance(n, ast.FunctionDef))
    result = acquisition_body() + '\nac = sys.modules[__name__]\n' + functions + '\nsys.exit(required_inputs_main(FROZEN_CONTRACTS))\n'
    compile(result, '<required-input-acquisition>', 'exec')
    return result


def build():
    header = '''# Additional research data only. No orders, Telegram or strategy fitting.
name: AlgoPack Research Required Inputs
on:
  push:
    branches: [main]
    paths: ['.github/workflows/algopack-research-required-inputs.yml']
  workflow_dispatch:
permissions:
  contents: read
concurrency:
  group: algopack-research-required-inputs
  cancel-in-progress: false
jobs:
  acquire:
    if: github.repository == 'ataruua-tech/-Br-gold'
    runs-on: ubuntu-latest
    timeout-minutes: 120
    strategy:
      fail-fast: false
      max-parallel: 1
      matrix:
        include:
          - {mode: catalogue, year: 2026}
          - {mode: panels, year: 2025}
          - {mode: panels, year: 2026}
          - {mode: panels, year: 2023}
          - {mode: panels, year: 2024}
    steps:
      - name: Acquire official missing inputs without fitting strategies
        env:
          MOEX_ALGOPACK_TOKEN: ${{ secrets.MOEX_ALGOPACK_TOKEN }}
          INPUT_MODE: ${{ matrix.mode }}
          INPUT_YEAR: ${{ matrix.year }}
          PYTHONUNBUFFERED: '1'
        run: |
'''
    footer = '''      - name: Retain official pages, hashes, coverage and errors
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: algopack-required-inputs-${{ matrix.mode }}-${{ matrix.year }}-${{ github.run_id }}-${{ github.run_attempt }}
          path: required-input-evidence/
          if-no-files-found: error
          retention-days: 90
'''
    return header + textwrap.indent("python3 - <<'PY'\n" + script() + 'PY\n', '          ') + footer


if __name__ == '__main__':
    target = ROOT / '.github/workflows/algopack-research-required-inputs.yml'
    target.write_text(build())
    print(target)
