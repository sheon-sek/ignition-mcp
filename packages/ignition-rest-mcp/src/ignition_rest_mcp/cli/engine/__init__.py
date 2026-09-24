"""The engine every ``ignition-mcp`` command runs on (D32 sections 3, 4, 6 and 9).

Modules, and what a command ticket uses from each:

* :mod:`.main`: ``COMMANDS`` (set a command's ``handler``), ``SETUP_STAGES``
  (append a ``setup`` stage), ``standard_inputs`` (add an input) and
  :class:`~.main.Context`, the one object a handler gets.
* :mod:`.resolve`: :class:`~.resolve.InputSpec`, :class:`~.resolve.Needed` and
  :class:`~.resolve.Risk` for Explicit acceptance, and :class:`~.resolve.Secret`.
* :mod:`.report`: :class:`~.report.Status` for ``ctx.reporter.end``.
* :mod:`.deployment`: the deployment directory, ``deployment.toml`` and secret files.
* :mod:`.errors`: the stable ``--json`` error codes.
* :mod:`.prompter`: the :class:`~.prompter.Prompter` interface tests replace.
"""
