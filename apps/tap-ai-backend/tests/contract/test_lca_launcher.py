"""Local LCA launch wiring; no services or credentials are created by these tests."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[4]


def test_browser_launcher_uses_standard_tap_app_and_scrubs_environment(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    launcher = scripts / "run-tap-browser.sh"
    launcher.write_text((ROOT / "scripts/run-tap-browser.sh").read_text())
    bins = tmp_path / ".venv/bin"
    bins.mkdir(parents=True)
    result = tmp_path / "launch.json"
    python = bins / "python"
    python.write_text(f"""#!{sys.executable}
import sys
if '-c' in sys.argv and 'urlsplit' in sys.argv[2]: print('18002')
""")
    uvicorn = bins / "uvicorn"
    uvicorn.write_text(f"""#!{sys.executable}
import json,os,sys
from pathlib import Path
Path({str(result)!r}).write_text(json.dumps({{"args":sys.argv[1:],"env":dict(os.environ)}}))
""")
    python.chmod(0o700)
    uvicorn.chmod(0o700)
    env = {
        **os.environ,
        "TAP_LCA_ENABLED": "true",
        "TAP_LCA_GATEWAY_TOKEN": "l" * 32,
        "TAP_BROWSER_DATABASE_URL": "mysql+pymysql://synthetic/owned",
        "TAP_BROWSER_OBJECT_ROOT": str(tmp_path / "objects"),
        "TAP_BROWSER_PROJECT_ID": "p",
        "TAP_BROWSER_ACCESS_TOKEN": "a" * 32,
        "TAP_BROWSER_TOKEN_EXPIRES_AT": "2099-01-01T00:00:00Z",
        "TAP_BROWSER_GATEWAY_TOKEN": "g" * 32,
        "TAP_BROWSER_WORKER_TOKEN": "w" * 32,
        "TAP_BROWSER_EXECUTION_MODE": "local",
        "TAP_BROWSER_LOCAL_UPSTREAM_PROXY": "http://127.0.0.1:7897",
        "TAP_BROWSER_API_ORIGIN": "http://127.0.0.1:18002",
        "OPENAI_API_KEY": "must-not-inherit",
    }
    subprocess.run(["bash", str(launcher), "api"], env=env, check=True, capture_output=True)
    captured = json.loads(result.read_text())
    assert captured["args"] == ["tap_platform.app:app", "--host", "127.0.0.1", "--port", "18002"]
    assert captured["env"]["TAP_LCA_GATEWAY_TOKEN"] == "l" * 32
    assert captured["env"]["TAP_BROWSER_GATEWAY_TOKEN"] == "g" * 32
    assert captured["env"]["TAP_BROWSER_EXECUTION_MODE"] == "local"
    assert "OPENAI_API_KEY" not in captured["env"]
