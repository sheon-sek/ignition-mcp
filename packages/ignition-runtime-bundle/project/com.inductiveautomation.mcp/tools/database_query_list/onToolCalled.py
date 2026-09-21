def onToolCalled(builder):
	from java.lang import System
	from java.util import UUID
	from java.lang import Exception as JavaException
	import json
	import re
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.DatabaseQueryList")

	def toolError(code, message):
		error = {"code": code, "message": message, "correlationId": correlationId}
		logger.warn("correlationId=" + correlationId + " code=" + code + " " + message)
		return {"content": builder.text(system.util.jsonEncode(error)), "isError": True}

	def integer(value, name, minimum, maximum):
		if isinstance(value, bool) or not isinstance(value, (int, long)):
			raise ValueError(name + " must be an integer.")
		if value < minimum or value > maximum:
			raise ValueError(name + " is outside its allowed range.")
		return int(value)

	def normalizeParameter(name, raw):
		if not isinstance(name, basestring) or not re.match(r"^[A-Za-z][A-Za-z0-9_]{0,63}$", name):
			raise ValueError("Registry parameter names must be simple identifiers up to 64 characters.")
		if not isinstance(raw, dict):
			raise ValueError("Registry parameter definitions must be objects.")
		allowedKeys = set(("type", "required", "minimum", "maximum", "maxLength"))
		if not set(raw.keys()).issubset(allowedKeys):
			raise ValueError("Registry parameter definition contains unsupported keys.")
		valueType = raw.get("type")
		if valueType not in ("string", "integer", "number", "boolean", "datetime"):
			raise ValueError("Registry parameter type is unsupported.")
		required = raw.get("required", False)
		if not isinstance(required, bool):
			raise ValueError("Registry parameter required must be boolean.")
		public = {"name": name, "type": valueType, "required": required}
		if "minimum" in raw:
			if valueType not in ("integer", "number") or isinstance(raw["minimum"], bool) or not isinstance(raw["minimum"], (int, long, float)):
				raise ValueError("minimum is only valid for numeric parameters.")
			public["minimum"] = raw["minimum"]
		if "maximum" in raw:
			if valueType not in ("integer", "number") or isinstance(raw["maximum"], bool) or not isinstance(raw["maximum"], (int, long, float)):
				raise ValueError("maximum is only valid for numeric parameters.")
			public["maximum"] = raw["maximum"]
		if "minimum" in raw and "maximum" in raw and raw["minimum"] > raw["maximum"]:
			raise ValueError("Registry parameter minimum exceeds maximum.")
		if "maxLength" in raw:
			if valueType != "string":
				raise ValueError("maxLength is only valid for string parameters.")
			public["maxLength"] = integer(raw["maxLength"], "parameter maxLength", 1, 4096)
		return public

	def loadRegistry():
		raw = System.getenv("IGNITION_MCP_DATABASE_QUERY_REGISTRY_JSON")
		if raw is None or not unicode(raw).strip():
			return []
		raw = unicode(raw)
		if len(raw.encode("utf-8")) > 32768:
			raise ValueError("Database query registry exceeds the 32 KiB deployment-config limit.")
		try:
			document = json.loads(raw)
		except Exception:
			raise ValueError("Database query registry is not valid JSON.")
		if not isinstance(document, dict) or document.get("schemaVersion") != 1 or not isinstance(document.get("entries"), list):
			raise ValueError("Database query registry must be a schemaVersion 1 object with an entries array.")
		if len(document["entries"]) > 100:
			raise ValueError("Database query registry exceeds 100 approved aliases.")
		entries = []
		seen = set()
		for rawEntry in document["entries"]:
			if not isinstance(rawEntry, dict):
				raise ValueError("Database query registry entries must be objects.")
			requiredKeys = set(("alias", "description", "project", "path", "resultMode", "datasourcePolicy", "parameters", "pagination"))
			if set(rawEntry.keys()) != requiredKeys:
				raise ValueError("Database query registry entry keys do not match the approved schema.")
			alias = rawEntry["alias"]
			if not isinstance(alias, basestring) or not re.match(r"^[a-z][a-z0-9_]{0,63}$", alias):
				raise ValueError("Database query alias must use lower snake_case and be at most 64 characters.")
			if alias in seen:
				raise ValueError("Database query aliases must be unique.")
			seen.add(alias)
			description = rawEntry["description"]
			project = rawEntry["project"]
			path = rawEntry["path"]
			if not isinstance(description, basestring) or not description.strip() or len(description) > 512:
				raise ValueError("Database query description must be a non-empty string up to 512 characters.")
			if not isinstance(project, basestring):
				raise ValueError("Database query project must be a non-empty fixed project name.")
			project = project.strip()
			if not project or len(project) > 128:
				raise ValueError("Database query project must be a non-empty fixed project name.")
			if not isinstance(path, basestring):
				raise ValueError("Database query path must be a fixed project-relative Named Query path.")
			path = path.strip()
			if not path or len(path) > 512 or path.startswith("/") or ".." in path.split("/"):
				raise ValueError("Database query path must be a fixed project-relative Named Query path.")
			if rawEntry["resultMode"] not in ("dataset", "scalar"):
				raise ValueError("Database query resultMode must be dataset or scalar.")
			if rawEntry["datasourcePolicy"] != "named-query-fixed":
				raise ValueError("Database query datasourcePolicy must be named-query-fixed.")
			if not isinstance(rawEntry["parameters"], dict) or len(rawEntry["parameters"]) > 32:
				raise ValueError("Database query parameters must be an object with at most 32 Value parameters.")
			parameters = []
			for name in sorted(rawEntry["parameters"]):
				parameters.append(normalizeParameter(name, rawEntry["parameters"][name]))
			pagination = rawEntry["pagination"]
			if not isinstance(pagination, dict) or "mode" not in pagination:
				raise ValueError("Database query pagination policy is invalid.")
			if rawEntry["resultMode"] == "scalar":
				if pagination != {"mode": "none"}:
					raise ValueError("Scalar database queries must use pagination mode none.")
				publicPagination = {"mode": "none"}
			elif pagination["mode"] == "offset":
				expected = set(("mode", "limitParameter", "offsetParameter", "defaultPageSize", "hardPageSize", "maxOffset"))
				if set(pagination.keys()) != expected:
					raise ValueError("Offset pagination policy keys do not match the approved schema.")
				limitParameter = pagination["limitParameter"]
				offsetParameter = pagination["offsetParameter"]
				if (not isinstance(limitParameter, basestring) or not re.match(r"^[A-Za-z][A-Za-z0-9_]{0,63}$", limitParameter)
						or not isinstance(offsetParameter, basestring) or not re.match(r"^[A-Za-z][A-Za-z0-9_]{0,63}$", offsetParameter)
						or limitParameter == offsetParameter):
					raise ValueError("Offset pagination requires distinct simple native limit/offset parameter names up to 64 characters.")
				if limitParameter in rawEntry["parameters"] or offsetParameter in rawEntry["parameters"]:
					raise ValueError("Caller parameters cannot override handler-owned pagination parameters.")
				defaultPageSize = integer(pagination["defaultPageSize"], "defaultPageSize", 1, 200)
				hardPageSize = integer(pagination["hardPageSize"], "hardPageSize", defaultPageSize, 2000)
				maxOffset = integer(pagination["maxOffset"], "maxOffset", 0, 1000000)
				publicPagination = {"mode": "offset", "defaultPageSize": defaultPageSize, "hardPageSize": hardPageSize, "maxOffset": maxOffset}
			elif pagination["mode"] == "fixed":
				if set(pagination.keys()) != set(("mode", "maxRows")):
					raise ValueError("Fixed pagination policy keys do not match the approved schema.")
				publicPagination = {"mode": "fixed", "maxRows": integer(pagination["maxRows"], "maxRows", 1, 2000)}
			else:
				raise ValueError("Dataset database queries must use offset or fixed bounded-result policy.")
			entries.append({"alias": alias, "description": description.strip(), "resultMode": rawEntry["resultMode"], "parameters": parameters, "pagination": publicPagination})
		return entries

	try:
		entries = loadRegistry()
		domain = {"entries": entries, "summary": {"approved": len(entries)}, "meta": {"correlationId": correlationId}}
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > 262144:
			return toolError("limit_exceeded", "Database query registry output exceeds the 256 KiB default limit.")
		return {"structuredContent": domain}
	except ValueError as exc:
		return toolError("schema_mismatch", unicode(exc))
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " database_query_list failed: " + unicode(exc))
		return toolError("internal_error", "The approved database query registry could not be loaded.")
