"""Pure ASGI exact-Origin gate, independent of caller-controlled proxy headers."""

from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from tap.interfaces.http.problems import problem_response


def validate_origins(origins: frozenset[str]) -> None:
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.port is None
            or origin != f"http://{parsed.netloc}"
        ):
            raise ValueError("Origin must be an explicit loopback HTTP origin with a port")


class OriginPolicyMiddleware:
    def __init__(self, app: ASGIApp, *, allowed_origins: frozenset[str]) -> None:
        self.app = app
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            origins = [
                value.decode("latin-1")
                for key, value in scope["headers"]
                if key.lower() == b"origin"
            ]
            if len(origins) != 1 or origins[0] not in self.allowed_origins:
                response = problem_response("authorization-denied", Request(scope))
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
