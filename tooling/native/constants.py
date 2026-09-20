"""Designer MCP resource constants verified by the project skill/scaffold."""

from __future__ import annotations

import re

TOOLS_DIR = "com.inductiveautomation.mcp/tools"
RESOURCES_DIR = "com.inductiveautomation.mcp/resources"
PROMPTS_DIR = "com.inductiveautomation.mcp/prompts"
HANDLER = "onToolCalled.py"
PROMPT_HANDLER = "onPrompt.py"
PRIMITIVE_FILES = {TOOLS_DIR: HANDLER, RESOURCES_DIR: "data.bin", PROMPTS_DIR: PROMPT_HANDLER}
PARAMETER_TYPES = frozenset({"string", "object", "array", "number", "integer", "boolean"})
PARAMETER_REQUIRED_KEYS = frozenset({"name", "description", "type", "required"})
PARAMETER_ALLOWED_KEYS = PARAMETER_REQUIRED_KEYS | {"default"}
PROMPT_ARGUMENT_KEYS = frozenset({"name", "title", "description", "required"})
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
PYTHON2_KEYWORDS = frozenset(
    "and as assert break class continue def del elif else except exec finally for from global if "
    "import in is lambda not or pass print raise return try while with yield None True False".split()
)
NATIVE_BINDING_PENDING_COMMENT = "# NATIVE_BINDING_PENDING"
