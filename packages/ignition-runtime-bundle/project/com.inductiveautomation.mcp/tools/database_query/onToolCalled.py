def onToolCalled(builder, alias, parameters, pageSize, offset):
	from java.lang import Boolean, Number, System, Exception as JavaException
	from java.util import UUID, Date, Map, List
	from java.time import Instant
	import json
	import math
	import re
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.DatabaseQuery")

	def toolError(code, message):
		error = {"code": code, "message": message, "correlationId": correlationId}
		logger.warn("correlationId=" + correlationId + " code=" + code + " " + message)
		return {"content": builder.text(system.util.jsonEncode(error)), "isError": True}

	class EncodedNumber(object):
		def __init__(self, kind, text):
			self.kind = kind
			self.text = text

	def encodeNulls(value):
		if value is None:
			return {"$ignition": "null"}
		if isinstance(value, EncodedNumber):
			return {"type": value.kind, "text": value.text}
		if isinstance(value, dict):
			reservedNumberShape = value.get("type") in ("decimal", "non-finite-number") and "text" in value
			if "$ignition" in value or reservedNumberShape:
				return {"$ignition": "object", "entries": [[key, encodeNulls(child)] for key, child in sorted(value.items())]}
			return dict((key, encodeNulls(child)) for key, child in value.items())
		if isinstance(value, (list, tuple)):
			return [encodeNulls(child) for child in value]
		return value

	def integer(value, name, minimum, maximum):
		if isinstance(value, bool) or not isinstance(value, (int, long)):
			raise ValueError(name + " must be an integer.")
		if value < minimum or value > maximum:
			raise ValueError(name + " is outside its allowed range.")
		return int(value)

	def mapValue(value, name):
		if value is None:
			return {}
		if isinstance(value, dict):
			return value
		if isinstance(value, Map):
			return dict((unicode(entry.getKey()), entry.getValue()) for entry in value.entrySet())
		raise ValueError(name + " must be an object.")

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
		spec = {"type": valueType, "required": required}
		if "minimum" in raw:
			if valueType not in ("integer", "number") or isinstance(raw["minimum"], bool) or not isinstance(raw["minimum"], (int, long, float)):
				raise ValueError("minimum is only valid for numeric parameters.")
			spec["minimum"] = raw["minimum"]
		if "maximum" in raw:
			if valueType not in ("integer", "number") or isinstance(raw["maximum"], bool) or not isinstance(raw["maximum"], (int, long, float)):
				raise ValueError("maximum is only valid for numeric parameters.")
			spec["maximum"] = raw["maximum"]
		if "minimum" in spec and "maximum" in spec and spec["minimum"] > spec["maximum"]:
			raise ValueError("Registry parameter minimum exceeds maximum.")
		if "maxLength" in raw:
			if valueType != "string":
				raise ValueError("maxLength is only valid for string parameters.")
			spec["maxLength"] = integer(raw["maxLength"], "parameter maxLength", 1, 4096)
		return spec

	def loadRegistry():
		raw = System.getenv("IGNITION_MCP_DATABASE_QUERY_REGISTRY_JSON")
		if raw is None or not unicode(raw).strip():
			return {}
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
		registry = {}
		for rawEntry in document["entries"]:
			if not isinstance(rawEntry, dict):
				raise ValueError("Database query registry entries must be objects.")
			requiredKeys = set(("alias", "description", "project", "path", "resultMode", "datasourcePolicy", "parameters", "pagination"))
			if set(rawEntry.keys()) != requiredKeys:
				raise ValueError("Database query registry entry keys do not match the approved schema.")
			entryAlias = rawEntry["alias"]
			if not isinstance(entryAlias, basestring) or not re.match(r"^[a-z][a-z0-9_]{0,63}$", entryAlias):
				raise ValueError("Database query alias must use lower snake_case and be at most 64 characters.")
			if entryAlias in registry:
				raise ValueError("Database query aliases must be unique.")
			if not isinstance(rawEntry["description"], basestring) or not rawEntry["description"].strip() or len(rawEntry["description"]) > 512:
				raise ValueError("Database query description must be a non-empty string up to 512 characters.")
			project = rawEntry["project"]
			path = rawEntry["path"]
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
			resultMode = rawEntry["resultMode"]
			if resultMode not in ("dataset", "scalar"):
				raise ValueError("Database query resultMode must be dataset or scalar.")
			if rawEntry["datasourcePolicy"] != "named-query-fixed":
				raise ValueError("Database query datasourcePolicy must be named-query-fixed.")
			if not isinstance(rawEntry["parameters"], dict) or len(rawEntry["parameters"]) > 32:
				raise ValueError("Database query parameters must be an object with at most 32 Value parameters.")
			parameterSpecs = {}
			for name in rawEntry["parameters"]:
				parameterSpecs[name] = normalizeParameter(name, rawEntry["parameters"][name])
			pagination = rawEntry["pagination"]
			if not isinstance(pagination, dict) or "mode" not in pagination:
				raise ValueError("Database query pagination policy is invalid.")
			if resultMode == "scalar":
				if pagination != {"mode": "none"}:
					raise ValueError("Scalar database queries must use pagination mode none.")
				normalizedPagination = {"mode": "none"}
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
				if limitParameter in parameterSpecs or offsetParameter in parameterSpecs:
					raise ValueError("Caller parameters cannot override handler-owned pagination parameters.")
				normalizedPagination = {
					"mode": "offset",
					"limitParameter": limitParameter,
					"offsetParameter": offsetParameter,
					"defaultPageSize": integer(pagination["defaultPageSize"], "defaultPageSize", 1, 200),
					"hardPageSize": integer(pagination["hardPageSize"], "hardPageSize", 1, 2000),
					"maxOffset": integer(pagination["maxOffset"], "maxOffset", 0, 1000000)
				}
				if normalizedPagination["defaultPageSize"] > normalizedPagination["hardPageSize"]:
					raise ValueError("defaultPageSize exceeds hardPageSize.")
			elif pagination["mode"] == "fixed":
				if set(pagination.keys()) != set(("mode", "maxRows")):
					raise ValueError("Fixed pagination policy keys do not match the approved schema.")
				normalizedPagination = {"mode": "fixed", "maxRows": integer(pagination["maxRows"], "maxRows", 1, 2000)}
			else:
				raise ValueError("Dataset database queries must use offset or fixed bounded-result policy.")
			registry[entryAlias] = {
				"project": project.strip(),
				"path": path.strip(),
				"resultMode": resultMode,
				"parameters": parameterSpecs,
				"pagination": normalizedPagination
			}
		return registry

	def coerceParameter(name, value, spec):
		valueType = spec["type"]
		if value is None:
			raise ValueError("Null is not an accepted Named Query Value parameter; omit optional parameters instead.")
		if valueType == "string":
			if not isinstance(value, basestring):
				raise ValueError(name + " must be a string.")
			if len(value) > spec.get("maxLength", 4096):
				raise ValueError(name + " exceeds its maximum string length.")
			return unicode(value)
		if valueType == "integer":
			if isinstance(value, bool) or not isinstance(value, (int, long, Number)):
				raise ValueError(name + " must be an integer.")
			if isinstance(value, Number) and not isinstance(value, (int, long)):
				numeric = float(value.doubleValue())
				if numeric != math.floor(numeric):
					raise ValueError(name + " must be an integer.")
				value = long(numeric)
			value = long(value)
		elif valueType == "number":
			if isinstance(value, bool) or not isinstance(value, (int, long, float, Number)):
				raise ValueError(name + " must be numeric.")
			value = float(value.doubleValue()) if isinstance(value, Number) and not isinstance(value, (int, long, float)) else float(value)
			if math.isnan(value) or math.isinf(value):
				raise ValueError(name + " must be finite.")
		elif valueType == "boolean":
			if not isinstance(value, bool):
				raise ValueError(name + " must be boolean.")
			return bool(value)
		elif valueType == "datetime":
			if not isinstance(value, basestring):
				raise ValueError(name + " must be an ISO-8601 instant.")
			try:
				return Date(Instant.parse(value.strip()).toEpochMilli())
			except Exception:
				raise ValueError(name + " must be an ISO-8601 instant.")
		if "minimum" in spec and value < spec["minimum"]:
			raise ValueError(name + " is below its approved minimum.")
		if "maximum" in spec and value > spec["maximum"]:
			raise ValueError(name + " exceeds its approved maximum.")
		return value

	def jsonValue(value):
		if value is None or isinstance(value, (bool, int, long, basestring)):
			return value
		if isinstance(value, Boolean):
			return value.booleanValue()
		if isinstance(value, float):
			if math.isnan(value) or math.isinf(value):
				return EncodedNumber("non-finite-number", unicode(value))
			return value
		if isinstance(value, Number):
			typeName = unicode(value.getClass().getName())
			if typeName in ("java.lang.Byte", "java.lang.Short", "java.lang.Integer", "java.lang.Long", "java.math.BigInteger"):
				return long(unicode(value))
			if typeName == "java.math.BigDecimal":
				return EncodedNumber("decimal", unicode(value))
			return jsonValue(float(value.doubleValue()))
		if isinstance(value, Date):
			return unicode(value.toInstant().toString())
		if isinstance(value, dict):
			return dict((unicode(key), jsonValue(child)) for key, child in value.items())
		if isinstance(value, Map):
			return dict((unicode(entry.getKey()), jsonValue(entry.getValue())) for entry in value.entrySet())
		if isinstance(value, (list, tuple, List)):
			return [jsonValue(child) for child in value]
		if hasattr(value, "getClass") and value.getClass().isArray():
			component = unicode(value.getClass().getComponentType().getName())
			if component in ("byte", "java.lang.Byte"):
				raise TypeError("Binary database values are not returned inline.")
			return [jsonValue(child) for child in value]
		raise TypeError("Unsupported database value type: " + unicode(type(value)))

	try:
		if not isinstance(alias, basestring) or not alias.strip():
			return toolError("invalid_argument", "alias must be a non-empty approved database query alias.")
		alias = alias.strip()
		try:
			registry = loadRegistry()
		except ValueError as exc:
			return toolError("schema_mismatch", unicode(exc))
		entry = registry.get(alias)
		if entry is None:
			return toolError("not_found", "The database query alias is not approved in this deployment.")
		rawParameters = mapValue(parameters, "parameters")
		unknown = set(rawParameters.keys()) - set(entry["parameters"].keys())
		if unknown:
			return toolError("invalid_argument", "parameters contains keys that are not approved for this alias.")
		nativeParameters = {}
		for name in entry["parameters"]:
			spec = entry["parameters"][name]
			if name not in rawParameters:
				if spec["required"]:
					return toolError("invalid_argument", "Missing required parameter: " + name)
				continue
			nativeParameters[name] = coerceParameter(name, rawParameters[name], spec)
		if pageSize is None:
			pageSize = 0
		if offset is None:
			offset = 0
		if isinstance(pageSize, bool) or not isinstance(pageSize, (int, long)) or pageSize < 0 or pageSize > 2000:
			return toolError("invalid_argument", "pageSize must be an integer from 0 to 2000; 0 selects the registry default.")
		if isinstance(offset, bool) or not isinstance(offset, (int, long)) or offset < 0 or offset > 1000000:
			return toolError("invalid_argument", "offset must be an integer from 0 to 1,000,000.")
		pagination = entry["pagination"]
		if entry["resultMode"] == "scalar":
			if offset != 0:
				return toolError("invalid_argument", "offset is not valid for scalar aliases.")
			value = system.db.execScalar(entry["path"], nativeParameters, project=entry["project"])
			domain = encodeNulls({"alias": alias, "resultMode": "scalar", "value": jsonValue(value), "meta": {"correlationId": correlationId}})
		else:
			if pagination["mode"] == "offset":
				if offset > pagination["maxOffset"]:
					return toolError("limit_exceeded", "offset exceeds the approved maximum for this alias.")
				effectivePageSize = pagination["defaultPageSize"] if pageSize == 0 else int(pageSize)
				if effectivePageSize < 1 or effectivePageSize > pagination["hardPageSize"]:
					return toolError("limit_exceeded", "pageSize exceeds the approved maximum for this alias.")
				if int(offset) + effectivePageSize - 1 > pagination["maxOffset"]:
					return toolError("limit_exceeded", "The requested page extends beyond the approved maximum offset for this alias.")
				nativeParameters[pagination["limitParameter"]] = effectivePageSize
				nativeParameters[pagination["offsetParameter"]] = int(offset)
				rowLimit = effectivePageSize
			else:
				if offset != 0 or pageSize != 0:
					return toolError("invalid_argument", "pageSize/offset are not valid for a fixed-bounded alias.")
				rowLimit = pagination["maxRows"]
				effectivePageSize = rowLimit
			data = system.db.execQuery(entry["path"], nativeParameters, project=entry["project"])
			if not (hasattr(data, "getColumnCount") and hasattr(data, "getRowCount") and hasattr(data, "getValueAt")):
				return toolError("schema_mismatch", "Approved dataset query did not return an Ignition Dataset.")
			if data.getRowCount() > rowLimit:
				return toolError("schema_mismatch", "Approved Named Query violated its declared pre-execution row bound.")
			columns = [unicode(data.getColumnName(index)) for index in range(data.getColumnCount())]
			rows = []
			for row in range(data.getRowCount()):
				rows.append([jsonValue(data.getValueAt(row, column)) for column in range(data.getColumnCount())])
			if pagination["mode"] == "offset":
				nextOffset = None
				if len(rows) == effectivePageSize:
					candidateOffset = int(offset) + len(rows)
					if candidateOffset > pagination["maxOffset"]:
						return toolError("limit_exceeded", "The result reaches the approved maximum offset; use a more selective approved query.")
					nextOffset = candidateOffset
				page = {"mode": "offset", "offset": int(offset), "limit": effectivePageSize, "nextOffset": nextOffset}
			else:
				page = {"mode": "fixed", "offset": 0, "limit": rowLimit, "nextOffset": None}
			domain = encodeNulls({"alias": alias, "resultMode": "dataset", "columns": columns, "rows": rows, "page": page, "meta": {"correlationId": correlationId}})
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > 262144:
			return toolError("limit_exceeded", "Structured database output exceeds the 256 KiB default limit; request a smaller page or use a more selective approved query.")
		return {"structuredContent": domain}
	except ValueError as exc:
		return toolError("invalid_argument", unicode(exc))
	except TypeError as exc:
		return toolError("schema_mismatch", unicode(exc))
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " database_query failed: " + unicode(exc))
		return toolError("upstream_error", "The approved Named Query could not be completed.")
