def onToolCalled(builder, tagPaths, timeout, timestampFormat):
	from java.lang import Boolean, Number
	from java.util import UUID, Date, Map, List
	import math
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.TagRead")

	def toolError(code, message):
		return {"content": builder.text(code + ": " + message + "; correlationId=" + correlationId), "isError": True}

	def optionalText(value):
		return None if value is None else unicode(value)

	def quality(value):
		if value is None:
			raise TypeError("QualifiedValue has no QualityCode")
		name = value.getName() if hasattr(value, "getName") else unicode(value)
		level = value.getLevel()
		return {"code": int(value.getCode()), "name": unicode(name), "level": unicode(level), "good": bool(value.isGood()), "diagnosticMessage": optionalText(value.getDiagnosticMessage())}

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
			if timestampFormat == "epochMillis":
				return long(value.getTime())
			return unicode(value.toInstant().toString())
		if hasattr(value, "getColumnCount") and hasattr(value, "getRowCount") and hasattr(value, "getValueAt"):
			columns = [unicode(value.getColumnName(c)) for c in range(value.getColumnCount())]
			rows = [[jsonValue(value.getValueAt(r, c)) for c in range(value.getColumnCount())] for r in range(value.getRowCount())]
			return {"columns": columns, "rows": rows}
		if isinstance(value, dict):
			return dict((unicode(k), jsonValue(v)) for k, v in value.items())
		if isinstance(value, Map):
			return dict((unicode(entry.getKey()), jsonValue(entry.getValue())) for entry in value.entrySet())
		if isinstance(value, (list, tuple, List)):
			return [jsonValue(child) for child in value]
		if hasattr(value, "getClass") and value.getClass().isArray():
			return [jsonValue(child) for child in value]
		raise TypeError("Unsupported Tag value type: " + unicode(type(value)))

	def validCurrentValuePath(value):
		if not isinstance(value, basestring) or not value.strip():
			return False
		value = value.strip()
		if not value.startswith("["):
			return False
		closing = value.find("]")
		if closing <= 1 or closing >= len(value) - 1:
			return False
		if value.startswith("[.]") or value.startswith("[~]") or value.startswith("[]"):
			return False
		body = value[closing + 1:]
		if "." in body or "[" in body or "]" in body:
			return False
		segments = [segment for segment in body.split("/") if segment]
		if "_types_" in segments:
			return False
		return True

	try:
		if not isinstance(tagPaths, (list, tuple, List)) or len(tagPaths) == 0:
			return toolError("invalid_argument", "tagPaths must be a non-empty array.")
		if len(tagPaths) > 500:
			return toolError("limit_exceeded", "tagPaths exceeds the hard limit of 500.")
		paths = []
		for path in tagPaths:
			if not validCurrentValuePath(path):
				return toolError("invalid_argument", "Every path must be an absolute provider-qualified current Tag value path; Tag property and document sub-paths are not allowed.")
			paths.append(path.strip())
		if timeout is None:
			timeout = 10000
		if isinstance(timeout, bool) or not isinstance(timeout, (int, long)) or timeout < 0 or timeout > 30000:
			return toolError("invalid_argument", "timeout must be an integer from 0 to 30000 milliseconds.")
		if timestampFormat is None:
			timestampFormat = "iso8601"
		if timestampFormat not in ("iso8601", "epochMillis"):
			return toolError("invalid_argument", "timestampFormat must be iso8601 or epochMillis.")
		values = system.tag.readBlocking(paths, int(timeout))
		if len(values) != len(paths):
			return toolError("schema_mismatch", "Native Tag read result count does not match the request.")
		items = []
		succeeded = 0
		for index in range(len(paths)):
			try:
				value = values[index]
				if value is None or not (hasattr(value, "getValue") and hasattr(value, "getQuality") and hasattr(value, "getTimestamp")):
					raise TypeError("Native item is not a QualifiedValue")
				item = {"path": paths[index], "status": "ok", "value": jsonValue(value.getValue()), "quality": quality(value.getQuality()), "timestamp": jsonValue(value.getTimestamp())}
				succeeded += 1
			except Exception as itemExc:
				logger.error("correlationId=" + correlationId + " tag_read item serialization failed: " + unicode(itemExc))
				item = {"path": paths[index], "status": "error", "error": {"code": "schema_mismatch", "message": "The Tag read item could not be represented.", "correlationId": correlationId}}
			items.append(item)
		domain = {"items": items, "summary": {"requested": len(paths), "succeeded": succeeded, "failed": len(paths) - succeeded}, "meta": {"correlationId": correlationId}}
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > 262144:
			return toolError("limit_exceeded", "Structured output exceeds the Phase 1 default limit of 256 KiB.")
		return {"structuredContent": domain}
	except Exception as exc:
		logger.error("correlationId=" + correlationId + " tag_read failed: " + unicode(exc))
		return toolError("upstream_error", "The Tag read operation could not be completed.")
