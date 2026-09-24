"""The engine every ``ignition-mcp`` command runs on (D32 sections 3, 4, 6 and 9).

Modules, and what a command ticket uses from each:

* :mod:`.main`: :func:`~.main.register_stage` with a :class:`~.main.Stage` (a
  ``plan`` that gets a :class:`~.main.Context` with no write method, an ``apply``
  that gets an :class:`~.main.ApplyContext` whose ``save``, ``write_secret`` and
  ``gateway_writer`` check the engine's gate, and the stage's own inputs),
  ``COMMANDS`` (set a command's ``handler``, add to its ``extra_inputs``) and
  :class:`~.main.Context`, the one object a handler or stage gets.
* :mod:`.resolve`: :class:`~.resolve.InputSpec`, :class:`~.resolve.Needed` and
  :class:`~.resolve.Risk` for Explicit acceptance, and :class:`~.resolve.Secret`.
* :mod:`.report`: ``ctx.reporter.step(...)`` and :class:`~.report.Status`.
* :mod:`.deployment`: the deployment directory, ``deployment.toml`` and secret files.
* :mod:`.errors`: the stable ``--json`` error codes.
* :mod:`.prompter`: the :class:`~.prompter.Prompter` interface tests replace.
"""
