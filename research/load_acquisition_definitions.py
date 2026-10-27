"""Load definitions from the pinned standalone workflow, without acquiring data."""
import hashlib
import sys
import textwrap
import types
from pathlib import Path

WORKFLOW_BLOB = 'e7ca5b8bf52425594d0cea9329453b808e995b5c'


def acquisition_body(root=None):
    root = root or Path(__file__).resolve().parents[1]
    raw = (root / '.github/workflows/algopack-research-data.yml').read_bytes()
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if blob != WORKFLOW_BLOB:
        raise ValueError('Unreviewed research acquisition source')
    body = textwrap.dedent(raw.decode().split("python3 - <<'PY'\n", 1)[1].split('\n          PY\n', 1)[0])
    body, suffix = body.rsplit('sys.exit(acquisition_main(FROZEN_CONTRACTS))', 1)
    if suffix.strip():
        raise ValueError('Unexpected executable acquisition suffix')
    return body


def load_acquisition():
    name = '_br_gold_frozen_acquisition'
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    sys.modules[name] = module
    try:
        exec(compile(acquisition_body(), '<pinned-acquisition-definitions>', 'exec'), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module
