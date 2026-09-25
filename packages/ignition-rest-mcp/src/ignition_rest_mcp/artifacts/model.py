"""D17 artifact model, kinds, sensitivity/retention classes and store protocol."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class ArtifactState(str, Enum):
    STAGING = "STAGING"
    PUBLISHING = "PUBLISHING"
    READY = "READY"
    DELETING = "DELETING"
    LOST = "LOST"


ARTIFACT_KINDS = ("project_archive", "project_export", "tag_config_export")
SENSITIVITY_CLASSES = ("INTERNAL", "CONFIDENTIAL", "RESTRICTED")
RETENTION_CLASSES = ("EPHEMERAL", "EXPORT", "RECOVERY")
DOWNLOAD_PATH_PREFIX = "/artifacts/"

_KIND_MEDIA_TYPE = {
    "project_archive": "application/zip",
    "project_export": "application/zip",
    "tag_config_export": "application/json",
}

VISIBLE_STATES = (ArtifactState.READY,)
CONSUMED_QUOTA_STATES = (
    ArtifactState.STAGING, ArtifactState.PUBLISHING, ArtifactState.READY, ArtifactState.DELETING,
)


def media_type_for(kind: str) -> str:
    if kind not in _KIND_MEDIA_TYPE:
        raise ValueError(f"unknown artifact kind: {kind}")
    return _KIND_MEDIA_TYPE[kind]


@dataclass(frozen=True, slots=True)
class Artifact:
    artifact_id: str
    kind: str
    state: ArtifactState
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    sensitivity: str
    retention_class: str
    created_at: str
    expires_at: str | None
    owner: str
    gateway_id: str
    project_name: str
    correlation_id: str
    transaction_id: str
    retention_lock: bool

    def to_ref(self) -> dict[str, Any]:
        """The public ArtifactRef (contracts/shared/artifact-ref.schema.json).

        The owning principal is internal metadata and deliberately not part of it.
        """

        return {
            "artifactId": self.artifact_id,
            "mediaType": self.media_type,
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
            "kind": self.kind,
            "filename": self.filename,
            "sensitivity": self.sensitivity,
            "retentionClass": self.retention_class,
            "createdAt": self.created_at,
            "expiresAt": self.expires_at,
            "download": {"path": f"{DOWNLOAD_PATH_PREFIX}{self.artifact_id}"},
        }


class ArtifactValidator(Protocol):
    """Offline validation of a fully staged file before publication."""

    name: str

    async def validate(self, path: str) -> None:
        """Raise ``ValueError`` (or a subclass with a stable code) to refuse."""
        ...


class ArtifactWriter(Protocol):
    """Streaming staging sink handed to producers."""

    artifact_id: str
    bytes_written: int

    async def write(self, chunk: bytes) -> None: ...

    async def abort(self) -> None: ...


class ArtifactReader(Protocol):
    artifact: Artifact

    async def read_chunk(self) -> bytes | None: ...

    async def close(self) -> None: ...


class ArtifactStore(Protocol):
    """D17 storage abstraction; the MCP Tools depend on this, not on the filesystem."""

    async def create(
        self, *, kind: str, sensitivity: str, retention_class: str, owner: str,
        filename: str, media_type: str, correlation_id: str, gateway_id: str = "",
        project_name: str = "", transaction_id: str = "", declared_size: int | None = None,
        validator: ArtifactValidator | None = None,
    ) -> ArtifactWriter: ...

    async def publish(self, writer: ArtifactWriter) -> Artifact: ...

    async def open_read(self, artifact_id: str) -> ArtifactReader: ...

    async def stat(self, artifact_id: str) -> Artifact: ...

    async def exists(self, artifact_id: str) -> bool: ...

    async def list(
        self, *, principal: str, allow_admin: bool, kind: str | None, limit: int, offset: int,
    ) -> tuple[list[Artifact], int]: ...

    async def cleanup_expired(self) -> int: ...

    async def reconcile(self, *, batch: int, deadline_seconds: float) -> dict[str, int]: ...
