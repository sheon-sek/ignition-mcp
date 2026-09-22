"""``ignition-mcp setup-native``: the Runtime Bundle deployment CLI (D20, D21, D26 Phase 6).

The whole desired state comes from the bundle manifest JSON the operator passes
in; this package never reads repo-relative paths (no ``contracts/``, no
``packages/ignition-runtime-bundle/``).  ``doctor``, ``plan`` and ``verify`` only
read.  ``apply`` writes the planned project, Server Config and Runtime Target
Policy, and ``install-module`` writes the Gateway's Module plane from one trusted
local ``.modl``: both go through the curated guarded path, and neither accepts a
certificate, an EULA, a build change or a restart on its own.
"""
