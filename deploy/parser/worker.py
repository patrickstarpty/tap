"""Network-free one-shot PID1 deadline, bounded child pipes and process reaping."""

from __future__ import annotations

import asyncio
import os
import signal
import sys

sys.path[:0] = ["/opt/parser/site", "/opt/parser/src"]

from tap.modules.knowledge.adapters.parser_protocol import (  # noqa: E402
    MAX_REPLY,
    MAX_STDERR,
    encode_request,
    encode_result,
    read_request,
)


def child_limits() -> None:
    """Only invoked by the controlled single-threaded container PID1 at spawn."""
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (8, 10))
    resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def child() -> None:
    from tap.modules.knowledge.adapters.artifact_codecs import (
        encode_normalized_artifact,
    )
    from tap.modules.knowledge.adapters.document_parsers import ParserRegistry
    from tap.modules.knowledge.domain.documents import DocumentParseRejected

    async def parse() -> bytes:
        reader = asyncio.StreamReader()
        reader.feed_data(sys.stdin.buffer.read(25 * 1024 * 1024 + 4101))
        reader.feed_eof()
        request_id, source = await read_request(reader)
        if await reader.read(1):
            raise ValueError
        try:
            result = ParserRegistry().parse(source)
            payload = encode_normalized_artifact(str(source.revision_id), result)
            if len(payload) > MAX_REPLY:
                raise DocumentParseRejected("document-too-complex")
            return encode_result(request_id, payload=payload)
        except DocumentParseRejected as error:
            return encode_result(request_id, error=error.code)
        except MemoryError:
            return encode_result(request_id, error="document-too-complex")
        except Exception:
            return encode_result(request_id, error="invalid-document")

    try:
        sys.stdout.buffer.write(asyncio.run(parse()))
        sys.stdout.buffer.flush()
    except BaseException:
        os._exit(70)


async def supervise() -> None:
    reader = asyncio.StreamReader()
    await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
    )
    request_id, source = await read_request(reader)
    # Docker OpenStdin may remain open after the attached CLI half-closes.
    # The trusted broker sends exactly one validated length-framed request;
    # completion of that frame, not EOF, starts parsing.
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        "/opt/parser/child.py",
        "--child",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        preexec_fn=child_limits,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
    )

    async def bounded(stream: asyncio.StreamReader | None, cap: int) -> bytes:
        assert stream is not None
        data = bytearray()
        while chunk := await stream.read(65536):
            if len(data) + len(chunk) > cap:
                raise ValueError
            data.extend(chunk)
        return bytes(data)

    async def write() -> None:
        assert process.stdin is not None
        process.stdin.write(encode_request(source, request_id))
        await process.stdin.drain()
        process.stdin.close()
        await process.stdin.wait_closed()

    tasks = [
        asyncio.create_task(write()),
        asyncio.create_task(bounded(process.stdout, MAX_REPLY + 4100)),
        asyncio.create_task(bounded(process.stderr, MAX_STDERR)),
    ]
    try:
        async with asyncio.timeout(25):
            results = await asyncio.gather(*tasks)
            code = await process.wait()
        if code != 0:
            result = encode_result(request_id, error="parser-unavailable")
        else:
            result = results[1]
            assert isinstance(result, bytes)
    except (TimeoutError, ValueError, BrokenPipeError, ConnectionError):
        result = encode_result(request_id, error="document-too-complex")
    finally:
        # Whole namespace exits after this direct group is killed and reaped;
        # daemon-owned container wait additionally proves no descendant survives.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    sys.stdout.buffer.write(result)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    if sys.argv[1:] == ["--child"]:
        child()
    else:
        signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
        signal.signal(signal.SIGTERM, lambda *_: os._exit(143))
        signal.alarm(35)
        try:
            asyncio.run(supervise())
        except BaseException:
            os._exit(70)
