"""Test-image-only child. Never copied into the production parser image."""

import asyncio
import errno
import json
import os
import resource
import socket
import sys
import time
from pathlib import Path

sys.path[:0] = ["/opt/parser/site", "/opt/parser/src"]
from tap.modules.knowledge.adapters.parser_protocol import encode_result, read_request  # noqa: E402


async def main():
    reader = asyncio.StreamReader()
    reader.feed_data(sys.stdin.buffer.read(25 * 1024 * 1024 + 4101))
    reader.feed_eof()
    request, source = await read_request(reader)
    case = source.content.decode("ascii")
    if case == "constraints":
        assert resource.getrlimit(resource.RLIMIT_AS) == (384 * 1024 * 1024,) * 2, "AS"
        assert resource.getrlimit(resource.RLIMIT_CPU) == (8, 10), "CPU"
        assert resource.getrlimit(resource.RLIMIT_FSIZE) == (32 * 1024 * 1024,) * 2, (
            "constraint-line-24"
        )
        assert os.getuid() == 65532, "constraint-line-25"
        interfaces = {name for _, name in socket.if_nameindex()}
        assert "lo" in interfaces and interfaces <= {"lo", "tunl0", "ip6tnl0"}, "interfaces"
        for name in interfaces - {"lo"}:
            assert int(Path("/sys/class/net", name, "flags").read_text().strip(), 16) & 1 == 0, (
                "active non-loopback interface"
            )
        assert len(Path("/proc/net/route").read_text().splitlines()) == 1, "IPv4 route"
        assert not os.path.exists("/var/run/docker.sock"), "constraint-line-27"
        assert not any(
            (key.endswith("_KEY") and key != "GPG_KEY") or "SECRET" in key or "TOKEN" in key
            for key in os.environ
        ), "constraint-line-28"
        for address in [("127.0.0.1", 80), ("192.0.2.1", 443)]:
            connection = socket.socket()
            connection.settimeout(0.2)
            assert connection.connect_ex(address) != 0, "constraint-line-31"
            connection.close()
        for route in Path("/proc/net/ipv6_route").read_text().splitlines():
            fields = route.split()
            assert fields[-1] == "lo" and fields[0] in {"0" * 32, "0" * 31 + "1"}, (
                "external IPv6 route"
            )
        connection = socket.socket(socket.AF_INET6)
        connection.settimeout(0.2)
        assert connection.connect_ex(("2001:db8::1", 443)) != 0, "IPv6 connection"
        connection.close()
        try:
            open("/opt/parser/forbidden", "wb")
            raise AssertionError("writable root")
        except OSError as error:
            assert error.errno in (errno.EROFS, errno.EACCES), "constraint-line-37"
    elif case == "memory":
        try:
            bytearray(400 * 1024 * 1024)
            raise AssertionError("address-space budget missing")
        except MemoryError:
            pass
    elif case == "cpu":
        while True:
            pass
    elif case in ("timeout", "cancel", "supervisor-crash"):
        if os.fork() == 0:
            os.setsid()
        time.sleep(60)
    elif case == "oom":
        children = []
        for _ in range(6):
            pid = os.fork()
            if pid == 0:
                allocation = bytearray(120 * 1024 * 1024)
                assert len(allocation) == 120 * 1024 * 1024
                time.sleep(60)
                os._exit(0)
            children.append(pid)
        for pid in children:
            os.waitpid(pid, 0)
    elif case == "crash":
        os._exit(73)
    elif case == "output":
        sys.stdout.buffer.write(b"x" * (32 * 1024 * 1024 + 4101))
        sys.stdout.buffer.flush()
    elif case == "stderr":
        sys.stderr.buffer.write(b"x" * 16385)
        sys.stderr.buffer.flush()
    else:
        raise AssertionError("unknown fixed test case")
    sys.stdout.buffer.write(
        encode_result(request, payload=json.dumps({"probe": case, "passed": True}).encode())
    )
    sys.stdout.buffer.flush()


try:
    asyncio.run(main())
except AssertionError as error:
    sys.stdout.buffer.write(
        encode_result("c" * 32, payload=json.dumps({"failed": str(error)}).encode())
    )
    sys.stdout.buffer.flush()
