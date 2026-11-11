"""Browser artifact reads exposed to workflow application services."""

from typing import Any, Protocol


class BrowserReadError(RuntimeError):
    """The browser artifact source could not complete a read."""


class BrowserArtifactReader(Protocol):
    @property
    def project_id(self) -> str: ...

    async def request(
        self, method: str, path: str, body: object | None = None, key: str | None = None
    ) -> dict[str, Any]: ...

    async def read_evidence(self, evidence_id: str) -> tuple[bytes, str]: ...
