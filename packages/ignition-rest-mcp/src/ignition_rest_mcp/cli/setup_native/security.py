"""D20's opt-in Security Level and Runtime API token provisioning (ticket #22).

Two writes only ever happen when the operator asks for them, and neither may touch
something that is already there:

* ``--provision-security-levels`` creates the **dedicated Runtime Security Level**
  D09 asks for — one level per privilege profile, a child of the Gateway's
  ``Authenticated`` level. D20's sequence is read the current singleton → preserve
  the existing tree → make the minimal structural change → write under an optimistic
  precondition (the Resource signature) → read back → verify the structure.
* ``--create-runtime-token`` creates the **Runtime API token** for that profile,
  granted exactly that level (D09: separate credentials per privilege profile, never
  one shared Runtime super-key). The Gateway generates the key and hands back a
  ``{key, hash}`` pair; the hash is what the token resource stores, and the raw key
  is written to the operator-named ``0600`` file and nowhere else — not to stdout,
  not to a report, not to the evidence.

The second run of the same setup is a ``NO CHANGE`` run because the file itself is
the proof of ownership: the secret in it must hash to the token hash the Gateway
serves, and every other case — a token whose secret this CLI cannot prove, a level
with a shape this CLI did not write, a file that already holds a credential — is a
``BLOCKED`` line reported to the operator rather than an overwrite.

Nothing here dispatches a request: the observations are injected read-only Gateway
clients, the documents are pure, and the one local write is the secret file.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ignition_rest_mcp.cli.setup_native import gateway as gw
from ignition_rest_mcp.cli.setup_native.inputs import (
    API_TOKEN_TYPE,
    CONFIG_COLLECTION,
    Inputs,
    SECURITY_LEVEL_PARENT,
    SECURITY_LEVELS_TYPE,
    warn_posix_modes_unavailable,
)

#: The documented API-token extension point (``config.profile.type``).
BASIC_TOKEN_PROFILE = "basic-token"
SECRET_FILE_MODE = 0o600
LEVEL_DESCRIPTION = "ignition-mcp Runtime MCP Security Level for the {profile} profile (deployment-owned)."
TOKEN_DESCRIPTION = "ignition-mcp Runtime MCP service credential for the {profile} profile (deployment-owned)."


class CredentialError(RuntimeError):
    """A generated credential this CLI refuses to store; safe to report."""


class FileError(RuntimeError):
    """The credential file could not be read or created; safe to report."""


# ------------------------------------------------------------------ security levels


def level_tree(document: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """The ``config.securityLevels`` tree of a singleton document, or ``None``.

    ``None`` means the document is not a readable security tree, which D20 treats as
    an unknown topology: the caller aborts rather than guessing at one.
    """

    if not isinstance(document, dict):
        return None
    config = document.get("config")
    levels = config.get("securityLevels") if isinstance(config, dict) else None
    if not isinstance(levels, list) or any(not isinstance(node, dict) for node in levels):
        return None
    return [node for node in levels]


def singleton_collection(document: dict[str, Any] | None) -> str:
    """The collection the observed singleton lives in (``core`` when it does not say)."""

    collection = document.get("collection") if isinstance(document, dict) else None
    return collection if isinstance(collection, str) and collection else CONFIG_COLLECTION


def desired_level(inputs: Inputs) -> dict[str, Any]:
    """The dedicated Runtime Security Level node this CLI would create."""

    return {
        "name": inputs.security_level,
        "description": LEVEL_DESCRIPTION.format(profile=inputs.profile),
        "children": [],
    }


def find_level(tree: list[dict[str, Any]], name: str) -> tuple[list[str], dict[str, Any]] | None:
    """The path of level names down to ``name`` and the node itself (depth-first)."""

    def walk(nodes: list[Any], path: list[str]) -> tuple[list[str], dict[str, Any]] | None:
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("name"), str):
                continue
            here = [*path, str(node["name"])]
            if node["name"] == name:
                return here, node
            children = node.get("children")
            if isinstance(children, list):
                found = walk(children, here)
                if found is not None:
                    return found
        return None

    return walk(tree, [])


def level_shape_problem(node: dict[str, Any]) -> str:
    """How an existing level of our name differs from the shape this CLI writes.

    The shape this CLI manages is a *leaf*: a level an operator has nested levels
    under is not ours to reason about, and neither is one whose ``children`` field is
    not a list at all.
    """

    children = node.get("children")
    if children is None or children == []:
        return ""
    if not isinstance(children, list):
        return "its children field is not a list"
    return f"it carries {len(children)} child level(s)"


def with_managed_level(
    tree: list[dict[str, Any]], inputs: Inputs
) -> tuple[list[dict[str, Any]] | None, str]:
    """The tree with the managed level inserted, or ``(None, reason)``.

    Everything else in the tree travels verbatim: D20's whole point is the *minimal*
    structural change to a singleton every deployment shares.
    """

    parent = SECURITY_LEVEL_PARENT
    matches = [node for node in tree if isinstance(node, dict) and node.get("name") == parent]
    if not matches:
        return None, f"the Gateway's security tree has no {parent} level to place the Runtime level under"
    if len(matches) > 1:
        return None, f"the Gateway's security tree carries {len(matches)} top-level {parent} levels"
    if not isinstance(matches[0].get("children"), list):
        return None, f"the Gateway's {parent} level carries no children list"
    return [_with_child(node, parent, desired_level(inputs)) for node in tree], ""


def _with_child(node: dict[str, Any], parent: str, child: dict[str, Any]) -> dict[str, Any]:
    if node.get("name") != parent:
        return node
    merged = dict(node)
    merged["children"] = [*node["children"], child]
    return merged


def level_paths(tree: list[dict[str, Any]]) -> set[tuple[str, ...]]:
    """Every level's full path in a tree, the unit the read-back verification compares."""

    paths: set[tuple[str, ...]] = set()

    def walk(nodes: list[Any], prefix: tuple[str, ...]) -> None:
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("name"), str):
                continue
            here = (*prefix, str(node["name"]))
            paths.add(here)
            children = node.get("children")
            if isinstance(children, list):
                walk(children, here)

    walk(tree, ())
    return paths


def missing_paths(before: list[dict[str, Any]], after: list[dict[str, Any]] | None) -> list[str]:
    """The levels a write dropped, as dotted paths (``[]`` when the tree survived)."""

    if after is None:
        return ["the Gateway serves no security tree after the write"]
    served = level_paths(after)
    return [".".join(path) for path in sorted(level_paths(before) - served)]


def verify_readback(
    document: dict[str, Any] | None, before: list[dict[str, Any]], inputs: Inputs
) -> str:
    """How the served tree differs from what apply wrote; ``""`` when it landed."""

    tree = level_tree(document)
    if tree is None:
        return "the security-levels singleton is not readable after the write"
    found = find_level(tree, inputs.security_level)
    if found is None:
        return f"{inputs.security_level_path} is not in the served tree"
    path, node = found
    if path != [SECURITY_LEVEL_PARENT, inputs.security_level]:
        return f"{inputs.security_level} reads back at {'.'.join(path)}"
    problem = level_shape_problem(node)
    if problem:
        return f"{inputs.security_level_path} reads back as a non-leaf ({problem})"
    missing = missing_paths(before, tree)
    if missing:
        return f"the write dropped existing levels: {', '.join(missing)}"
    return ""


# --------------------------------------------------------------------- API tokens


def grant_tree(
    tree: list[dict[str, Any]], path: list[str], leaf: dict[str, Any] | None = None
) -> list[dict[str, Any]] | None:
    """The granted-levels tree for one path: each node keeps only the child on it.

    This is the shape a ``basic-token`` profile grants security levels with (the live
    G0 bootstrap wrote the same one by file). Siblings are dropped on purpose — a
    token granting the whole ``Authenticated`` subtree would be the broad generic
    credential D09 rules out. ``leaf`` supplies the last node for a level this run
    creates; ``None`` means the path must already exist in the tree.
    """

    top = next(
        (node for node in tree if isinstance(node, dict) and node.get("name") == path[0]), None,
    )
    if top is None:
        return None
    granted_top = _grant_node(top)
    node = granted_top
    current = top
    for name in path[1:]:
        children = current.get("children")
        child = (
            next((item for item in children if isinstance(item, dict) and item.get("name") == name), None)
            if isinstance(children, list)
            else None
        )
        if child is None and leaf is not None:
            child = leaf
        if child is None:
            return None
        granted = _grant_node(child)
        node["children"] = [granted]
        node = granted
        current = child
    node["children"] = []
    return [granted_top]


def _grant_node(node: dict[str, Any]) -> dict[str, Any]:
    granted: dict[str, Any] = {"name": node.get("name")}
    description = node.get("description")
    if isinstance(description, str):
        granted["description"] = description
    return granted


def token_grant(inputs: Inputs, tree: list[dict[str, Any]], *, creating: bool) -> list[dict[str, Any]] | None:
    """The Runtime API token's granted-levels tree for this profile's dedicated level."""

    path = [SECURITY_LEVEL_PARENT, inputs.security_level]
    return grant_tree(tree, path, desired_level(inputs) if creating else None)


def token_config(
    inputs: Inputs, grant: list[dict[str, Any]], *, token_hash: str, timestamp_ms: int
) -> dict[str, Any]:
    """The API token's ``config`` object: one ``basic-token`` profile granted one level."""

    return {
        "profile": {
            "type": BASIC_TOKEN_PROFILE,
            "secureChannelRequired": not inputs.runtime_token_insecure_channel,
            "securityLevels": grant,
            "timestamp": timestamp_ms,
        },
        "settings": {"tokenHash": token_hash},
    }


def token_description(inputs: Inputs) -> str:
    return TOKEN_DESCRIPTION.format(profile=inputs.profile)


def stored_token_hash(document: dict[str, Any] | None) -> str:
    """The token hash the Gateway serves for an existing API token, ``""`` when unreadable."""

    if not isinstance(document, dict):
        return ""
    config = document.get("config")
    settings = config.get("settings") if isinstance(config, dict) else None
    value = settings.get("tokenHash") if isinstance(settings, dict) else None
    return value if isinstance(value, str) else ""


def token_hash(key: str) -> str:
    """The Gateway's own token-hash derivation, or ``""`` for a key that cannot be one.

    Ignition generates a 32-byte key, represents it as unpadded Base64URL and stores
    the unpadded Base64URL SHA-256 digest of the decoded key bytes (live-recorded by
    the G0 bootstrap, ``tests/harness/runtime-binding/prepare_ci_security.py``).
    """

    padded = key + "=" * (-len(key) % 4)
    try:
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeEncodeError):
        return ""
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode("ascii")


def credential(payload: Any) -> tuple[str, str]:
    """The Gateway's ``(key, hash)`` pair, cross-checked against its own derivation.

    The hash is what the token resource will store and the key is what the operator
    will authenticate with, so a pair that does not agree with the documented
    derivation is a credential this CLI must not create: the file it writes would
    never authenticate.
    """

    document = payload if isinstance(payload, dict) else {}
    key = document.get("key")
    declared = document.get("hash")
    if not isinstance(key, str) or not key or not isinstance(declared, str) or not declared:
        raise CredentialError("the Gateway's API-token generate route returned no key/hash pair")
    derived = token_hash(key)
    if not derived:
        raise CredentialError("the Gateway returned an API key this CLI cannot decode as Base64URL")
    if derived != declared.rstrip("="):
        raise CredentialError(
            "the Gateway's returned key/hash pair disagrees with the documented derivation; "
            "refusing to store a credential that could never authenticate"
        )
    return key, declared


def token_secret(name: str, key: str) -> str:
    """The one-line credential the operator's file holds and the MCP client sends."""

    return f"{name}:{key}"


# ------------------------------------------------------------------ secret files


@dataclass(frozen=True, slots=True, repr=False)
class SecretFile:
    """What the operator's ``--runtime-token-file`` holds; the key is never reported."""

    exists: bool = False
    error: str = ""
    name: str = ""
    key: str = field(default="", repr=False)

    def hashes_to(self, stored_hash: str) -> bool:
        """Whether this file is the secret of the token hash the Gateway serves."""

        return bool(stored_hash) and token_hash(self.key) == stored_hash.rstrip("=")


def observe_secret_file(path: Path) -> SecretFile:
    """Read the credential file, enforcing the mode and shape when it exists.

    A file that exists is the operator's credential: the mode and the one-line
    ``<name>:<key>`` shape are checked before it is used to decide anything.
    """

    if path.is_symlink():
        return SecretFile(exists=False, error="it is a symlink; credential files must be regular files")
    try:
        info = path.stat()
    except FileNotFoundError:
        return SecretFile(exists=False)
    except OSError as error:
        return SecretFile(exists=False, error=f"it cannot be stat'd ({type(error).__name__})")
    if not stat.S_ISREG(info.st_mode):
        return SecretFile(exists=False, error="it is not a regular file")
    mode = stat.S_IMODE(info.st_mode)
    if sys.platform == "win32":
        warn_posix_modes_unavailable()
    elif mode & 0o077:
        return SecretFile(
            exists=False,
            error=f"it is accessible to group or others (mode {mode:04o}); require 0600 and chmod it first",
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return SecretFile(exists=False, error=f"it cannot be read ({type(error).__name__})")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        return SecretFile(exists=False, error=f"it must hold exactly one non-empty line, found {len(lines)}")
    name, separator, key = lines[0].strip().partition(":")
    if not separator or not name or not key:
        return SecretFile(exists=False, error="it must hold exactly one '<name>:<key>' credential line")
    return SecretFile(exists=True, name=name, key=key)


def check_secret_file_target(path: Path) -> str:
    """Preflight for a credential file this run would create; ``""`` when it can be."""

    if path.exists() or path.is_symlink():
        return f"{path} already exists; refusing to overwrite a credential file"
    parent = path.parent
    if not parent.is_dir():
        return f"{parent} is not a directory"
    if not os.access(parent, os.W_OK):
        return f"{parent} is not writable"
    return ""


def write_secret_file(path: Path, secret: str) -> None:
    """Create the credential file with mode 0600, never readable by anyone else.

    ``O_CREAT|O_EXCL`` means the file cannot already exist (so no credential is ever
    overwritten) and cannot be a symlink; ``fchmod`` pins the mode whatever the
    process umask is. A file this function has to abort on is removed again rather
    than left behind half-written. Windows has no POSIX modes, so there the mode is
    neither set nor checked and the operator protects the file with ACLs.
    """

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, SECRET_FILE_MODE)
    except FileExistsError as error:
        raise FileError(f"{path} already exists; refusing to overwrite a credential file") from error
    except OSError as error:
        raise FileError(f"cannot create {path}: {type(error).__name__}") from error
    try:
        try:
            if sys.platform != "win32":
                os.fchmod(descriptor, SECRET_FILE_MODE)
            payload = (secret + "\n").encode("utf-8")
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise OSError(f"short write ({written} of {len(payload)} bytes)")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if sys.platform == "win32":
            warn_posix_modes_unavailable()
        else:
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode != SECRET_FILE_MODE:
                raise OSError(f"the file was created with mode {mode:04o}, not {SECRET_FILE_MODE:04o}")
    except Exception as error:
        # Any failure after creation (including an AttributeError from a missing
        # platform API) removes the file so no 0-byte credential is left behind.
        try:
            os.unlink(path)
        except OSError:
            pass
        raise FileError(f"cannot write {path}: {type(error).__name__}") from error


# -------------------------------------------------------------------- observation


@dataclass(frozen=True, slots=True)
class Observation:
    """The security planes one run reasons over, read only when a flag asks for it."""

    #: The served security tree, ``None`` with ``levels_error`` when it is unreadable.
    levels: list[dict[str, Any]] | None = None
    signature: str = ""
    collection: str = CONFIG_COLLECTION
    levels_error: str = ""
    #: The existing API token document, ``None`` when the Gateway serves none.
    token: dict[str, Any] | None = None
    token_error: str = ""
    secret: SecretFile = SecretFile()


async def observe(client: gw.GatewayRest, inputs: Inputs) -> Observation:
    """Read the Security Levels singleton and (for a token) the credential file and token."""

    levels: list[dict[str, Any]] | None = None
    signature = ""
    collection = CONFIG_COLLECTION
    levels_error = ""
    try:
        document = await client.singleton_document(SECURITY_LEVELS_TYPE)
    except gw.GatewayProbeError as error:
        levels_error = str(error)
    else:
        if document is None:
            levels_error = "the Gateway serves no security-levels singleton"
        else:
            signature = str(document.get("signature") or "")
            collection = singleton_collection(document)
            levels = level_tree(document)
            if levels is None:
                levels_error = "the security-levels singleton carries no readable securityLevels tree"

    token: dict[str, Any] | None = None
    token_error = ""
    secret = SecretFile()
    if inputs.create_runtime_token:
        if inputs.runtime_token_file is not None:
            secret = observe_secret_file(inputs.runtime_token_file)
        try:
            token = await client.resource_document(API_TOKEN_TYPE, inputs.runtime_token)
        except gw.GatewayProbeError as error:
            token_error = str(error)
    return Observation(
        levels=levels,
        signature=signature,
        collection=collection,
        levels_error=levels_error,
        token=token,
        token_error=token_error,
        secret=secret,
    )
