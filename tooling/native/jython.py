"""Conservative static checks for Ignition Jython 2.7 MCP handlers."""

from __future__ import annotations

import ast
import io
import re
import symtable
import tokenize
from typing import cast

from .constants import PROMPT_HANDLER
from .validation import ValidationError, require

_NEWER_AST_NODES = frozenset(
    {
        "AsyncFunctionDef", "AsyncFor", "AsyncWith", "Await", "JoinedStr", "FormattedValue",
        "AnnAssign", "NamedExpr", "Nonlocal", "YieldFrom", "Match", "TryStar", "TypeAlias",
        "TemplateStr", "Interpolation", "MatMult",
    }
)


def _check_common_syntax(tree: ast.AST, tokens: list[tokenize.TokenInfo], location: str) -> None:
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    def common(condition: bool, node: ast.AST, feature: str) -> None:
        require(
            condition,
            f"{location}:{getattr(node, 'lineno', 1)}",
            f"{feature} is outside the Python 2.7 / Python 3 common syntax subset",
        )

    for node in ast.walk(tree):
        common(type(node).__name__ not in _NEWER_AST_NODES, node, type(node).__name__)
        if isinstance(node, (ast.FunctionDef, ast.Lambda)):
            args = node.args
            common(
                not args.kwonlyargs and not getattr(args, "posonlyargs", []),
                node,
                "keyword-only or positional-only parameters",
            )
            common(not getattr(node, "returns", None), node, "return annotation")
            common(not getattr(node, "type_params", []), node, "type parameters")
        if isinstance(node, ast.arg):
            common(node.annotation is None, node, "parameter annotation")
        if isinstance(node, ast.Raise):
            common(node.cause is None, node, "raise from")
        if isinstance(node, ast.ClassDef):
            common(
                not node.keywords and not getattr(node, "type_params", []),
                node,
                "class keyword arguments or type parameters",
            )
        if isinstance(node, ast.Dict):
            common(all(key is not None for key in node.keys), node, "dictionary unpacking")
        if isinstance(node, ast.Starred):
            common(isinstance(parents.get(node), ast.Call), node, "extended iterable unpacking")
        if isinstance(node, ast.Constant):
            common(node.value is not Ellipsis, node, "ellipsis literal")

    for token in tokens:
        if token.type == tokenize.NUMBER:
            require(
                "_" not in token.string,
                f"{location}:{token.start[0]}",
                "numeric underscores are outside the common syntax subset",
            )
        if token.type == tokenize.NAME:
            require(
                token.string.isascii(),
                f"{location}:{token.start[0]}",
                "non-ASCII identifiers are outside the common syntax subset",
            )


def _check_self_contained(tree: ast.AST, source: str, location: str) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            forbidden = any(alias.name.split(".")[0] == "MCP" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            forbidden = (node.module or "").split(".")[0] == "MCP"
        else:
            forbidden = False
        require(
            not forbidden,
            location,
            "MCP shared helper dependency is not included in the self-contained Runtime Bundle profile",
        )

    def inspect(table: symtable.SymbolTable) -> None:
        for symbol in table.get_symbols():
            if symbol.get_name() == "MCP" and symbol.is_referenced() and symbol.is_global():
                raise ValidationError(
                    f"{location}: unbound MCP helper reference is forbidden in self-contained handlers"
                )
        for child in table.get_children():
            inspect(child)

    inspect(symtable.symtable(source, location, "exec"))


def validate_handler(
    data: bytes,
    location: str,
    parameter_names: list[str],
    entrypoint: str = "onToolCalled",
) -> None:
    try:
        source = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError as error:
        raise ValidationError(f"{location}: handler must be UTF-8") from error

    # Jython 2.7 refuses non-ASCII source without a PEP 263 coding declaration,
    # and the bundle declares none, so a stray character (a section sign in a
    # comment) is a runtime syntax error the static checks must catch.
    if any(ord(character) > 127 for character in source):
        raise ValidationError(f"{location}: handler source must be ASCII (Jython 2.7 without an encoding declaration)")

    lines = source.split("\n")
    header = r"def[ \t]+" + re.escape(entrypoint) + r"[ \t]*\([^\r\n]*\)[ \t]*:[ \t]*(?:#.*)?"
    require(
        bool(lines) and re.fullmatch(header, lines[0]) is not None,
        location,
        f"first line must contain the complete one-line def {entrypoint}(...): header",
    )

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        tree = ast.parse(source, filename=location)
        compile(tree, location, "exec")
    except (SyntaxError, ValueError, tokenize.TokenError) as error:
        raise ValidationError(f"{location}: invalid handler syntax: {error}") from error

    require(
        len(tree.body) == 1
        and isinstance(tree.body[0], ast.FunctionDef)
        and tree.body[0].name == entrypoint,
        location,
        f"{entrypoint} must be the only top-level statement/function",
    )
    function = cast(ast.FunctionDef, tree.body[0])
    args = function.args
    require(
        not args.defaults
        and not args.kw_defaults
        and not args.vararg
        and not args.kwarg
        and not args.kwonlyargs
        and not getattr(args, "posonlyargs", []),
        location,
        "handler signature must use positional parameters without defaults, *args or **kwargs",
    )
    require(
        [arg.arg for arg in args.args] == ["builder"] + parameter_names,
        location,
        "handler signature parameter names/order must match builder followed by resource parameters",
    )

    string_continuations: set[int] = set()
    for token in tokens:
        if token.type == tokenize.STRING:
            string_continuations.update(range(token.start[0] + 1, token.end[0] + 1))
    for number, line in enumerate(lines[1:], 2):
        if not line.strip() or number in string_continuations:
            continue
        prefix = re.match(r"[ \t\f]*", line)
        indentation = prefix.group() if prefix else ""
        require(
            all(char == "\t" for char in indentation),
            f"{location}:{number}",
            "handler indentation must use Tabs only",
        )
        require(
            bool(indentation) or line.startswith("#"),
            f"{location}:{number}",
            "handler body lines must be indented with Tabs",
        )

    _check_common_syntax(tree, tokens, location)
    _check_self_contained(tree, source, location)


def validate_prompt_handler(data: bytes, location: str) -> None:
    validate_handler(data, location, ["arguments"], PROMPT_HANDLER.removesuffix(".py"))
