"""One packaged probe bundle for collection, live admission and corpus review."""
import hashlib
from pathlib import Path

PROBE_VERSION = 4
ASSETS = ('canvas_chain_probe.js', 'render_integration_probe.js',
          'gpu_backend_probe.js', 'device_render_probe.js', 'device_probe.js')


def probe_source():
    root = Path(__file__).parent
    return '\n'.join((root / name).read_text(encoding='utf-8') for name in ASSETS)


def probe_hash():
    return hashlib.sha256(probe_source().encode('utf-8')).hexdigest()
