"""Opt-in rendered LCA recording → edit → frozen replay through real gateways.

Only the authenticated local validation scope is synthetic. The actual web entry,
HTTP routes, capability grants, worker, Chromium actions and receipts are real.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import signal
import socket
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import create_engine

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.interfaces.http.routes.lca import LcaGateway
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.workflows.adapters.browser_client import BrowserApiClient

pytestmark = pytest.mark.skipif(
    os.environ.get("TAP_RUN_LCA_RECORDING_UI") != "1",
    reason="explicit owned real Chromium LCA UI/gateway journey",
)
ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def recording_site():
    """Reuse the core acceptance fixture without importing backend code on skip."""
    spec = importlib.util.spec_from_file_location(
        "owned_lca_recording_fixture",
        ROOT / "apps/backend/tests/integration/test_lca_real_recording.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield from module.recording_site.__wrapped__()


def port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.mark.asyncio
async def test_rendered_record_edit_replay_and_restore(tmp_path, recording_site):
    from tap_platform.access import AccessPrincipal
    from tap_platform.browser_automation.http import create_browser_router
    from tap_platform.browser_automation.local import LocalBrowserRuntime
    from tap_platform.browser_automation.objects import BrowserEvidenceSink, BrowserObjectStore
    from tap_platform.browser_automation.service import BrowserService
    from tap_platform.browser_automation.store import BrowserStore
    from tap_platform.browser_automation.store import metadata as browser_metadata
    from tap_platform.browser_automation.worker import BrowserWorker
    from tap_platform.lca.http import DelegatedLcaAuthorizer, create_lca_router
    from tap_platform.lca.service import LcaService
    from tap_platform.lca.store import LcaStore
    from tap_platform.lca.store import metadata as lca_metadata

    site, submissions, first_submit, release_submit = recording_site
    engine = create_engine(f"sqlite:///{tmp_path / 'lca-ui.db'}")
    browser_metadata.create_all(engine)
    lca_metadata.create_all(engine)
    store = BrowserStore(engine)
    objects = BrowserObjectStore(tmp_path / "objects")
    browser_service = BrowserService(store, objects)
    lca_service = LcaService(LcaStore(engine), browser_service)
    principal = AccessPrincipal(
        project_id="p",
        principal_id="owned-browser-service",
        principal_type="service",
        audience="tap-browser",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        enabled=True,
        actions=frozenset(
            {
                "browser.sessions.create",
                "browser.sessions.read",
                "browser.sessions.cancel",
                "browser.sessions.control",
                "browser.actions.create",
                "browser.actions.read",
            }
        ),
    )
    scope = ProjectScopeContext(
        enterprise_id="local",
        project_id="p",
        actor_id="alice",
        identity_mode=IdentityMode.VALIDATION,
    )

    class Authority:
        async def current(self, facts):
            return scope

        async def authorize(self, actual_scope, action, resource):
            assert actual_scope == scope and action in {
                "lca.read",
                "lca.write",
                "lca.execute",
                "lca.publish",
            }
            return AuthorizationDecision(True, "owned-ui-fixture")

    core_port, gateway_port, vite_port = port(), port(), port()
    core_origin = f"http://127.0.0.1:{core_port}"
    ui_origin = f"http://127.0.0.1:{vite_port}"
    core = FastAPI()
    core.include_router(
        create_browser_router(browser_service, principal, "a" * 32, gateway_token="b" * 32)
    )
    core.include_router(create_lca_router(lca_service, DelegatedLcaAuthorizer("d" * 32, "p")))
    authority = Authority()
    gateway = create_app(
        HttpServices(
            scope=scope,
            scope_provider=authority,
            authorization_policy=authority,
            browser_client=BrowserApiClient(core_origin, "p", "a" * 32, gateway_token="b" * 32),
        ),
        allowed_origins=frozenset({ui_origin}),
        lca_gateway=LcaGateway(core_origin, "d" * 32),
    )
    servers = [
        uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=number, log_level="error"))
        for app, number in ((core, core_port), (gateway, gateway_port))
    ]
    serving = [asyncio.create_task(server.serve()) for server in servers]
    runtime = LocalBrowserRuntime(
        evidence_sink=BrowserEvidenceSink(store, objects), project_id="p", test_target=site
    )
    worker = BrowserWorker(browser_service, runtime, "p", "owned-rendered-lca-worker")
    stop = asyncio.Event()

    async def pump():
        while not stop.is_set():
            await worker.tick()
            await asyncio.sleep(0.03)

    pumping = asyncio.create_task(pump())
    vite = process = None
    logs = []
    try:
        async with asyncio.timeout(10):
            while not all(server.started for server in servers):
                await asyncio.sleep(0.02)
        vite = await asyncio.create_subprocess_exec(
            shutil.which("corepack"),
            "pnpm",
            "exec",
            "vite",
            "--host",
            "127.0.0.1",
            "--port",
            str(vite_port),
            "--strictPort",
            cwd=ROOT / "apps/web",
            env={**os.environ, "TAPPER_API_PORT": str(gateway_port)},
            start_new_session=True,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        async with asyncio.timeout(15):
            while True:
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", vite_port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.05)
        script = r"""
        import {chromium, expect} from '@playwright/test';
        import {createInterface} from 'node:readline';
        const input=createInterface({input:process.stdin});
        const submitReceived=new Promise(resolve=>input.once('line',resolve));
        const browser=await chromium.launch({headless:true});
        const context=await browser.newContext({viewport:{width:1280,height:960}});
        const errors=[];
        const commands=[];
        context.on('page',page=>{
          page.on('pageerror',error=>errors.push(String(error)));
          page.on('request',request=>{
            if(request.method()==='POST' && /\/recordings\/[^/]+\/actions$/.test(request.url()))
              commands.push(JSON.parse(request.postData()));
          });
        });
        const first=await context.newPage();
        let page=first;
        const go=async p=>{
          await p.goto(process.env.UI_ORIGIN+'/?module=low-code');
          await p.getByRole('heading',{name:'Low Code Automation',exact:true})
            .waitFor({timeout:30000});
        };
        const snapshot=async name=>{
          await page.screenshot({path:process.env.UI_SCREENSHOT+'/'+name+'.png',fullPage:true});
        };
        const action=async (type,selector,value)=>{
          await page.getByLabel('Recording operation').selectOption(type);
          if(type==='navigate') await page.getByLabel('Entry URL',{exact:true}).fill(value);
          else await page.getByLabel('Target CSS selector').fill(selector);
          if(type==='fill' || type==='select')
            await page.getByLabel('Synthetic input value').fill(value);
          await page.getByRole('button',{name:'Perform action',exact:true}).click();
          await expect(page.getByRole('list',{name:'Acknowledged recording actions'})
            .getByText(new RegExp('^'+type+': completed'))).toBeVisible({timeout:30000});
        };
        const save=async version=>{
          await page.getByRole('button',{name:'Save draft',exact:true}).click();
          const notice=`Draft saved as version ${version}. Saving did not start a replay.`;
          await expect(page.getByText(notice,{exact:true}))
            .toBeVisible({timeout:15000});
        };
        const replay=async (status,version)=>{
          await page.getByLabel('customer (string, required)').fill('replay-user');
          const admitted=page.waitForResponse(response=>response.request().method()==='POST'
            && /\/flows\/[^/]+\/runs$/.test(response.url()));
          await page.getByRole('button',{name:'Debug replay',exact:true}).click();
          const response=await admitted;
          if(response.status()!==202) throw new Error(await response.text());
          const run=await response.json();
          const receipt=page.getByRole('article',{name:'Run '+run.id,exact:true});
          await expect(receipt.locator('dt').filter({hasText:/^Execution$/}).locator('+ dd'))
            .toHaveText(status,{timeout:60000});
          await expect(receipt.getByRole('button',{name:'Download screenshot',exact:true}).first())
            .toBeVisible();
          await expect(receipt.getByRole('button',{name:'Download trace',exact:true}).first())
            .toBeVisible();
          const canonical=receipt.getByRole('heading',{name:'Canonical step results',exact:true})
            .locator('+ ol');
          await expect(canonical.locator('li')).toHaveCount(5);
          await expect(canonical.locator('li').last()).toContainText(' · '+status);
          await receipt.getByText('Frozen run definition',{exact:true}).click();
          await expect(receipt.getByText(new RegExp('Version '+version+' · Generator')))
            .toBeVisible();
          await snapshot('replay-'+version+'-'+status);
          return run;
        };
        try {
          await go(first);
          await page.getByRole('button',{name:'New debug flow'}).click();
          await page.getByLabel('Flow title').fill('Owned rendered recording');
          await page.getByLabel('Allowed origins (one per line)').fill('http://example.test');
          await page.getByLabel('Step open value',{exact:true}).fill('http://example.test/form');
          await save(1);
          await page.getByRole('button',{name:'Start recording',exact:true}).click();
          await page.getByRole('button',{name:'Take recording control',exact:true}).click();
          await expect.poll(async()=>{
            await page.getByRole('button',{name:'Refresh recording status'}).click();
            return page.getByText('Recording state: recording',{exact:true}).count();
          },{timeout:30000,intervals:[200,500]}).toBe(1);
          await page.getByRole('button',{name:'Refresh live frame'}).click();
          await action('navigate',null,'http://example.test/form');
          await action('fill','[data-testid="customer"]','recorded-user');
          await action('select','[data-testid="plan"]','Premium');
          await snapshot('recording-desktop');
          await page.setViewportSize({width:390,height:844});
          await snapshot('recording-mobile');
          if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))
            throw new Error('recording mobile horizontal overflow');
          await page.setViewportSize({width:1280,height:960});

          // A second real rendered tab restores the recording, so Stop can run
          // while the originating tab is awaiting the held submit acknowledgement.
          const second=await context.newPage();
          await go(second);
          await second.getByRole('navigation',{name:'Saved debug flows'})
            .getByRole('button',{name:'Owned rendered recording'}).click();
          await expect(second.getByRole('button',{name:/^Open recording /})).toHaveCount(1);
          await first.bringToFront();
          await first.getByRole('button',{name:'Refresh live frame'}).click();
          await first.getByLabel('Recording operation').selectOption('click');
          await first.getByLabel('Target CSS selector').fill('[data-testid="submit"]');
          const perform=first.getByRole('button',{name:'Perform action',exact:true});
          await expect(perform).toBeEnabled();
          // Same-tick double activation must be suppressed by the real UI lock.
          await perform.evaluate(button=>{button.click();button.click();});
          console.log('submit-requested');
          await submitReceived;
          await second.bringToFront();
          await second.getByRole('button',{name:/^Open recording /}).click();
          await second.getByRole('button',{name:'Stop and drain recording'}).click();
          await expect(second.getByText('Recording state: draining',{exact:true})).toBeVisible();
          await expect(second.getByRole('button',{name:'Import recorded draft'})).toHaveCount(0);
          console.log('recording-draining');
          page=second;
          await expect.poll(async()=>{
            if(await page.getByText('Recording state: stopped',{exact:true}).count()) return 1;
            await page.getByRole('button',{name:'Refresh recording status'}).click();
            return page.getByText('Recording state: stopped',{exact:true}).count();
          },{timeout:20000,intervals:[200,500]}).toBe(1);
          await expect(page.getByText('Recorded draft: 4 steps.',{exact:false})).toBeVisible();
          if(commands.filter(command=>command.action.type==='click').length!==1)
            throw new Error('duplicate UI submit was dispatched');
          await page.getByRole('button',{name:'Import recorded draft'}).click();
          await expect(page.getByRole('button',{name:'Debug replay',exact:true})).toBeDisabled();
          await page.getByText('Parameters (0)',{exact:true}).click();
          await page.getByRole('button',{name:'Add parameter',exact:true}).click();
          await page.getByLabel('Parameter name',{exact:true}).fill('customer');
          const steps=page.locator('.lca-steps > li');
          await steps.nth(1).getByLabel(/value source$/).selectOption('parameter');
          await expect(steps.nth(1).getByLabel(/parameter$/)).toHaveValue('customer');
          await page.getByText('Targets (3)',{exact:true}).click();
          await page.getByRole('button',{name:'Add target',exact:true}).click();
          await page.getByLabel('Locator value',{exact:true}).last().fill('result');
          await page.getByRole('button',{name:'Add assertion',exact:true}).click();
          const assertion=steps.last();
          await assertion.getByLabel(/ operation$/).selectOption('assert_text');
          const target=await assertion.getByLabel(/ target$/).locator('option')
            .last().getAttribute('value');
          await assertion.getByLabel(/ target$/).selectOption(target);
          await assertion.getByLabel(/ value$/).fill('replay-user / premium');
          await assertion.getByLabel(/ timeout$/).fill('1000');
          await save(2);
          console.log('edited-without-replay');
          const passed=await replay('passed',2);
          await assertion.getByLabel(/ value$/).fill('deliberately incorrect');
          await save(3);
          const failed=await replay('failed',3);
          await assertion.getByLabel(/ value$/).fill('replay-user / premium');
          await save(4);
          const corrected=await replay('passed',4);
          await page.reload();
          await page.getByRole('navigation',{name:'Saved debug flows'})
            .getByRole('button',{name:'Owned rendered recording'}).click();
          await expect(page.getByText('Saved version 4 · Saved',{exact:true})).toBeVisible();
          await expect(page.locator('.lca-steps > li')).toHaveCount(5);
          for(const run of [passed,failed,corrected])
            await expect(page.getByRole('button',{name:run.id,exact:true})).toBeVisible();
          await page.getByRole('button',{name:failed.id,exact:true}).click();
          const historic=page.getByRole('article',{name:'Run '+failed.id,exact:true});
          await expect(historic.locator('dt').filter({hasText:/^Execution$/}).locator('+ dd'))
            .toHaveText('failed');
          await historic.getByText('Frozen run definition',{exact:true}).click();
          await expect(historic.locator('pre')).toContainText('deliberately incorrect');
          const downloadPromise=page.waitForEvent('download');
          await historic.getByRole('button',{name:'Download screenshot',exact:true})
            .first().click();
          const download=await downloadPromise;
          await download.saveAs(process.env.UI_SCREENSHOT+'/failed-run-evidence.png');
          await snapshot('history-desktop');
          await page.setViewportSize({width:390,height:844});
          await snapshot('history-mobile');
          const overflow=await page.evaluate(()=>({
            width:document.documentElement.scrollWidth,viewport:innerWidth,
            elements:[...document.querySelectorAll('.lca-workspace *')].filter(element=>
              element.getBoundingClientRect().right>innerWidth).map(element=>({
                tag:element.tagName,className:element.className,
                text:element.textContent.slice(0,100),right:element.getBoundingClientRect().right
              })).slice(-10)
          }));
          if(overflow.width>overflow.viewport)
            throw new Error('history mobile horizontal overflow: '+JSON.stringify(overflow));
          if(errors.length) throw new Error(errors.join('\n'));
          console.log(JSON.stringify({flow_id:passed.flow_id,runs:[passed.id,failed.id,corrected.id],commands}));
        } catch(error) {
          await snapshot('failure');
          console.error('PAGE TEXT: '+await page.locator('body').innerText());
          throw error;
        } finally {
          input.close();await browser.close();
        }
        """
        environment = {
            key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ
        }
        process = await asyncio.create_subprocess_exec(
            shutil.which("node"),
            "--input-type=module",
            "-e",
            script,
            cwd=ROOT / "apps/web",
            env={**environment, "UI_ORIGIN": ui_origin, "UI_SCREENSHOT": str(tmp_path)},
            start_new_session=True,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def observe():
            async for line in process.stdout:
                message = line.decode().strip()
                logs.append(message)
                if message == "submit-requested":
                    assert await asyncio.to_thread(first_submit.wait, 10)
                    assert len(submissions) == 1
                    process.stdin.write(b"submit-received\n")
                    await process.stdin.drain()
                elif message == "recording-draining":
                    assert len(submissions) == 1
                    release_submit.set()
                elif message == "edited-without-replay":
                    assert len(submissions) == 1

        _, errors, _ = await asyncio.wait_for(
            asyncio.gather(observe(), process.stderr.read(), process.wait()), 240
        )
        assert process.returncode == 0, errors.decode() + "\n".join(logs)
        summary = json.loads(logs[-1])
        assert [command["action"]["type"] for command in summary["commands"]] == [
            "navigate",
            "fill",
            "select",
            "click",
        ]
        assert len(submissions) == 4
        assert submissions[0]["customer"] == "recorded-user"
        assert [item["customer"] for item in submissions[1:]] == ["replay-user"] * 3
        assert {item["plan"] for item in submissions} == {"premium"}
        assert len({item["context"] for item in submissions}) == 4
        async with httpx.AsyncClient(base_url=ui_origin) as client:
            base = "/api/projects/p/lca"
            flow = (await client.get(f"{base}/flows/{summary['flow_id']}")).json()
            assert flow["version"] == 4
            assert flow["definition"]["steps"][1]["arguments"] == {
                "value": {"parameter": "customer"}
            }
            recordings = (await client.get(f"{base}/flows/{flow['id']}/recordings")).json()
            assert len(recordings) == 1 and recordings[0]["status"] == "stopped"
            projection = recordings[0]["projection"]
            assert projection["ready"] and not projection["missing_sequences"]
            assert [step["operation"] for step in projection["definition"]["steps"]] == [
                "navigate",
                "fill",
                "select",
                "click",
            ]
            assert projection["definition"]["steps"][2]["arguments"] == {"value": "premium"}
            assert len(projection["excluded"]) == 2
            history = (await client.get(f"{base}/flows/{flow['id']}/runs")).json()
            assert {item["id"] for item in history} == set(summary["runs"])
            runs = [
                (await client.get(f"{base}/runs/{run_id}")).json() for run_id in summary["runs"]
            ]
            assert [run["execution"]["status"] for run in runs] == ["passed", "failed", "passed"]
            assert len({run["snapshot_id"] for run in runs}) == 3
            assert len({run["execution_id"] for run in runs}) == 3
            assert runs[1]["definition_snapshot"]["definition"]["steps"][-1]["arguments"] == {
                "text": "deliberately incorrect"
            }
        assert (tmp_path / "failed-run-evidence.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        print(json.dumps({"screenshots": str(tmp_path), "submissions": 4, **summary}))
    finally:
        release_submit.set()
        stop.set()
        try:
            await pumping
        finally:
            await worker.shutdown()
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*serving)
            for child in (process, vite):
                if child is not None and child.returncode is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(child.wait(), 3)
                    except TimeoutError:
                        os.killpg(child.pid, signal.SIGKILL)
                        await child.wait()
            engine.dispose()
