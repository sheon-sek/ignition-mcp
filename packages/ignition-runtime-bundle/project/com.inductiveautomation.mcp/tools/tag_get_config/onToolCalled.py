def onToolCalled(builder, path, recursive, overridesOnly, maxResults):
	from java.lang import Boolean, Number, Enum, Exception as JavaException
	from java.util import UUID, Date, Map, List
	import math
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.TagGetConfig")

	def toolError(code, message):
		error = {"code": code, "message": message, "correlationId": correlationId}
		logger.warn("correlationId=" + correlationId + " code=" + code + " " + message)
		return {"content": builder.text(system.util.jsonEncode(error)), "isError": True}

	def encodeNulls(value):
		if value is None:
			return {"$ignition": "null"}
		if isinstance(value, dict):
			if "$ignition" in value:
				return {"$ignition": "object", "entries": [[key, encodeNulls(child)] for key, child in sorted(value.items())]}
			return dict((key, encodeNulls(child)) for key, child in value.items())
		if isinstance(value, (list, tuple)):
			return [encodeNulls(child) for child in value]
		return value

	def jsonValue(value):
		if value is None or isinstance(value, (bool, int, long, basestring)):
			return value
		if isinstance(value, Boolean):
			return value.booleanValue()
		if isinstance(value, float):
			if math.isnan(value) or math.isinf(value):
				return {"type": "non-finite-number", "text": unicode(value)}
			return value
		if isinstance(value, Number):
			typeName = unicode(value.getClass().getName())
			if typeName in ("java.lang.Byte", "java.lang.Short", "java.lang.Integer", "java.lang.Long", "java.math.BigInteger"):
				return long(unicode(value))
			if typeName == "java.math.BigDecimal":
				return {"type": "decimal", "text": unicode(value)}
			return jsonValue(float(value.doubleValue()))
		if isinstance(value, Date):
			return unicode(value.toInstant().toString())
		if isinstance(value, dict):
			return dict((unicode(key), jsonValue(child)) for key, child in value.items())
		if hasattr(value, "iteritems"):
			return dict((unicode(key), jsonValue(child)) for key, child in value.iteritems())
		if isinstance(value, Map):
			return dict((unicode(entry.getKey()), jsonValue(entry.getValue())) for entry in value.entrySet())
		if isinstance(value, (list, tuple, List)):
			return [jsonValue(child) for child in value]
		if isinstance(value, Enum):
			return unicode(value)
		if hasattr(value, "getClass") and value.getClass().isArray():
			return [jsonValue(child) for child in value]
		return {"type": "native-object", "class": unicode(value.getClass().getName()) if hasattr(value, "getClass") else unicode(type(value)), "text": unicode(value)}

	def countNodes(values):
		count = 0
		for item in values:
			count += 1
			children = item.get("tags") if isinstance(item, dict) else None
			if children is not None:
				count += countNodes(children)
		return count

	def validPath(value):
		if not isinstance(value, basestring) or not value.strip():
			return False
		value = value.strip()
		if not value.startswith("["):
			return False
		closing = value.find("]")
		if closing <= 1 or value.startswith("[.]") or value.startswith("[~]") or value.startswith("[]"):
			return False
		body = value[closing + 1:]
		return "_types_" not in [segment for segment in body.split("/") if segment]

	stage = "validation"
	try:
		if not validPath(path):
			return toolError("invalid_argument", "path must be an absolute provider-qualified Tag path outside the internal UDT definition namespace.")
		path = path.strip()
		if recursive is None:
			recursive = False
		if overridesOnly is None:
			overridesOnly = False
		if not isinstance(recursive, bool) or not isinstance(overridesOnly, bool):
			return toolError("invalid_argument", "recursive and overridesOnly must be boolean.")
		if maxResults is None:
			maxResults = 50
		if isinstance(maxResults, bool) or not isinstance(maxResults, (int, long)) or maxResults < 1 or maxResults > 200:
			return toolError("invalid_argument", "maxResults must be an integer from 1 to 200.")
		stage = "native_read"
		nativeConfiguration = system.tag.getConfiguration(path, bool(recursive), bool(overridesOnly))
		stage = "result_normalization"
		configuration = jsonValue(nativeConfiguration)
		stage = "result_count"
		count = countNodes(configuration)
		if count > maxResults:
			return toolError("limit_exceeded", "Tag configuration exceeds maxResults; use a narrower path or disable recursive retrieval.")
		domain = {"path": path, "recursive": bool(recursive), "overridesOnly": bool(overridesOnly), "configuration": configuration, "summary": {"returned": count, "limit": int(maxResults)}, "meta": {"correlationId": correlationId}}
		domain = encodeNulls(domain)
		stage = "serialization"
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > 262144:
			return toolError("limit_exceeded", "Structured output exceeds the 256 KiB default limit; use a narrower path or disable recursive retrieval.")
		return {"structuredContent": domain}
	except (Exception, JavaException) as exc:
		exceptionType = unicode(type(exc))
		logger.error("correlationId=" + correlationId + " stage=" + stage + " exceptionType=" + exceptionType + " tag_get_config failed: " + unicode(exc))
		return toolError("upstream_error", "The Tag configuration read could not be completed.")
