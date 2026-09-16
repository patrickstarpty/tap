"""Trusted host supervisor: fixed local Docker jobs, private byte-only UDS."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from tap.modules.knowledge.adapters.parser_protocol import (
    MAX_REPLY,
    MAX_STDERR,
    ParserProtocolError,
    decode_request,
    encode_request,
    encode_result,
    read_control,
    read_header,
    read_result,
    request_length,
)
from tap.modules.knowledge.domain.documents import (
    PARSER_VERSION,
    DocumentId,
    DocumentSource,
    MediaType,
    canonical_sha256,
    revision_id_for,
)


def socket_path(state: Path) -> Path:
    canonical = state.resolve()
    if len(os.fsencode(canonical / "parser.sock")) < 100:
        return canonical / "parser.sock"
    digest = hashlib.sha256(f"{os.getuid()}:{canonical}".encode()).hexdigest()[:24]
    return Path("/tmp") / f"tap-parser-{os.getuid()}-{digest}" / "parser.sock"


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("invalid parser private directory")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("invalid parser private directory ownership")


@contextmanager
def state_lock(state: Path):
    private_directory(state)
    for name in (
        "lock",
        "owner",
        "association.json",
        "cleanup.json",
        "ready-pid",
        "unresolved.json",
        "creation-unresolved",
        "last-terminal.json",
    ):
        path = state / name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("invalid parser state file")
    descriptor = os.open(state / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def write_association(state: Path, association: dict[str, str]) -> None:
    """Commit private recovery metadata atomically while the caller owns state_lock."""
    private_directory(state)
    record = state / "association.json"
    if record.is_symlink() or (record.exists() and not record.is_file()):
        raise ValueError("invalid parser association file")
    if record.exists() and json.loads(record.read_text()) == association:
        return
    descriptor, name = tempfile.mkstemp(prefix=".association-", dir=state)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            info = os.fstat(output.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise ValueError("invalid parser association temporary file")
            json.dump(association, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, record)
    finally:
        temporary.unlink(missing_ok=True)


class Supervisor:
    def __init__(
        self, root: Path, state: Path, project: str, image: str, docker: list[str]
    ) -> None:
        self.root, self.state, self.project, self.image, self.docker = (
            root,
            state,
            project,
            image,
            docker,
        )
        owner_file = state / "owner"
        if owner_file.exists():
            self.owner = owner_file.read_text().strip()
        else:
            self.owner = uuid4().hex
            owner_file.write_text(self.owner)
        if not re.fullmatch("[0-9a-f]{32}", self.owner):
            raise ValueError("invalid parser ownership")
        association = {"owner": self.owner, "project": project, "imageId": image}
        record = state / "association.json"
        if record.exists() and json.loads(record.read_text()) != association:
            raise ValueError("parser deployment association mismatch")
        write_association(state, association)
        self.busy = False
        self.unresolved = (state / "unresolved.json").exists()
        self.jobs: set[asyncio.Task[bytes]] = set()
        self.environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(state),
            "TAPPER_PARSER_IMAGE": image,
            "TAPPER_PARSER_OWNER": self.owner,
        }
        self.compose = [
            *docker,
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(root / "compose.yaml"),
            "-p",
            project,
            "--profile",
            "tapper-parser",
        ]

    async def command(self, args: list[str], timeout: float = 5) -> str:
        child = await asyncio.create_subprocess_exec(
            *args,
            env=self.environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

        async def bounded(stream: asyncio.StreamReader | None) -> bytes:
            assert stream is not None
            value = bytearray()
            while chunk := await stream.read(8192):
                if len(value) + len(chunk) > 65536:
                    raise ValueError("parser Docker output exceeded limit")
                value.extend(chunk)
            return bytes(value)

        drains = [
            asyncio.create_task(bounded(child.stdout)),
            asyncio.create_task(bounded(child.stderr)),
        ]
        try:
            async with asyncio.timeout(timeout):
                stdout, _ = await asyncio.gather(*drains)
                await child.wait()
                if child.returncode:
                    raise ValueError("parser Docker operation failed")
                return stdout.decode().strip()
        finally:
            await self.reap_cli(child, drains)

    @staticmethod
    async def reap_cli(child: asyncio.subprocess.Process, drains: list[asyncio.Task]) -> None:
        # Stop every owned local writer before draining the finite residual pipe
        # buffers. wait() alone can deadlock after kill when a StreamReader has
        # paused its transport due to backpressure.
        if child.returncode is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for task in drains:
            task.cancel()
        await asyncio.gather(*drains, return_exceptions=True)
        await child.communicate()
        await child.wait()

    async def owned(self) -> list[str]:
        value = await self.command(
            [
                *self.docker,
                "ps",
                "-aq",
                "--no-trunc",
                "--filter",
                "label=io.tap.parser.owner=" + self.owner,
                "--filter",
                "label=com.docker.compose.project=" + self.project,
            ]
        )
        ids = value.splitlines() if value else []
        if len(ids) > 2 or any(not re.fullmatch("[0-9a-f]{64}", cid) for cid in ids):
            raise ValueError("parser ownership mismatch")
        return ids

    async def inspect(self, cid: str) -> dict:
        info = json.loads(await self.command([*self.docker, "inspect", cid]))[0]
        labels = info["Config"].get("Labels", {})
        host = info["HostConfig"]
        if (
            info["Id"] != cid
            or info["Image"] != self.image
            or labels.get("io.tap.parser.owner") != self.owner
            or labels.get("com.docker.compose.project") != self.project
            or labels.get("com.docker.compose.service") != "tap-parser"
            or host["NetworkMode"] != "none"
            or not host["ReadonlyRootfs"]
            or info["Config"]["User"] != "65532:65532"
            or host.get("Binds")
            or info["Config"].get("Entrypoint") != ["python", "-I", "-B", "/opt/parser/worker.py"]
            or info["Config"].get("Cmd")
            or info["Config"].get("Tty")
            or not all(
                info["Config"].get(key)
                for key in ("OpenStdin", "AttachStdin", "AttachStdout", "AttachStderr")
            )
            or any(
                value.split("=", 1)[0]
                not in {"PATH", "LANG", "GPG_KEY", "PYTHON_VERSION", "PYTHON_SHA256"}
                for value in info["Config"].get("Env", [])
            )
            or any(m["Type"] != "tmpfs" for m in info.get("Mounts", []))
            or host.get("Memory") != 512 * 1024 * 1024
            or host.get("MemorySwap") != 512 * 1024 * 1024
            or host.get("PidsLimit") != 16
            or host.get("NanoCpus") != 1_000_000_000
            or host.get("LogConfig", {}).get("Type") != "none"
            or host.get("CapDrop") != ["ALL"]
            or host.get("CapAdd")
            or host.get("SecurityOpt") != ["no-new-privileges:true"]
            or host.get("Privileged")
            or host.get("PidMode")
            or host.get("IpcMode") == "host"
            or host.get("PortBindings")
            or host.get("Devices")
            or host.get("Tmpfs") != {"/tmp": "size=32m,noexec,nosuid,nodev,mode=1777"}
            or sorted(host.get("Ulimits", []), key=lambda item: item["Name"])
            != [{"Name": "core", "Soft": 0, "Hard": 0}, {"Name": "nofile", "Soft": 64, "Hard": 64}]
        ):
            raise ValueError("parser isolation mismatch")
        return info

    async def cleanup(self) -> bool:
        (self.state / "cleanup.json").unlink(missing_ok=True)
        try:
            async with asyncio.timeout(15):
                for cid in await self.owned():
                    info = await self.inspect(cid)
                    if info["State"]["Running"]:
                        await self.command([*self.docker, "kill", "--signal=TERM", cid])
                        try:
                            await self.command([*self.docker, "wait", cid], timeout=1)
                        except (ValueError, TimeoutError):
                            await self.command([*self.docker, "kill", cid])
                    await self.command([*self.docker, "wait", cid])
                    info = await self.inspect(cid)
                    if info["State"]["Running"]:
                        raise ValueError("parser did not terminate")
                    (self.state / "last-terminal.json").write_text(
                        json.dumps(
                            {
                                "containerId": cid,
                                "exitCode": info["State"]["ExitCode"],
                                "oomKilled": info["State"]["OOMKilled"],
                                "running": False,
                                "imageId": self.image,
                                "owner": self.owner,
                            }
                        )
                    )
                    await self.command([*self.docker, "rm", "-v", cid])
                if (self.state / "creation-unresolved").exists():
                    raise ValueError("parser creation remains unresolved")
                if await self.owned():
                    raise ValueError("parser owned containers remain")
                (self.state / "cleanup.json").write_text(
                    json.dumps(
                        {
                            "owner": self.owner,
                            "project": self.project,
                            "imageId": self.image,
                            "containers": [],
                            "localChildrenReaped": True,
                        }
                    )
                )
                self.unresolved = False
                (self.state / "unresolved.json").unlink(missing_ok=True)
                return True
        except Exception:
            self.unresolved = True
            (self.state / "unresolved.json").write_text(
                json.dumps({"owner": self.owner, "unresolved": True})
            )
            return False

    async def run_job(self, request_id: str, source: DocumentSource) -> bytes:
        response = encode_result(request_id, error="parser-unavailable")
        if self.unresolved:
            return response
        (self.state / "cleanup.json").unlink(missing_ok=True)
        process: asyncio.subprocess.Process | None = None
        drains: list[asyncio.Task] = []
        try:
            configuration = json.loads(
                await self.command([*self.compose, "config", "--format", "json", "tap-parser"])
            )
            if set(configuration.get("services", {})) != {"tap-parser"}:
                raise ValueError("parser requires one closed service")
            template = configuration["services"]["tap-parser"]
            expected = {
                "image": self.image,
                "network_mode": "none",
                "user": "65532:65532",
                "read_only": True,
                "cpus": 1,
                "mem_limit": "536870912",
                "memswap_limit": "536870912",
                "pids_limit": 16,
                "stdin_open": True,
                "profiles": ["tapper-parser"],
                "pull_policy": "never",
                "restart": "no",
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "tmpfs": ["/tmp:size=32m,noexec,nosuid,nodev,mode=1777"],
                "ulimits": {"core": {}, "nofile": {"hard": 64, "soft": 64}},
                "logging": {"driver": "none"},
                "labels": {"io.tap.parser.owner": self.owner},
                "command": None,
                "entrypoint": None,
            }
            # Installed Compose omits false tty and zero-valued core limits;
            # both accepted renderings materialize the same explicit safe argv.
            if template.get("tty") is False:
                template.pop("tty")
            if template.get("ulimits", {}).get("core") == {"hard": 0, "soft": 0}:
                template["ulimits"]["core"] = {}
            if template != expected:
                raise ValueError("parser Compose template mismatch")
            # Compose create sets AttachStdout=false. Materialize its validated
            # fixed job with explicit stream attachments before starting it.
            create = [
                *self.docker,
                "create",
                "-i",
                "-a",
                "stdin",
                "-a",
                "stdout",
                "-a",
                "stderr",
                "--name",
                self.project + "-parser-job",
                "--network",
                "none",
                "--read-only",
                "--user",
                "65532:65532",
                "--cpus",
                "1",
                "--memory",
                "512m",
                "--memory-swap",
                "512m",
                "--pids-limit",
                "16",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--tmpfs",
                "/tmp:size=32m,noexec,nosuid,nodev,mode=1777",
                "--ulimit",
                "nofile=64:64",
                "--ulimit",
                "core=0:0",
                "--log-driver",
                "none",
                "--label",
                "io.tap.parser.owner=" + self.owner,
                "--label",
                "com.docker.compose.project=" + self.project,
                "--label",
                "com.docker.compose.service=tap-parser",
                self.image,
            ]
            creating = asyncio.create_task(self.command(create, timeout=15))
            cancelled = False
            while not creating.done():
                try:
                    await asyncio.shield(creating)
                except asyncio.CancelledError:
                    cancelled = True
            try:
                creating.result()
            except TimeoutError:
                (self.state / "creation-unresolved").write_text(self.owner)
                raise
            if cancelled:
                raise asyncio.CancelledError
            ids = await self.owned()
            if len(ids) != 1:
                raise ValueError("parser container missing")
            cid = ids[0]
            await self.inspect(cid)
            process = await asyncio.create_subprocess_exec(
                *self.docker,
                "start",
                "-ai",
                cid,
                env=self.environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )

            async def bounded(stream: asyncio.StreamReader | None, maximum: int) -> bytes:
                assert stream is not None
                data = bytearray()
                while chunk := await stream.read(65536):
                    if len(data) + len(chunk) > maximum:
                        raise ParserProtocolError()
                    data.extend(chunk)
                return bytes(data)

            async def write() -> bytes:
                assert process is not None and process.stdin is not None
                process.stdin.write(encode_request(source, request_id))
                await process.stdin.drain()
                process.stdin.close()
                await process.stdin.wait_closed()
                return b""

            drains = [
                asyncio.create_task(write()),
                asyncio.create_task(bounded(process.stdout, MAX_REPLY + 4100)),
                asyncio.create_task(bounded(process.stderr, MAX_STDERR)),
            ]
            async with asyncio.timeout(38):
                _, output, _ = await asyncio.gather(*drains)
                await process.wait()
                # CLI exit is not proof: observe container terminal state separately.
                await self.command([*self.docker, "wait", cid])
                info = await self.inspect(cid)
                if info["State"]["Running"] or info["State"]["ExitCode"] != 0:
                    raise ParserProtocolError()
                reader = asyncio.StreamReader()
                reader.feed_data(output)
                reader.feed_eof()
                await read_result(reader, request_id)
                if await reader.read(1):
                    raise ParserProtocolError()
                response = output
        except asyncio.CancelledError:
            response = encode_result(request_id, error="cancelled")
        except Exception:
            pass
        finally:
            # Shield cleanup from repeated peer cancellation. Never emit a terminal
            # receipt until the exact remote container was removed (or unresolved).
            async def settle() -> bool:
                if process is not None:
                    await self.reap_cli(process, drains)
                return await self.cleanup()

            cleanup = asyncio.create_task(settle())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            if not cleanup.result():
                response = encode_result(request_id, error="parser-unavailable")
        return response

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        job: asyncio.Task[bytes] | None = None
        control: asyncio.Task | None = None
        acquired = False
        request_id = "0" * 32
        try:
            async with asyncio.timeout(30):
                header = await read_header(reader)
                if header == {"version": 1, "health": True}:
                    writer.write(
                        encode_result(
                            request_id, error="parser-unavailable" if self.unresolved else None
                        )
                    )
                    await writer.drain()
                    return
                request_id = header.get("requestId", request_id)
                if self.busy or self.unresolved:
                    writer.write(encode_result(request_id, error="parser-unavailable"))
                    await writer.drain()
                    return
                self.busy = acquired = True
                content = await reader.readexactly(request_length(header))
                request_id, source = decode_request(header, content)
            job = asyncio.create_task(self.run_job(request_id, source))
            self.jobs.add(job)
            control = asyncio.create_task(read_control(reader))
            done, _ = await asyncio.wait((job, control), return_when=asyncio.FIRST_COMPLETED)
            if control in done:
                try:
                    value = control.result()
                    # Only explicit cancellation is cancellation. Complete request
                    # followed by normal EOF/half-close must still finish normally.
                    if value is None:
                        response = await job
                    elif value != {"version": 1, "cancel": request_id}:
                        raise ParserProtocolError()
                    else:
                        job.cancel()
                except ParserProtocolError:
                    job.cancel()
            response = await job
            writer.write(response)
            await writer.drain()
        except (Exception, asyncio.CancelledError):
            if job is not None:
                job.cancel()
                await asyncio.shield(job)
        finally:
            if acquired:
                self.busy = False
            if job is not None:
                self.jobs.discard(job)
            if control is not None:
                control.cancel()
                await asyncio.gather(control, return_exceptions=True)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--socket-path", action="store_true")
    parser.add_argument("--cleanup-only", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch("[a-z0-9][a-z0-9_-]{2,62}", args.project):
        raise ValueError("invalid parser project")
    state = args.state_dir.absolute()
    if args.socket_path:
        print(socket_path(state))
        return
    with state_lock(state):
        await serve(state, args.project, args.cleanup_only)


async def serve(state: Path, project: str, cleanup_only: bool) -> None:
    (state / "ready-pid").unlink(missing_ok=True)
    socket = socket_path(state)
    private_directory(socket.parent)
    if socket.is_symlink():
        raise ValueError("invalid parser socket")
    socket.unlink(missing_ok=True)
    root = Path(__file__).resolve().parents[5]

    async def verify_image() -> str:
        verify = await asyncio.create_subprocess_exec(
            "bash",
            str(root / "scripts/build-tapper-parser.sh"),
            "verify",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        output, _ = await verify.communicate()
        image = output.decode().strip()
        if verify.returncode or not re.fullmatch("sha256:[0-9a-f]{64}", image):
            raise ValueError("parser image unavailable")
        return image

    async def docker_context(*args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        output, _ = await process.communicate()
        if process.returncode:
            raise ValueError("parser local Docker unavailable")
        return output.decode().strip()

    context = await docker_context("context", "show")
    endpoint = await docker_context(
        "context", "inspect", context, "--format", '{{(index .Endpoints "docker").Host}}'
    )
    if not endpoint.startswith("unix://") or os.getenv("DOCKER_HOST"):
        raise ValueError("parser requires local Docker")
    compose_plugin = await docker_context(
        "info",
        "--format",
        '{{range .ClientInfo.Plugins}}{{if eq .Name "compose"}}{{.Path}}{{end}}{{end}}',
    )
    if not Path(compose_plugin).is_file():
        raise ValueError("parser Compose plugin unavailable")
    with tempfile.TemporaryDirectory(prefix="tap-parser-docker-") as config:
        (Path(config) / "config.json").write_text(
            json.dumps({"auths": {}, "cliPluginsExtraDirs": [str(Path(compose_plugin).parent)]})
        )
        supervisor = Supervisor(
            root,
            state,
            project,
            json.loads((state / "association.json").read_text())["imageId"]
            if (state / "association.json").exists()
            else await verify_image(),
            ["docker", "--config", config, "--host", endpoint],
        )
        if not await supervisor.cleanup():
            raise ValueError("parser cleanup unresolved")
        if cleanup_only:
            if socket.parent != state.resolve():
                socket.parent.rmdir()
            return
        # Reconcile the previously verified image under its exact old owner first.
        # A new receipt is bound only after that independent cleanup succeeded.
        image = await verify_image()
        write_association(
            state,
            {
                "owner": supervisor.owner,
                "project": project,
                "imageId": image,
            },
        )
        supervisor.image = image
        supervisor.environment["TAPPER_PARSER_IMAGE"] = image
        data = b"Parser startup readiness retained fact."
        document = DocumentId("doc_" + "0" * 32)
        source = DocumentSource(
            "ready.txt",
            MediaType.TEXT,
            data,
            document,
            revision_id_for(document, canonical_sha256(data), PARSER_VERSION),
        )
        result = await supervisor.run_job("0" * 32, source)
        reader = asyncio.StreamReader()
        reader.feed_data(result)
        reader.feed_eof()
        error, _ = await read_result(reader, "0" * 32)
        if error:
            raise ValueError("parser startup execution failed")
        server = await asyncio.start_unix_server(supervisor.handle, path=socket, limit=65536)
        socket.chmod(0o600)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        (state / "ready-pid").write_text(str(os.getpid()))
        try:
            async with server:
                await stop.wait()
        finally:
            server.close()
            await server.wait_closed()
            for job in supervisor.jobs:
                job.cancel()
            await asyncio.gather(*supervisor.jobs, return_exceptions=True)
            (state / "ready-pid").unlink(missing_ok=True)
            socket.unlink(missing_ok=True)
            if not await supervisor.cleanup():
                raise ValueError("parser shutdown cleanup unresolved")
            if socket.parent != state.resolve():
                socket.parent.rmdir()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        raise SystemExit(
            "Parser supervisor unavailable; check the owned build and lifecycle."
        ) from None
