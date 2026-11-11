"""Keep pytest temporary roots canonical on macOS without weakening Store guards.

macOS exposes /var as a symlink to /private/var.  Files produced by the
standard tempfile module or pytest tmp_path should live under the real path.
Explicit symlink paths must still be rejected by production Store.
"""
import os
import tempfile
from pathlib import Path

# Set this before pytest collects tests, including unittest-style fixtures.
# Propagate to subprocesses that call tempfile.gettempdir() independently.
_real_tmpdir = str(Path(tempfile.gettempdir()).resolve())
tempfile.tempdir = _real_tmpdir
os.environ["TMPDIR"] = _real_tmpdir
