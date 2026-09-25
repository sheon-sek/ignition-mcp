"""Content drift of the managed bundle project (D32 section 10, issue #80).

``setup`` compares the project the Gateway exports with the bundle archive it would
import. Only the MCP resources count: every folder under
``com.inductiveautomation.mcp/tools``, ``resources`` and ``prompts`` that holds files
is one Tool, Text Resource or Prompt. ``project.json`` is left to the ownership
marker, bundle version and inheritable checks that already read it.

The comparison, ``bundle-content-v1``, removes what the Gateway changes on import
and nothing else. The 8.3.8 live rehearsal of issue #80 showed two changes:

* ``resource.json`` comes back re-serialized, with its ``attributes`` keys in
  another order and no trailing newline. It is compared as parsed JSON.
* nothing else. The Tool scripts and ``data.bin`` files come back byte for byte.

One more difference is setup's own: the builder stamps the checkout's git revision
into ``bundle_info``, and a checkout on a later commit with the same bundle version
stamps another one. A file the bundle stamps is compared with any stamped revision
replaced by one placeholder on both sides.

Both archives must have passed the D15 ZIP safety gate before they reach this
module; the D16 fingerprint pass does that for the export.
"""

from __future__ import annotations

import io
import json
import re
import zipfile

#: The folder of the MCP Module's project resources.
MANAGED_ROOT = "com.inductiveautomation.mcp/"
#: The resource kinds the drift check covers: Tools, Text Resources and Prompts.
MANAGED_KINDS = ("tools", "resources", "prompts")
#: How many differing resources a report names before it counts the rest.
REPORT_LIMIT = 10

_STAMP = re.compile(rb'"(?:[0-9a-f]{40}|UNSTAMPED)"')
_PLACEHOLDER = b'"<source revision>"'


def managed_resources(archive: bytes) -> dict[str, dict[str, bytes]]:
    """``{"tools/<name>": {file name: bytes}}`` for every MCP resource in ``archive``."""

    resources: dict[str, dict[str, bytes]] = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        for info in zipped.infolist():
            name = info.filename
            if info.is_dir() or not name.startswith(MANAGED_ROOT):
                continue
            folder, _, file = name[len(MANAGED_ROOT):].rpartition("/")
            if folder.partition("/")[0] not in MANAGED_KINDS or "/" not in folder:
                continue
            resources.setdefault(folder, {})[file] = zipped.read(info)
    return resources


def _normal(file: str, data: bytes, stamped: bool) -> object:
    if file == "resource.json":
        try:
            return json.loads(data)
        except ValueError:
            return data
    return _STAMP.sub(_PLACEHOLDER, data) if stamped else data


def differences(served: bytes, bundle: bytes) -> list[str]:
    """The MCP resources whose content differs, each with what differs, sorted by name.

    ``served`` is the Gateway's export and ``bundle`` the archive setup would import.
    An empty list means the project holds exactly the bundle's Tools, Text Resources
    and Prompts.
    """

    have = managed_resources(served)
    want = managed_resources(bundle)
    found: list[str] = []
    for name in sorted(set(have) | set(want)):
        if name not in want:
            found.append(f"{name} (not in the bundle)")
            continue
        if name not in have:
            found.append(f"{name} (missing)")
            continue
        files = sorted(set(have[name]) | set(want[name]))
        changed = []
        for file in files:
            if file not in have[name] or file not in want[name]:
                changed.append(file)
                continue
            stamped = _STAMP.search(want[name][file]) is not None and file != "resource.json"
            if _normal(file, have[name][file], stamped) != _normal(file, want[name][file], stamped):
                changed.append(file)
        if changed:
            found.append(f"{name} ({', '.join(changed)})")
    return found


def summary(found: list[str], limit: int = REPORT_LIMIT) -> str:
    """``found`` as one bounded line: the first ``limit`` names and a count of the rest."""

    shown = ", ".join(found[:limit])
    rest = len(found) - limit
    return shown + (f" and {rest} more" if rest > 0 else "")
