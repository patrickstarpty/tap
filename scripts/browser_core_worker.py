#!/usr/bin/env python3
"""Test supervisor composition for an exact owned synthetic site.

The normal BrowserWorker entrypoint deliberately has no private-target option.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import signal
import subprocess

from tap_platform.browser_automation.docker import (
    BrowserRuntime,
    SubprocessDockerClient,
)
from tap_platform.browser_automation.http import browser_service_from_environment
from tap_platform.browser_automation.objects import BrowserEvidenceSink
from tap_platform.browser_automation.worker import BrowserWorker


def verified_site_target(
    inspected: object, container_id: str, name: str, owner: str
) -> str:
    """Derive a fixed test target only from the exact inspected owned container."""
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise ValueError("expected one owned site container")
    site = inspected[0]
    if not isinstance(site, dict):
        raise ValueError("invalid site inspect response")
    if (
        len(container_id) != 64
        or any(character not in "0123456789abcdef" for character in container_id)
        or site.get("Id") != container_id
        or site.get("Name") != f"/{name}"
        or site.get("Config", {}).get("Labels", {}).get("tap.owner") != owner
    ):
        raise ValueError("site identity is not owned")
    address = (
        site.get("NetworkSettings", {})
        .get("Networks", {})
        .get("bridge", {})
        .get("IPAddress")
    )
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as error:
        raise ValueError("site address is invalid") from error
    if (
        not isinstance(ip, ipaddress.IPv4Address)
        or not ip.is_private
        or ip.is_loopback
        or ip.is_link_local
    ):
        raise ValueError("site address is not an owned bridge address")
    return f"http://{ip}:8080"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-container-id")
    parser.add_argument("--site-name")
    parser.add_argument("--owner")
    args = parser.parse_args()
    selected = (args.site_container_id, args.site_name, args.owner)
    if any(selected) and not all(selected):
        parser.error("owned site identity requires all three fields")
    target = None
    if all(selected):
        result = subprocess.run(
            ["docker", "inspect", args.site_container_id],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        target = verified_site_target(
            json.loads(result.stdout),
            args.site_container_id,
            args.site_name,
            args.owner,
        )
    service, principal, _ = browser_service_from_environment()
    if service is None or principal is None:
        raise RuntimeError("TAP browser configuration required")
    image = os.environ["TAP_BROWSER_IMAGE"]
    runtime = BrowserRuntime(
        SubprocessDockerClient(),
        image,
        evidence_sink=BrowserEvidenceSink(service.store, service.objects),
        project_id=principal.project_id,
        test_target=target,
    )
    worker = BrowserWorker(service, runtime, principal.project_id)

    async def serve() -> None:
        loop = asyncio.get_running_loop()
        running = asyncio.create_task(
            worker.run(float(os.getenv("TAP_BROWSER_WORKER_POLL_SECONDS", "0.2")))
        )
        loop.add_signal_handler(signal.SIGTERM, running.cancel)
        try:
            await running
        except asyncio.CancelledError:
            pass  # The owned supervisor requested the worker's normal shutdown.
        finally:
            loop.remove_signal_handler(signal.SIGTERM)

    asyncio.run(serve())


if __name__ == "__main__":
    main()
