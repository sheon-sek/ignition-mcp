def onToolCalled(builder, paths, startTime, endTime, sampleCount):
	from java.lang import Boolean, Number, Exception as JavaException
	from java.util import UUID, Date, List
	from java.time import Instant
	import math
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.HistorianQuerySeries")

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

	def parseTime(value, name, defaultValue):
		if value is None or (isinstance(value, basestring) and not value.strip()):
			return defaultValue
		if not isinstance(value, basestring):
			raise ValueError(name + " must be an ISO-8601 timestamp string.")
		try:
			return Date(Instant.parse(value.strip()).toEpochMilli())
		except Exception:
			raise ValueError(name + " must be an ISO-8601 instant such as 2026-09-20T12:00:00Z.")

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
		return unicode(value)

	def qualityValue(value):
		if value is None:
			return {"code": None, "name": "Unknown", "good": False}
		if hasattr(value, "getCode"):
			name = value.getName() if hasattr(value, "getName") else unicode(value)
			return {"code": int(value.getCode()), "name": unicode(name), "good": bool(value.isGood()) if hasattr(value, "isGood") else False}
		if isinstance(value, Number):
			return {"code": int(value.longValue()), "name": unicode(value), "good": False}
		return {"code": None, "name": unicode(value), "good": False}

	try:
		if not isinstance(paths, (list, tuple, List)) or len(paths) == 0:
			return toolError("invalid_argument", "paths must be a non-empty array of historical paths.")
		if len(paths) > 50:
			return toolError("limit_exceeded", "paths exceeds the hard limit of 50.")
		normalizedPaths = []
		for path in paths:
			if not isinstance(path, basestring) or not path.strip():
				return toolError("invalid_argument", "Every historical path must be a non-empty string.")
			normalizedPaths.append(path.strip())
		if sampleCount is None:
			sampleCount = 100
		if isinstance(sampleCount, bool) or not isinstance(sampleCount, (int, long)) or sampleCount < 1:
			return toolError("invalid_argument", "sampleCount must be a positive integer. Natural (0) and On-Change (-1) retrieval are not public.")
		if sampleCount > 25000 or len(normalizedPaths) * int(sampleCount) > 25000:
			return toolError("limit_exceeded", "paths multiplied by sampleCount exceeds the 25,000-point hard limit.")
		now = Date()
		end = parseTime(endTime, "endTime", now)
		start = parseTime(startTime, "startTime", Date(end.getTime() - 86400000))
		if start.getTime() > end.getTime():
			return toolError("invalid_argument", "startTime must be before or equal to endTime.")
		if end.getTime() - start.getTime() > 604800000:
			return toolError("limit_exceeded", "Historian series range exceeds the 7-day hard limit; split the query into smaller windows.")
		data = system.historian.queryRawPoints(paths=normalizedPaths, startTime=start, endTime=end, returnFormat="TALL", returnSize=int(sampleCount))
		columnIndex = {}
		for index in range(data.getColumnCount()):
			columnIndex[unicode(data.getColumnName(index)).lower()] = index
		required = ("path", "value", "quality", "timestamp")
		for name in required:
			if name not in columnIndex:
				return toolError("schema_mismatch", "Historian TALL result omitted required column: " + name)
		if data.getRowCount() > len(normalizedPaths) * int(sampleCount):
			return toolError("limit_exceeded", "Historian returned more points than the declared paths x sampleCount bound.")
		points = []
		for row in range(data.getRowCount()):
			points.append({
				"path": unicode(data.getValueAt(row, columnIndex["path"])),
				"timestamp": jsonValue(data.getValueAt(row, columnIndex["timestamp"])),
				"value": jsonValue(data.getValueAt(row, columnIndex["value"])),
				"quality": qualityValue(data.getValueAt(row, columnIndex["quality"]))
			})
		domain = encodeNulls({
			"startTime": unicode(start.toInstant().toString()),
			"endTime": unicode(end.toInstant().toString()),
			"sampleCount": int(sampleCount),
			"points": points,
			"summary": {"paths": len(normalizedPaths), "returnedPoints": len(points), "hardPointLimit": 25000},
			"meta": {"correlationId": correlationId}
		})
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > 262144:
			return toolError("limit_exceeded", "Structured output exceeds the 256 KiB default limit; reduce paths, sampleCount, or time range.")
		return {"structuredContent": domain}
	except ValueError as exc:
		return toolError("invalid_argument", unicode(exc))
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " historian_query_series failed: " + unicode(exc))
		return toolError("upstream_error", "The Historian series query could not be completed.")
