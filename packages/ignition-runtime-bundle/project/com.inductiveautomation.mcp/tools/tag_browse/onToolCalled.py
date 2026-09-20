def onToolCalled(builder, path, recursive, maxResults):
	from java.util import UUID
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.TagBrowse")

	def toolError(code, message):
		return {"content": builder.text(code + ": " + message + "; correlationId=" + correlationId), "isError": True}

	def validAbsolutePath(value):
		if not isinstance(value, basestring) or not value.strip():
			return False
		value = value.strip()
		if not value.startswith("["):
			return False
		closing = value.find("]")
		if closing <= 1:
			return False
		if value.startswith("[.]") or value.startswith("[~]") or value.startswith("[]"):
			return False
		return True

	try:
		if not validAbsolutePath(path):
			return toolError("invalid_argument", "path must be an absolute provider-qualified Tag path.")
		path = path.strip()
		body = path[path.find("]") + 1:]
		segments = [segment for segment in body.split("/") if segment]
		if "_types_" in segments:
			return toolError("invalid_argument", "UDT definition namespace is not exposed by tag_browse; use UDT tools.")
		if recursive is None:
			recursive = False
		if not isinstance(recursive, bool):
			return toolError("invalid_argument", "recursive must be boolean.")
		if maxResults is None:
			maxResults = 100
		if isinstance(maxResults, bool) or not isinstance(maxResults, (int, long)) or maxResults < 1 or maxResults > 500:
			return toolError("invalid_argument", "maxResults must be an integer from 1 to 500.")
		result = system.tag.browse(path, {"recursive": recursive, "maxResults": int(maxResults) + 1})
		nodes = []
		for native in result.getResults():
			name = unicode(native["name"])
			fullPath = unicode(native["fullPath"])
			if name == "_types_" or "/_types_/" in fullPath or fullPath.endswith("]_types_"):
				continue
			node = {"path": fullPath, "name": name, "tagType": unicode(native["tagType"]), "hasChildren": bool(native["hasChildren"])}
			if "dataType" in native:
				node["dataType"] = unicode(native["dataType"])
			if "valueSource" in native:
				node["valueSource"] = unicode(native["valueSource"])
			if "typeId" in native and native["typeId"] is not None:
				node["typeId"] = unicode(native["typeId"])
			nodes.append(node)
		if len(nodes) > maxResults or result.getReturnedSize() < result.getTotalAvailableSize():
			return toolError("limit_exceeded", "Browse exceeds the requested bounded result limit; narrow path/filter scope.")
		domain = {"path": path, "recursive": bool(recursive), "nodes": nodes, "summary": {"returned": len(nodes), "limit": int(maxResults)}, "meta": {"correlationId": correlationId}}
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > 1048576:
			return toolError("limit_exceeded", "Structured output exceeds the 1 MiB hard limit.")
		return {"structuredContent": domain}
	except Exception as exc:
		logger.error("correlationId=" + correlationId + " tag_browse failed: " + unicode(exc))
		return toolError("internal_error", "The Tag browse operation could not be completed.")
