"""Validate Designer Project source for the Runtime MCP Bundle."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

from .constants import (
    HANDLER, IDENTIFIER, PARAMETER_ALLOWED_KEYS, PARAMETER_REQUIRED_KEYS, PARAMETER_TYPES,
    PRIMITIVE_FILES, PROMPT_ARGUMENT_KEYS,
    PROMPT_HANDLER, PROMPTS_DIR, PYTHON2_KEYWORDS, RESOURCES_DIR, TOOLS_DIR,
)
from .jsonio import load_json_object
from .jython import validate_handler, validate_prompt_handler
from .validation import ValidationError, require, to_lf


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _single_segment(value: Any) -> bool:
    return _nonempty_string(value) and value not in {".", ".."} and not any(char in value for char in ("/", "\\", "\x00"))


def _snapshot(project_dir: Path) -> tuple[dict[str, bytes], set[str]]:
    require(project_dir.is_dir() and not project_dir.is_symlink(), project_dir, "project directory must exist and be a regular directory")
    files: dict[str, bytes] = {}
    directories: set[str] = set()
    for path in sorted(project_dir.rglob("*")):
        relative = path.relative_to(project_dir).as_posix()
        require(not path.is_symlink(), relative, "symbolic links are not supported")
        if "__pycache__" in path.parts:
            continue
        if path.is_dir():
            directories.add(relative)
        else:
            require(path.is_file(), relative, "must be a regular file")
            # Every file in a Designer project is text. A Windows checkout may hold CRLF
            # files; read them as LF so sizes and the built ZIP match a Linux checkout.
            data = to_lf(path.read_bytes(), relative)
            files[relative] = data
    return files, directories


def _metadata(data: bytes, location: str, payload_name: str) -> dict[str, Any]:
    resource = load_json_object(data, location)
    require(resource.get("scope") == "G", location, "scope must be G")
    require(type(resource.get("version")) is int and resource["version"] == 1, location, "version must be integer 1")
    for key in ("restricted", "overridable"):
        require(type(resource.get(key)) is bool, location, f"{key} must be boolean")
    require(resource.get("files") == [payload_name], location, f"files must be exactly [{payload_name}]")
    raw_attributes = resource.get("attributes")
    require(type(raw_attributes) is dict, location, "attributes must be an object")
    attributes = cast(dict[str, Any], raw_attributes)
    require(_single_segment(attributes.get("title")), location, "attributes.title must be a nonempty single path segment")
    require(_nonempty_string(attributes.get("description")), location, "attributes.description must be a nonempty string")
    return attributes


def _validate_parameter_default(parameter_type: str, value: Any, location: str) -> None:
    valid = {
        "string": isinstance(value, str),
        "object": type(value) is dict,
        "array": type(value) is list,
        "number": type(value) in {int, float},
        "integer": type(value) is int,
        "boolean": type(value) is bool,
    }
    require(valid.get(parameter_type, False), location, "default value must match parameter type")


def _tool_parameters(data: bytes, location: str) -> list[str]:
    attributes = _metadata(data, location, HANDLER)
    raw_parameters = attributes.get("parameters")
    require(type(raw_parameters) is list, location, "parameters must be an ordered list")
    parameters = cast(list[Any], raw_parameters)
    names: list[str] = []
    for index, parameter in enumerate(parameters):
        field = f"{location}: parameters[{index}]"
        require(type(parameter) is dict, field, "parameter must be an object")
        keys = set(parameter)
        require(PARAMETER_REQUIRED_KEYS <= keys <= PARAMETER_ALLOWED_KEYS, field, "parameter keys must include name, description, type, required and may include default")
        name = parameter["name"]
        require(isinstance(name, str) and IDENTIFIER.fullmatch(name) is not None and name not in PYTHON2_KEYWORDS and name != "builder", field, "name must be a non-reserved Jython 2 identifier other than builder")
        require(name not in names, field, "parameter names must be unique")
        require(_nonempty_string(parameter["description"]), field, "description must be a nonempty string")
        require(parameter["type"] in PARAMETER_TYPES, field, "unsupported parameter type")
        require(type(parameter["required"]) is bool, field, "required must be boolean")
        if "default" in parameter:
            _validate_parameter_default(parameter["type"], parameter["default"], field)
        names.append(name)
    return names


def _text_resource(data: bytes, payload: bytes, location: str) -> None:
    attributes = _metadata(data, location, "data.bin")
    require(attributes.get("dataType") == "Text", location, "dataType must be Text")
    size = attributes.get("size")
    require(type(size) is int and size >= 0, location, "size must be a nonnegative integer byte count")
    require(size == len(payload), location, "size must match data.bin UTF-8 byte count")
    require(_nonempty_string(attributes.get("mimeType")), location, "mimeType must be nonempty")
    try:
        payload.decode("utf-8")
    except UnicodeError as error:
        raise ValidationError(f"{location}: data.bin must contain UTF-8 Text") from error


def _prompt(data: bytes, location: str) -> None:
    attributes = _metadata(data, location, PROMPT_HANDLER)
    raw_arguments = attributes.get("arguments")
    require(type(raw_arguments) is list, location, "arguments must be an ordered list")
    arguments = cast(list[Any], raw_arguments)
    names: set[str] = set()
    for index, argument in enumerate(arguments):
        field = f"{location}: arguments[{index}]"
        require(type(argument) is dict, field, "argument must be an object")
        require(set(argument) == PROMPT_ARGUMENT_KEYS, field, "argument keys must be exactly name, title, description, required")
        for key in ("name", "title", "description"):
            require(_nonempty_string(argument[key]), field, f"{key} must be nonempty")
        require(argument["name"] not in names, field, "argument names must be unique")
        require(type(argument["required"]) is bool, field, "required must be boolean")
        names.add(argument["name"])


SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SOURCE_REVISION_TOKEN = b"__BUNDLE_SOURCE_REVISION__"
STAMPED_REVISION = re.compile(r'bundleSourceRevision\s*=\s*"([0-9a-f]{40}|UNSTAMPED)"')
BUNDLE_VERSION_LITERAL = re.compile(r'bundleVersion\s*=\s*"([^"]+)"')
MANAGED_MARKER = "ignition-mcp-managed: product=ignition-runtime-bundle; bundle="


def _read_sibling_version(project_dir: Path) -> str:
    path = project_dir.parent / "BUNDLE_VERSION"
    require(path.is_file(), path, "BUNDLE_VERSION must sit next to a project that ships bundle_info")
    version = path.read_text(encoding="utf-8").strip()
    require(SEMVER.fullmatch(version) is not None, path, "BUNDLE_VERSION must be MAJOR.MINOR.PATCH")
    return version


def _check_bundle_identity(project: dict[str, Any], files: dict[str, bytes], root: Path,
                           handler_path: str) -> None:
    """D21 single identity source: handler literal + ownership marker == BUNDLE_VERSION."""

    version = _read_sibling_version(root)
    handler = files[handler_path].decode("utf-8")
    literal = BUNDLE_VERSION_LITERAL.search(handler)
    if literal is None:
        raise ValidationError(f"{handler_path}: bundle_info must assign a bundleVersion string literal")
    require(
        literal.group(1) == version, handler_path,
        f"bundle_info bundleVersion {literal.group(1)!r} does not equal BUNDLE_VERSION {version!r}",
    )
    description = project.get("description")
    require(isinstance(description, str), "project.json", "description must be a string")
    assert isinstance(description, str)
    lines = description.splitlines()
    require(
        bool(lines) and lines[-1] == MANAGED_MARKER + version,
        "project.json",
        f"description must end with the managed-project marker line {MANAGED_MARKER + version!r}",
    )
    token_count = handler.encode("utf-8").count(SOURCE_REVISION_TOKEN)
    if token_count == 1:
        return  # unstamped source; the builder replaces it deterministically
    require(token_count == 0, handler_path, "bundle_info must contain the revision token exactly once")
    require(
        STAMPED_REVISION.search(handler) is not None, handler_path,
        "bundle_info must carry a stamped bundleSourceRevision (40-hex SHA or UNSTAMPED)",
    )


def validate_project(project_dir: str | Path) -> dict[str, bytes]:
    root = Path(project_dir)
    files, directories = _snapshot(root)
    require("project.json" in files, root, "missing project.json")
    project = load_json_object(files["project.json"], "project.json")
    require(type(project.get("enabled")) is bool, "project.json", "enabled must be boolean")
    require(project.get("inheritable") is False, "project.json", "inheritable must be false")

    allowed_directories = {"com.inductiveautomation.mcp", *PRIMITIVE_FILES}
    allowed_files = {"project.json"}
    primitives: list[tuple[str, str, str]] = []

    for base, payload in PRIMITIVE_FILES.items():
        leaves: list[str] = []
        for directory in sorted(path for path in directories if path.startswith(base + "/")):
            for segment in directory[len(base) + 1 :].split("/"):
                require(_single_segment(segment), directory, "resource path segments must be nonempty and local")
            if any(directory + "/" + name in files for name in ("resource.json", payload)):
                leaves.append(directory)
        for directory in leaves:
            if any(directory.startswith(parent + "/") for parent in leaves if parent != directory):
                raise ValidationError(f"{directory}: nested primitive resources are not supported")
            allowed_directories.add(directory)
            parent = directory.rsplit("/", 1)[0]
            while parent != base:
                allowed_directories.add(parent)
                parent = parent.rsplit("/", 1)[0]
            allowed_files.update({directory + "/resource.json", directory + "/" + payload})
            primitives.append((base, directory, payload))

    unknown = sorted((directories - allowed_directories) | (set(files) - allowed_files))
    require(not unknown, unknown[0] if unknown else root, "unsupported file/directory in self-contained Runtime Bundle profile")
    require(bool(primitives), root, "at least one Tool, Text Resource or Prompt is required")

    for base, directory, payload in primitives:
        resource_path = directory + "/resource.json"
        payload_path = directory + "/" + payload
        require(resource_path in files, resource_path, "missing required file")
        require(payload_path in files, payload_path, "missing required file")
        if base == TOOLS_DIR:
            validate_handler(files[payload_path], payload_path, _tool_parameters(files[resource_path], resource_path))
        elif base == RESOURCES_DIR:
            _text_resource(files[resource_path], files[payload_path], resource_path)
        elif base == PROMPTS_DIR:
            _prompt(files[resource_path], resource_path)
            validate_prompt_handler(files[payload_path], payload_path)

    bundle_info_handler = TOOLS_DIR + "/bundle_info/" + HANDLER
    if bundle_info_handler in files:
        _check_bundle_identity(project, files, root, bundle_info_handler)

    return {name: files[name] for name in sorted(allowed_files)}
