import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    source = (ROOT / 'research/audit_required_input_artifacts.py').read_text()
    compile(source, '<input-archive-audit>', 'exec')
    header = '''# Download and verify research archives inside GitHub. No market secrets or fitting.
name: AlgoPack Research Audit Inputs
on:
  push:
    branches: [main]
    paths: ['.github/workflows/algopack-research-audit-inputs.yml']
  workflow_dispatch:
permissions:
  contents: read
  actions: read
concurrency:
  group: algopack-research-audit-inputs
  cancel-in-progress: false
jobs:
  audit:
    if: github.repository == 'ataruua-tech/-Br-gold'
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - name: Verify source, artifact and payload hashes and inspect missing inputs
        env:
          GH_TOKEN: ${{ github.token }}
          PYTHONUNBUFFERED: '1'
        run: |
'''
    footer = '''      - name: Preserve verified coverage reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: algopack-research-input-audit-${{ github.run_id }}-${{ github.run_attempt }}
          path: input-audit-evidence/
          if-no-files-found: error
          retention-days: 90
'''
    return header + textwrap.indent("python3 - <<'PY'\n" + source + '\nPY\n', '          ') + footer


if __name__ == '__main__':
    destination = ROOT / '.github/workflows/algopack-research-audit-inputs.yml'
    destination.write_text(build())
    print(destination)
