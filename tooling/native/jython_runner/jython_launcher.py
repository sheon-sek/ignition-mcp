# mypy: ignore-errors
"""Jython 2.7 process entry point for recorded Runtime Tool fixtures.

A ``schemaVersion`` 2 fixture replays an **ordered** list of native calls:

    {"target": "system.tag.readBlocking", "args": [[path], 5000],
     "result": {"kind": "qualified-values", "items": [...]}}

The handler must make exactly those calls, in that order, with those arguments:
an unrecorded call, a mismatched argument, a mismatched keyword value, or a
recorded call the handler never makes fails the run. That is what lets a fixture
prove the negative half of a behavior: "the policy Tag was never read", "no
write was dispatched", instead of only its positive half.

Only recorded state crosses the boundary (``system.tag.*``, ``system.config.*``,
``system.alarm.*``, ``system.util.audit``). Deterministic helpers
(``system.util.jsonDecode``, ``jsonEncode``, ``getLogger``) are real
implementations in this process.

A recorded value may also carry a ``nativeType`` marker for a shape JSON cannot
express, so a fixture can replay what a handler distinguishes by duck typing:
``{"nativeType": "Dataset", "columns": [...], "rows": [[...]]}`` becomes the
Dataset-like object whose ``getColumnCount``/``getColumnName``/``getRowCount``/
``getValueAt`` a handler reads, ``{"nativeType": "JavaArray", "items": [...]}``
becomes an ``Object[]`` (which Jython hands a handler as an ``array.array``), and
``{"nativeType": "JythonLong"|"BigInteger"|"BigDecimal", "text": "..."}`` becomes the
interpreter's own ``long`` or the Java number, whose decimal text is not the JSON
number a fixture would otherwise carry.

A fixture may also inject a failure of one of those real helpers so a handler's
failure path is reachable from a recording:

    "utilFailures": {"jsonEncode": {"onCall": 1, "message": "recorded failure"}}

``onCall`` counts that helper's calls in the handler, 1-based. It exists because a
handler's own serialization step can only be exercised if it can be made to fail,
and the handler's outcome reporting must not depend on it.
"""

from __future__ import print_function

import json
import sys

from java.lang import Object, RuntimeException
from java.lang.reflect import Array
from java.math import BigDecimal, BigInteger
from java.util import ArrayList, LinkedHashMap


class _RecordedError(Exception):
    """The handler diverged from the recorded call sequence."""


class _Logger(object):
    """Handler diagnostics go to stderr, so a failing fixture shows the reason."""

    def warn(self, message):
        print("WARN " + str(message), file=sys.stderr)

    def error(self, message):
        print("ERROR " + str(message), file=sys.stderr)


class _QualityCode(object):
    def __init__(self, recorded):
        self.code = recorded.get("code", 0)
        self.name = recorded.get("name", "Good")
        self.level = recorded.get("level", "Good")
        self.good = recorded.get("good", True)
        self.diagnosticMessage = recorded.get("diagnosticMessage")

    def getCode(self):
        return self.code

    def getName(self):
        return self.name

    def getLevel(self):
        return self.level

    def isGood(self):
        return self.good

    def getDiagnosticMessage(self):
        return self.diagnosticMessage

    def __unicode__(self):
        return unicode(self.name)  # noqa: F821 - Jython 2.7 built-in

    def __str__(self):
        return self.name.encode("utf-8")


class _QualifiedValue(object):
    """A recorded QualifiedValue: the handler reads .value/.quality/.timestamp."""

    def __init__(self, recorded):
        self.value = _recorded_value(recorded.get("value"))
        self.quality = _QualityCode(recorded.get("quality") or {})
        self.timestamp = recorded.get("timestamp")

    def getValue(self):
        return self.value

    def getQuality(self):
        return self.quality

    def getTimestamp(self):
        return self.timestamp


def _recorded_value(recorded):
    """A recorded Tag value: JSON scalars and arrays as they are, plus the native
    shapes a handler distinguishes by duck typing. A ``nativeType`` marker turns a
    recorded mapping into the Java-like object the handler sees, so a fixture can
    replay a value shape JSON cannot express (a Dataset, a Java array) or a Java
    number whose text is not the JSON number a fixture would otherwise carry."""

    if isinstance(recorded, dict):
        marker = recorded.get("nativeType")
        if marker == "Dataset":
            return _RecordedDataset(recorded)
        if marker == "JavaArray":
            return _recorded_java_array(recorded)
        if marker == "JythonLong":
            return long(recorded["text"].encode("ascii"))  # noqa: F821 - Jython 2.7 built-in
        if marker == "BigInteger":
            return BigInteger(recorded["text"].encode("ascii"))
        if marker == "BigDecimal":
            return BigDecimal(recorded["text"].encode("ascii"))
    return recorded


def _recorded_java_array(recorded):
    """A recorded Java array: an ``Object[]``, which Jython hands a handler as an
    ``array.array``, so this replays the shape a Gateway returns for an array Tag
    value. JSON has no array type of its own, so a fixture names it explicitly."""

    items = recorded.get("items") or []
    native = Array.newInstance(Object, len(items))
    for index in range(len(items)):
        native[index] = _recorded_value(items[index])
    return native


class _RecordedDataset(object):
    """A recorded Dataset: the handler reads columns and cells through the API."""

    def __init__(self, recorded):
        self.columns = list(recorded.get("columns") or [])
        self.rows = [list(row) for row in (recorded.get("rows") or [])]

    def getColumnCount(self):
        return len(self.columns)

    def getColumnName(self, index):
        return self.columns[index]

    def getRowCount(self):
        return len(self.rows)

    def getValueAt(self, row, column):
        return self.rows[row][column]


class _RecordedTagPath(object):
    def __init__(self, recorded):
        self.nativeType = recorded["nativeType"]
        self.text = recorded["text"]

    def __unicode__(self):
        return unicode(self.text)  # noqa: F821 - Jython 2.7 built-in

    def __str__(self):
        return self.text.encode("utf-8")


class _RecordedResults(ArrayList):
    """`system.tag.query` returns an iterable with a continuationPoint attribute."""

    def __init__(self, recorded):
        ArrayList.__init__(self)
        self.nativeType = recorded["nativeType"]
        self.continuationPoint = recorded.get("continuationPoint")
        for item in recorded["items"]:
            native = LinkedHashMap()
            for key, value in item.items():
                if isinstance(value, dict) and value.get("nativeType") == "TagPath":
                    value = _RecordedTagPath(value)
                native.put(key, value)
            self.add(native)


class _QualifiedValues(ArrayList):
    def __init__(self, recorded):
        ArrayList.__init__(self)
        for item in recorded["items"]:
            self.add(_QualifiedValue(item))


class _QualityCodes(ArrayList):
    def __init__(self, recorded):
        ArrayList.__init__(self)
        for item in recorded["items"]:
            self.add(None if item is None else _QualityCode(item))


class _Configurations(ArrayList):
    """A recorded `system.tag.getConfiguration` result: a Java list of maps.

    Each node is a `LinkedHashMap` so the handler's `Map`/`List` normalization
    branches are exercised, exactly as the Gateway's own return value exercises
    them.
    """

    def __init__(self, recorded):
        ArrayList.__init__(self)
        for item in recorded["items"]:
            native = LinkedHashMap()
            for key, value in item.items():
                native.put(key, value)
            self.add(native)


class _RecordedResource(object):
    def __init__(self, recorded):
        self.name = recorded.get("name")
        self.enabled = recorded.get("enabled", True)
        self.signature = recorded.get("signature")


class _ShelvedPath(object):
    """A recorded `system.alarm.getShelvedPaths` entry.

    The handler reads `.getPath()`, `.getUser()`, `.getExpiration()` and
    `.isExpired()`, which is the shape `alarm_shelved_list` already consumes.
    """

    def __init__(self, recorded):
        self.path = recorded.get("path")
        self.user = recorded.get("user")
        self.expiration = recorded.get("expiration")
        self.expired = recorded.get("expired", False)

    def getPath(self):
        return self.path

    def getUser(self):
        return self.user

    def getExpiration(self):
        return self.expiration

    def isExpired(self):
        return self.expired


class _ShelvedPaths(ArrayList):
    def __init__(self, recorded):
        ArrayList.__init__(self)
        for item in recorded["items"]:
            self.add(_ShelvedPath(item))


def _raise_recorded(entry):
    message = "recorded native failure: " + str(entry.get("target"))
    detail = (entry.get("result") or {}).get("message")
    if detail:
        message = message + ": " + str(detail)
    # A java.lang exception, so the handler's `except (Exception, JavaException)`
    # sees exactly what the Gateway would raise.
    raise RuntimeException(message)


class _Recorder(object):
    def __init__(self, calls):
        self.pending = list(calls)
        self.position = 0

    def take(self, target, args, kwargs):
        if self.position >= len(self.pending):
            raise _RecordedError("unrecorded call to " + target)
        entry = self.pending[self.position]
        recorded_target = entry.get("target")
        if recorded_target != target:
            raise _RecordedError(
                "call order mismatch: expected " + str(recorded_target) + ", handler made " + target
            )
        if "args" in entry and list(entry["args"]) != list(args):
            raise _RecordedError("argument mismatch for " + target + ": " + repr(args))
        if "kwargs" in entry:
            expected = entry["kwargs"]
            for key in expected:
                if key not in kwargs or kwargs[key] != expected[key]:
                    raise _RecordedError(
                        "keyword mismatch for " + target + "." + str(key) + ": " + repr(kwargs.get(key))
                    )
        self.position += 1
        return self._build(target, entry)

    def _build(self, target, entry):
        result = entry.get("result") or {}
        kind = result.get("kind")
        if kind == "raise":
            _raise_recorded(entry)
        if kind == "none":
            return None
        if kind == "qualified-values":
            return _QualifiedValues(result)
        if kind == "quality-codes":
            return _QualityCodes(result)
        if kind == "configurations":
            return _Configurations(result)
        if kind == "boolean":
            return bool(result.get("value", False))
        if kind == "raw":
            # A recorded answer that is not the type the handler expects, so a
            # fixture can exercise the branch that refuses a non-answer.
            return result.get("value")
        if kind == "results":
            return _RecordedResults(result)
        if kind == "resource":
            return _RecordedResource(result)
        if kind == "shelved-paths":
            return _ShelvedPaths(result)
        if kind == "missing":
            return None
        raise _RecordedError("unsupported recorded result kind for " + target + ": " + str(kind))

    def verify_exhausted(self):
        if self.position != len(self.pending):
            remaining = [str(entry.get("target")) for entry in self.pending[self.position:]]
            raise _RecordedError("recorded calls the handler never made: " + ", ".join(remaining))


class _Tag(object):
    def __init__(self, recorder):
        self.recorder = recorder

    def readBlocking(self, *args, **kwargs):
        return self.recorder.take("system.tag.readBlocking", args, kwargs)

    def writeBlocking(self, *args, **kwargs):
        return self.recorder.take("system.tag.writeBlocking", args, kwargs)

    def query(self, *args, **kwargs):
        return self.recorder.take("system.tag.query", args, kwargs)

    def getConfiguration(self, *args, **kwargs):
        return self.recorder.take("system.tag.getConfiguration", args, kwargs)

    def configure(self, *args, **kwargs):
        return self.recorder.take("system.tag.configure", args, kwargs)

    def exists(self, *args, **kwargs):
        return self.recorder.take("system.tag.exists", args, kwargs)


class _Config(object):
    def __init__(self, recorder):
        self.recorder = recorder

    def getResource(self, *args, **kwargs):
        return self.recorder.take("system.config.getResource", args, kwargs)


class _Alarm(object):
    def __init__(self, recorder):
        self.recorder = recorder

    def shelve(self, *args, **kwargs):
        return self.recorder.take("system.alarm.shelve", args, kwargs)

    def unshelve(self, *args, **kwargs):
        return self.recorder.take("system.alarm.unshelve", args, kwargs)

    def getShelvedPaths(self, *args, **kwargs):
        return self.recorder.take("system.alarm.getShelvedPaths", args, kwargs)


class _Util(object):
    def __init__(self, recorder, failures=None):
        self.recorder = recorder
        self.failures = failures or {}
        self.encodeCalls = 0

    def getLogger(self, name):
        return _Logger()

    def jsonEncode(self, value):
        self.encodeCalls += 1
        failure = self.failures.get("jsonEncode")
        if failure and failure.get("onCall") == self.encodeCalls:
            raise RuntimeException(str(failure.get("message", "recorded jsonEncode failure")))
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def jsonDecode(self, value):
        return json.loads(value)

    def getProjectName(self):
        return "recorded-project"

    def audit(self, *args, **kwargs):
        return self.recorder.take("system.util.audit", args, kwargs)


class _System(object):
    def __init__(self, recorder, failures=None):
        self.tag = _Tag(recorder)
        self.config = _Config(recorder)
        self.alarm = _Alarm(recorder)
        self.util = _Util(recorder, failures)


class _Builder(object):
    def text(self, value):
        return {"type": "text", "text": value}


def _main():
    if len(sys.argv) != 3:
        raise ValueError("usage: jython_launcher.py HANDLER FIXTURE")
    handler_path = sys.argv[1]
    fixture_path = sys.argv[2]
    with open(fixture_path, "rb") as stream:
        fixture = json.load(stream)
    if fixture.get("schemaVersion") != 2:
        raise ValueError("Unsupported recorded fixture schemaVersion")

    recorder = _Recorder(fixture["calls"])
    namespace = {"system": _System(recorder, fixture.get("utilFailures"))}
    execfile(handler_path, namespace)  # noqa: F821 - Jython 2.7 built-in
    arguments = fixture["arguments"]
    ordered = [arguments[name] for name in fixture["parameterOrder"]]
    result = namespace["onToolCalled"](_Builder(), *ordered)
    recorder.verify_exhausted()
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    _main()
