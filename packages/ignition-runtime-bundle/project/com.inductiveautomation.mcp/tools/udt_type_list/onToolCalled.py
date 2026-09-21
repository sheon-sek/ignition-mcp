def onToolCalled(builder, provider, maxResults):
	from java.util import UUID
	from java.lang import Exception as JavaException
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.UdtTypeList")

	def toolError(code, message):
		error = {"code": code, "message": message, "correlationId": correlationId}
		logger.warn("correlationId=" + correlationId + " code=" + code + " " + message)
		return {"content": builder.text(system.util.jsonEncode(error)), "isError": True}

	def validProvider(value):
		return isinstance(value, basestring) and bool(value.strip()) and not any(char in value for char in "[]/")

	try:
		if not validProvider(provider):
			return toolError("invalid_argument", "provider must be a non-empty Tag Provider name without brackets or slashes.")
		provider = provider.strip()
		if maxResults is None:
			maxResults = 100
		if isinstance(maxResults, bool) or not isinstance(maxResults, (int, long)) or maxResults < 1 or maxResults > 500:
			return toolError("invalid_argument", "maxResults must be an integer from 1 to 500.")
		root = "[" + provider + "]_types_"
		result = system.tag.browse(root, {"recursive": True, "tagType": "UdtType", "maxResults": int(maxResults) + 1})
		items = []
		prefix = root + "/"
		for native in result.getResults():
			fullPath = unicode(native["fullPath"])
			if not fullPath.startswith(prefix):
				return toolError("schema_mismatch", "UDT browse returned a path outside the provider definition namespace.")
			typePath = fullPath[len(prefix):]
			if not typePath or "_types_" in [segment for segment in typePath.split("/") if segment]:
				continue
			items.append({"provider": provider, "typePath": typePath, "name": unicode(native["name"])})
		if len(items) > maxResults or result.getReturnedSize() < result.getTotalAvailableSize():
			return toolError("limit_exceeded", "UDT type inventory exceeds maxResults; narrow the provider design or increase the limit within the hard ceiling.")
		domain = {"items": items, "summary": {"returned": len(items), "limit": int(maxResults)}, "meta": {"correlationId": correlationId}}
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > 262144:
			return toolError("limit_exceeded", "Structured output exceeds the 256 KiB default limit.")
		return {"structuredContent": domain}
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " udt_type_list failed: " + unicode(exc))
		return toolError("upstream_error", "The UDT type inventory could not be retrieved.")
