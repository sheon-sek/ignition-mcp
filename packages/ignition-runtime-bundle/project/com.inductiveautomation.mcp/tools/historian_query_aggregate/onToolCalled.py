def onToolCalled(builder, paths, startTime, endTime, aggregates):
	from java.lang import Boolean, Number, Exception as JavaException
	from java.util import UUID, Date, List
	from java.time import Instant
	import math
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.HistorianQueryAggregate")

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

	try:
		if not isinstance(paths, (list, tuple, List)) or len(paths) == 0:
			return toolError("invalid_argument", "paths must be a non-empty array of historical paths.")
		if len(paths) > 100:
			return toolError("limit_exceeded", "paths exceeds the hard limit of 100.")
		normalizedPaths = []
		for path in paths:
			if not isinstance(path, basestring) or not path.strip():
				return toolError("invalid_argument", "Every historical path must be a non-empty string.")
			path = path.strip()
			if len(path) > 2048:
				return toolError("limit_exceeded", "Historical paths must not exceed 2048 characters each.")
			normalizedPaths.append(path)
		allowed = ("Average", "SimpleAverage", "Sum", "Minimum", "Maximum", "MinMax", "LastValue", "Range", "Count", "CountOn", "CountOff", "DurationOn", "DurationOff", "Variance", "StdDev", "PctGood", "PctBad")
		if aggregates is None:
			aggregates = ["Average"]
		if not isinstance(aggregates, (list, tuple, List)) or len(aggregates) == 0:
			return toolError("invalid_argument", "aggregates must be a non-empty array.")
		if len(aggregates) > len(allowed):
			return toolError("limit_exceeded", "aggregates must contain at most " + unicode(len(allowed)) + " items before duplicate removal.")
		normalizedAggregates = []
		for aggregate in aggregates:
			if not isinstance(aggregate, basestring) or aggregate not in allowed:
				return toolError("invalid_argument", "aggregates contains an unsupported calculation.")
			if aggregate not in normalizedAggregates:
				normalizedAggregates.append(aggregate)
		if len(normalizedPaths) * len(normalizedAggregates) > 25000:
			return toolError("limit_exceeded", "paths multiplied by requested aggregate calculations exceeds the 25,000-calculation hard limit.")
		now = Date()
		end = parseTime(endTime, "endTime", now)
		start = parseTime(startTime, "startTime", Date(end.getTime() - 604800000))
		if start.getTime() > end.getTime():
			return toolError("invalid_argument", "startTime must be before or equal to endTime.")
		if end.getTime() - start.getTime() > 31622400000:
			return toolError("limit_exceeded", "Historian aggregate range exceeds the 366-day hard limit; split the query into smaller windows.")
		data = system.historian.queryAggregatedPoints(paths=normalizedPaths, startTime=start, endTime=end, aggregates=normalizedAggregates, returnFormat="CALCULATION")
		if data.getColumnCount() < 2:
			return toolError("schema_mismatch", "Historian aggregate result does not contain calculation columns.")
		if data.getRowCount() > len(normalizedPaths):
			return toolError("schema_mismatch", "Historian aggregate returned more rows than requested paths.")
		items = []
		for row in range(data.getRowCount()):
			calculations = {}
			for column in range(1, data.getColumnCount()):
				calculations[unicode(data.getColumnName(column))] = jsonValue(data.getValueAt(row, column))
			items.append({"path": unicode(data.getValueAt(row, 0)), "calculations": calculations})
		domain = encodeNulls({
			"startTime": unicode(start.toInstant().toString()),
			"endTime": unicode(end.toInstant().toString()),
			"requestedAggregates": normalizedAggregates,
			"items": items,
			"summary": {"paths": len(normalizedPaths), "returned": len(items), "calculationBudget": len(normalizedPaths) * len(normalizedAggregates)},
			"meta": {"correlationId": correlationId}
		})
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > 262144:
			return toolError("limit_exceeded", "Structured output exceeds the 256 KiB default limit; reduce paths or aggregate calculations.")
		return {"structuredContent": domain}
	except ValueError as exc:
		return toolError("invalid_argument", unicode(exc))
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " historian_query_aggregate failed: " + unicode(exc))
		return toolError("upstream_error", "The Historian aggregate query could not be completed.")
