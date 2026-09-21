"""``ignition-mcp setup-native`` — read-only Runtime Bundle doctor / plan / verify (D20, D21).

The whole desired state comes from the bundle manifest JSON the operator passes
in; this package never reads repo-relative paths (no ``contracts/``, no
``packages/ignition-runtime-bundle/``).  Only the detection half of D20 lives
here: ``apply`` (Phase 4) and ``install-module`` (Phase 6) are deliberately
absent, so no command in this package can mutate a Gateway.
"""
