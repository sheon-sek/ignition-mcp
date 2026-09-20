# D17 — Binary Artifact / Export Handling

**Status:** DECIDED

## Core architecture
Large/file-like binary payloads do not travel through MCP Tool JSON or the model context.

Use:

```text
MCP control plane
→ ArtifactRef / metadata

Authenticated-or-trusted HTTP artifact data plane
→ binary upload/download
```

MCP binary Resources are not the default large-file channel because byte resources still require binary-to-MCP encoding and can create unnecessary memory/context overhead.

## Artifact-returning operations
Examples include:
- `project_export`;
- Project recovery snapshots;
- Gateway backup;
- future module/report/export bundles;
- other ZIP/archive-like outputs.

These return an `ArtifactRef`, not inline Base64.

Typical Artifact metadata:
- artifact ID;
- kind;
- display filename;
- MIME type;
- exact byte size;
- exact-byte SHA-256;
- created/expiry times;
- sensitivity;
- optional download location/token metadata.

## ArtifactStore abstraction
Business Tools use a storage abstraction rather than direct filesystem assumptions:

```text
ArtifactStore
├── create
├── open_read
├── stat
├── delete
├── exists
└── cleanup_expired
```

v1:
```text
LocalArtifactStore
```

with a configured persistent data directory/volume.

Future implementations may use shared object storage without changing public Tool contracts.

## HTTP transport is mandatory
The External FastMCP server and artifact data plane must support plain HTTP as a first-class deployment mode.

HTTPS/TLS is optional and deployment-dependent.

Internal/trusted deployments must not be forced to add TLS/OAuth infrastructure merely to use artifacts.

D07-A in D18 defines the corresponding security-profile amendment.

## Companion artifact HTTP API
The External service may expose a narrow data plane such as:

```text
POST   /artifacts
GET    /artifacts/{artifactId}
HEAD   /artifacts/{artifactId}
DELETE /artifacts/{artifactId}
```

This is binary transport, not a second business API.

Business operations remain MCP Tools.

## Streaming
Download from Ignition to ArtifactStore using bounded chunks.

Upload from ArtifactStore to Ignition using streaming request bodies where supported.

Do not:
- read entire large archive into memory;
- convert whole files to Base64;
- keep duplicate in-memory copies.

Compute size and SHA-256 incrementally during streaming.

## Atomic artifact creation
Artifact creation uses:
```text
temporary/staging object
→ stream/write
→ validate size/checksum/type
→ flush/commit
→ atomic publish
```

Only committed/ready artifacts are visible to consumers.

Partial writes are cleaned after failure/cancellation.

## Integrity
D17 exact-byte `artifact.sha256` is distinct from D16 logical Project-content fingerprint.

Both may exist for one Project archive:
- exact-byte hash → storage/transport integrity;
- logical Project fingerprint → concurrency comparison.

## Artifact ingress
v1 does not accept arbitrary remote URLs for import.

Do not provide generic `artifact_from_url` / `project_import(url=...)`.

Binary ingress is through:
- authenticated/trusted artifact upload;
- exports produced by the server from its configured Ignition Gateway.

This avoids SSRF, redirect, DNS-rebinding, and unbounded remote-download risks.

## Security / access
Artifact endpoints follow the D07/D07-A deployment security mode.

In secured mode, artifact access is authenticated/authorized.

In trusted-internal mode, unauthenticated HTTP may be allowed by explicit deployment configuration.

Do not rely on an unguessable Artifact ID as the only security control when secured mode is enabled.

If signed download tokens/URLs are used:
- they are short-lived;
- their lifetime is independent of Artifact TTL;
- tokens/full signed URLs are never logged.

## Sensitivity classes
At minimum:
```text
INTERNAL
CONFIDENTIAL
RESTRICTED
```

Examples:
- Project export/recovery ZIP → `CONFIDENTIAL`;
- Gateway backup → `RESTRICTED`.

Sensitive reads/exports can require stronger authorization than ordinary read-only Tools even if the underlying Native REST operation uses HTTP GET.

## Retention classes
v1:
```text
EPHEMERAL
→ transaction/request lifetime

EXPORT
→ default 24 hours

RECOVERY
→ default 7 days after transaction reaches a safe terminal state
```

TTL values are deployment-configurable.

The MCP server is not a long-term enterprise backup product.

## Quotas
Artifact storage must be finite and bounded.

Support deployment limits such as:
- max artifact size;
- max total bytes;
- max artifact count;
- minimum free disk threshold/ratio.

Enforce size both before transfer when information is available and during streaming.

`unlimited` is not a valid production posture.

## Recovery retention lock
D16 recovery artifacts cannot be deleted by normal expiry cleanup while their transaction is unresolved or marked recovery-required.

## Persistent metadata
Persist artifact metadata separately from the raw object.

Metadata includes at least:
- ID;
- kind/state;
- filename/MIME;
- size/hash;
- sensitivity;
- timestamps;
- Gateway/Project where relevant;
- correlation/transaction IDs;
- retention class.

Do not store secrets.

## Filenames and paths
Storage keys use generated opaque Artifact IDs.

User/Gateway filenames are display metadata only and are sanitized for output headers.

Never derive storage paths directly from user filenames/project names.

## ZIP validation
Uploaded Project archives continue to obey D15 ZIP safety checks:
- traversal;
- absolute paths;
- symlinks;
- duplicate entries;
- entry count;
- expanded size;
- compression bomb controls.

Do not attempt to reverse-engineer undocumented Gateway Backup internals merely for upload validation.

## Public MCP artifact management
v1 may expose:
- `artifact_list`;
- `artifact_info`;
- `artifact_delete`.

These operate on metadata/lifecycle, not binary body transport.

Do not expose:
- `artifact_download` returning Base64/bytes;
- `artifact_upload` accepting Base64.

## Project Tool impact
```text
project_export
→ ArtifactRef

project_import
← artifact_id
```

Artifact type compatibility is validated server-side before an artifact is consumed by an operation.

Deletion is idempotent, but retention-locked recovery artifacts cannot be removed through ordinary deletion.

```yaml
decision: D17
status: DECIDED
binary_in_tool_json: false
large_binary_mcp_resource: false
artifact_transport: http
plain_http_supported: true
tls_required: false
artifact_store_abstraction: true
v1_store: local_persistent
streaming_required: true
arbitrary_url_import: false
default_export_ttl_hours: 24
default_recovery_ttl_days: 7
```
