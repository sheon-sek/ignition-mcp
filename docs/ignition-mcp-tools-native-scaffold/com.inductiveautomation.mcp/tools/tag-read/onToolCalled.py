def onToolCalled(builder, tagPaths, timeout, timestampFormat):
	# NATIVE_BINDING_PENDING
	# These three project-local ports are NOT Ignition builder APIs.
	def emitSuccess(domain):
		raise NotImplementedError("NATIVE_BINDING_PENDING: bind the project success port using verified native result APIs")

	def emitToolError(canonicalError):
		raise NotImplementedError("NATIVE_BINDING_PENDING: bind the project tool-error port using verified native result APIs")

	def recordDiagnostic(code, exception, stage, correlationId):
		# Bind with diagnostic filtering while preserving the original exception chain.
		# No Ignition logging API or transport behavior is assumed by this port.
		raise NotImplementedError("NATIVE_BINDING_PENDING: bind the project diagnostic port")

	nativeBindingsReady = False
	if not nativeBindingsReady:
		# Raising here is an unbound scaffold failure, NOT native MCP isError.
		raise NotImplementedError("NATIVE_BINDING_PENDING: verify and bind all three project ports before native execution")

	from java.lang import Exception as JavaException
	from java.lang import Boolean, Number
	from java.util import UUID, Date, Map, List
	import math

	correlationId = unicode(UUID.randomUUID())
	runtimeExceptions = (Exception, JavaException)

	class InvalidArgument(ValueError):
		pass

	def canonicalError(code, message):
		return {"code": code, "message": message, "correlationId": correlationId}

	def optionalText(value):
		return None if value is None else unicode(value)

	def quality(value):
		if value is None:
			raise TypeError("QualifiedValue has no QualityCode")
		name = value.getName() if hasattr(value, "getName") else unicode(value)
		level = value.getLevel()
		if name is None or level is None or not unicode(name) or not unicode(level):
			raise TypeError("QualityCode has incomplete name or level")
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
		if hasattr(value, "getValue") and hasattr(value, "getQuality") and hasattr(value, "getTimestamp"):
			timestamp = value.getTimestamp()
			if timestamp is not None and not isinstance(timestamp, Date):
				raise TypeError("QualifiedValue timestamp is not a Date or None")
			return {"value": jsonValue(value.getValue()), "quality": quality(value.getQuality()), "timestamp": jsonValue(timestamp)}
		if hasattr(value, "getCode") and hasattr(value, "isGood"):
			return quality(value)
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
		raise TypeError("Add an explicit serializer for " + unicode(type(value)))

	try:
		paths = tagPaths
		if not isinstance(paths, (list, tuple, List)) or len(paths) == 0:
			raise InvalidArgument("tagPaths must be a non-empty array of non-empty strings.")
		for path in paths:
			if not isinstance(path, basestring) or not path.strip():
				raise InvalidArgument("tagPaths must contain non-empty strings.")
		if timeout is None:
			timeout = 5000
		if timestampFormat is None:
			timestampFormat = "iso8601"
		if not isinstance(timestampFormat, basestring) or timestampFormat not in ("iso8601", "epochMillis"):
			raise InvalidArgument("Unsupported timestampFormat. Supported values: iso8601, epochMillis (case-sensitive).")
		if isinstance(timeout, bool) or not isinstance(timeout, (int, long)) or timeout < 0 or timeout > 2147483647:
			raise InvalidArgument("timeout must be an integer from 0 to 2147483647 milliseconds.")
		paths = list(paths)
	except InvalidArgument as exc:
		recordDiagnostic("invalid_argument", exc, "validation", correlationId)
		return emitToolError(canonicalError("invalid_argument", unicode(exc)))
	except runtimeExceptions as exc:
		recordDiagnostic("internal_error", exc, "validation", correlationId)
		return emitToolError(canonicalError("internal_error", "The request could not be processed."))

	try:
		values = system.tag.readBlocking(paths, int(timeout))
	except runtimeExceptions as exc:
		recordDiagnostic("upstream_error", exc, "native", correlationId)
		return emitToolError(canonicalError("upstream_error", "The Tag read operation could not be completed."))

	try:
		isArray = hasattr(values, "getClass") and values.getClass().isArray()
		if not isinstance(values, (list, tuple, List)) and not isArray:
			exc = TypeError("Native read result is not a sequence")
			recordDiagnostic("schema_mismatch", exc, "native_result", correlationId)
			return emitToolError(canonicalError("schema_mismatch", "The Tag read result did not match the expected batch shape."))
		if len(values) > len(paths):
			exc = ValueError("Native read returned more items than requested")
			recordDiagnostic("schema_mismatch", exc, "native_result", correlationId)
			return emitToolError(canonicalError("schema_mismatch", "The Tag read result did not match the requested item count."))
		items = []
		succeeded = 0
		for index in range(len(paths)):
			try:
				if index >= len(values) or values[index] is None:
					raise TypeError("Native read did not provide a QualifiedValue for this slot")
				value = values[index]
				if not (hasattr(value, "getValue") and hasattr(value, "getQuality") and hasattr(value, "getTimestamp")):
					raise TypeError("Native read item is not a QualifiedValue")
				converted = jsonValue(value)
				item = {"path": paths[index], "status": "ok", "value": converted["value"], "quality": converted["quality"], "timestamp": converted["timestamp"]}
			except runtimeExceptions as exc:
				recordDiagnostic("schema_mismatch", exc, "item_serialization", correlationId)
				item = {"path": paths[index], "status": "error", "error": canonicalError("schema_mismatch", "The Tag read item could not be represented in the expected shape.")}
			if item["status"] == "ok":
				succeeded += 1
			items.append(item)
		domain = {"items": items, "summary": {"requested": len(paths), "succeeded": succeeded, "failed": len(paths) - succeeded}, "meta": {"correlationId": correlationId}}
	except runtimeExceptions as exc:
		recordDiagnostic("internal_error", exc, "batch_processing", correlationId)
		return emitToolError(canonicalError("internal_error", "The Tag read batch could not be processed."))
	return emitSuccess(domain)
