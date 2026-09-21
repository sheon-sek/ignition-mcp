# mypy: ignore-errors
"""Jython 2.7 process entry point for recorded Runtime Tool fixtures."""

from __future__ import print_function

import json
import sys

from java.util import ArrayList, LinkedHashMap


class _Logger(object):
    def warn(self, message):
        pass

    def error(self, message):
        pass


class _Util(object):
    def getLogger(self, name):
        return _Logger()

    def jsonEncode(self, value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class _RecordedTagPath(object):
    def __init__(self, recorded):
        self.nativeType = recorded["nativeType"]
        self.text = recorded["text"]

    def __unicode__(self):
        return unicode(self.text)  # noqa: F821 - Jython 2.7 built-in

    def __str__(self):
        return self.text.encode("utf-8")


class _RecordedResults(ArrayList):
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


class _Tag(object):
    def __init__(self, fixture):
        self.fixture = fixture

    def query(self, *args, **kwargs):
        if self.fixture["operation"] != "system.tag.query":
            raise ValueError("Fixture operation does not match system.tag.query")
        return _RecordedResults(self.fixture["result"])


class _System(object):
    def __init__(self, fixture):
        self.tag = _Tag(fixture)
        self.util = _Util()


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
    if fixture.get("schemaVersion") != 1:
        raise ValueError("Unsupported recorded fixture schemaVersion")

    namespace = {"system": _System(fixture)}
    execfile(handler_path, namespace)  # noqa: F821 - Jython 2.7 built-in
    arguments = fixture["arguments"]
    ordered = [arguments[name] for name in fixture["parameterOrder"]]
    result = namespace["onToolCalled"](_Builder(), *ordered)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    _main()
