def onToolCalled(builder, rootPath, nameFilters, recursive, maxResults, continuation):
	from java.util import UUID, List
	from java.lang import Exception as JavaException
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.HistorianBrowse")

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

	try:
		if not isinstance(rootPath, basestring) or not rootPath.strip():
			return toolError("invalid_argument", "rootPath must be a non-empty historical browse path.")
		rootPath = rootPath.strip()
		if len(rootPath) > 2048:
			return toolError("limit_exceeded", "rootPath must not exceed 2048 characters.")
		if nameFilters is None:
			nameFilters = []
		if not isinstance(nameFilters, (list, tuple, List)) or len(nameFilters) > 20:
			return toolError("invalid_argument", "nameFilters must be an array with at most 20 items.")
		filters = []
		for value in nameFilters:
			if not isinstance(value, basestring) or not value.strip():
				return toolError("invalid_argument", "nameFilters must contain non-empty strings.")
			value = value.strip()
			if len(value) > 256:
				return toolError("limit_exceeded", "nameFilters must not exceed 256 characters each.")
			filters.append(value)
		if recursive is None:
			recursive = False
		if not isinstance(recursive, bool):
			return toolError("invalid_argument", "recursive must be boolean.")
		if maxResults is None:
			maxResults = 100
		if isinstance(maxResults, bool) or not isinstance(maxResults, (int, long)) or maxResults < 1 or maxResults > 500:
			return toolError("invalid_argument", "maxResults must be an integer from 1 to 500.")
		if continuation is not None and not isinstance(continuation, basestring):
			return toolError("invalid_argument", "continuation must be a string when provided.")
		continuation = continuation.strip() if isinstance(continuation, basestring) else ""
		if len(continuation) > 8192:
			return toolError("limit_exceeded", "continuation must not exceed 8192 characters.")
		kwargs = {"rootPath": rootPath, "maxSize": int(maxResults), "recursive": bool(recursive)}
		if filters:
			kwargs["nameFilters"] = filters
		if continuation:
			kwargs["continuationPoint"] = continuation
		result = system.historian.browse(**kwargs)
		items = []
		for native in result.getResults():
			items.append({"path": unicode(native.getPath()), "type": unicode(native.getType()), "hasChildren": bool(native.hasChildren())})
		if len(items) > maxResults:
			return toolError("schema_mismatch", "Historian browse returned more rows than the requested native limit.")
		nextCursor = result.getContinuationPoint()
		domain = encodeNulls({
			"rootPath": rootPath,
			"recursive": bool(recursive),
			"items": items,
			"continuation": nextCursor,
			"summary": {"returned": len(items), "limit": int(maxResults), "hasMore": nextCursor is not None},
			"meta": {"correlationId": correlationId}
		})
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > 262144:
			return toolError("limit_exceeded", "Structured output exceeds the 256 KiB default limit; request a smaller page.")
		return {"structuredContent": domain}
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " historian_browse failed: " + unicode(exc))
		return toolError("upstream_error", "The Historian browse operation could not be completed.")
