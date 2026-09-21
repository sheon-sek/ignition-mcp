def onToolCalled(builder, maxResults):
	from java.util import UUID, Date
	from java.lang import Exception as JavaException
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.AlarmShelvedList")

	def toolError(code, message):
		error = {"code": code, "message": message, "correlationId": correlationId}
		logger.warn("correlationId=" + correlationId + " code=" + code + " " + message)
		return {"content": builder.text(system.util.jsonEncode(error)), "isError": True}

	def encodeNulls(value):
		if value is None: return {"$ignition": "null"}
		if isinstance(value, dict): return dict((key, encodeNulls(child)) for key, child in value.items())
		if isinstance(value, (list, tuple)): return [encodeNulls(child) for child in value]
		return value

	def jsonValue(value):
		if value is None: return None
		if isinstance(value, Date): return unicode(value.toInstant().toString())
		return unicode(value)

	try:
		if maxResults is None: maxResults = 100
		if isinstance(maxResults, bool) or not isinstance(maxResults, (int, long)) or maxResults < 1 or maxResults > 500:
			return toolError("invalid_argument", "maxResults must be an integer from 1 to 500.")
		values = system.alarm.getShelvedPaths()
		if len(values) > maxResults:
			return toolError("limit_exceeded", "Shelved alarm count exceeds maxResults; increase the limit within the hard ceiling or reduce shelving scope operationally.")
		items = []
		for value in values:
			items.append({"path": unicode(value.getPath()), "user": jsonValue(value.getUser()), "expiration": jsonValue(value.getExpiration()), "expired": bool(value.isExpired())})
		domain = encodeNulls({"items": items, "summary": {"returned": len(items), "limit": int(maxResults)}, "meta": {"correlationId": correlationId}})
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > 262144:
			return toolError("limit_exceeded", "Structured output exceeds the 256 KiB default limit.")
		return {"structuredContent": domain}
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " alarm_shelved_list failed: " + unicode(exc))
		return toolError("upstream_error", "The shelved alarm list could not be retrieved.")
