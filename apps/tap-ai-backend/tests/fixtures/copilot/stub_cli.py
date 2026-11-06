"""Standard-library subprocess behaviors for probe capture tests."""

import os
import subprocess
import sys
import time
from pathlib import Path

mode = sys.argv[1]
if mode == "interleaved":
    os.write(1, b'{"event":"fir')
    os.write(2, b"warning one\n")
    os.write(1, b'st"}\n')
    os.write(2, b"warning two\n")
    os.write(1, b'{"event":"second"}\n')
elif mode == "invalid":
    os.write(1, b"{invalid}\n")
elif mode == "long":
    os.write(1, b"x" * 100_000 + b"\n")
elif mode == "many":
    for _ in range(100):
        os.write(1, b"x" * 1024 + b"\n")
        os.write(2, b"y" * 1024 + b"\n")
elif mode == "nonzero":
    raise SystemExit(7)
elif mode in {"orphan", "wait"}:
    child = subprocess.Popen((sys.executable, "-c", "import time; time.sleep(30)"))
    Path("child.pid").write_text(str(child.pid))
    if mode == "wait":
        time.sleep(30)
else:
    raise SystemExit(3)
