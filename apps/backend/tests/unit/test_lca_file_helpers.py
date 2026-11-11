import pytest
import subprocess
from tap_platform.lca.file_actions import FILE_ACTION_HELPER


def test_timed_out_download_never_attaches_a_late_stream(tmp_path):
    script = (
        """
import {EventEmitter} from 'node:events';
import assert from 'node:assert/strict';
let attached=0, destroyed=0, release;
const test={info:()=>({attach:async()=>{attached++;}})};
const page=new EventEmitter();
const stream={destroy:()=>{destroyed++;},async *[Symbol.asyncIterator](){yield Buffer.from('late');}};
const download={createReadStream:()=>new Promise(resolve=>{release=resolve;}),cancel:async()=>{},delete:async()=>{},suggestedFilename:()=> 'late.txt'};
const locator={click:async()=>{page.emit('download',download);}};
"""
        + FILE_ACTION_HELPER
        + """
await assert.rejects(captureDownload(page,locator,'late',20),/timed out/);
assert.equal(page.listenerCount('download'),0);
release(stream);
await new Promise(resolve=>setTimeout(resolve,20));
assert.equal(attached,0);
assert.equal(destroyed,1);
"""
    )
    path = tmp_path / "late-download.mjs"
    path.write_text(script)
    result = subprocess.run(
        ["node", str(path)], text=True, capture_output=True, timeout=5
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_runtime_materializes_exact_canonical_input_limit(tmp_path):
    import hashlib
    import json
    from types import SimpleNamespace
    from tap_platform.browser_automation.contracts import BrowserBundle, BrowserLimits
    from tap_platform.browser_automation.docker import (
        BrowserRuntime,
        SubprocessDockerClient,
    )
    from tap_platform.browser_automation.service import canonical_bytes

    inputs = {"value": "x" * 999988}
    frozen = canonical_bytes(inputs)
    assert len(frozen) == 1_000_000
    bundle = BrowserBundle(
        "b",
        "p",
        hashlib.sha256(b"fixture").hexdigest(),
        b"fixture",
        hashlib.sha256(frozen).hexdigest(),
        ("https://example.com",),
        "now",
    )
    runtime = BrowserRuntime(
        SubprocessDockerClient(), "unused", evidence_sink=object(), project_id="p"
    )

    class Inspected(Exception):
        pass

    async def inspect_input(folder, *_):
        content = (folder / "inputs.json").read_bytes()
        assert content == frozen
        assert json.loads(content) == inputs
        raise Inspected

    runtime._run_replay = inspect_input
    with pytest.raises(Inspected):
        await runtime._execute_work(
            bundle,
            {"input_data": inputs},
            BrowserLimits(),
            "e",
            SimpleNamespace(task_id="t"),
        )
