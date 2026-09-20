def onToolCalled(builder, provider, typePath, maxResults):
	from java.lang import Boolean, Number, Exception as JavaException
	from java.util import UUID, Date, Map, List
	import math
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.UdtTypeGet")

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
		if isinstance(value, Map):
			return dict((unicode(entry.getKey()), jsonValue(entry.getValue())) for entry in value.entrySet())
		if isinstance(value, (list, tuple, List)):
			return [jsonValue(child) for child in value]
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

	def validProvider(value):
		return isinstance(value, basestring) and bool(value.strip()) and not any(char in value for char in "[]/")

	def validTypePath(value):
		if not isinstance(value, basestring) or not value.strip():
			return False
		value = value.strip().strip("/")
		segments = value.split("/")
		return all(segment not in ("", ".", "..", "_types_") and "[" not in segment and "]" not in segment for segment in segments)

	try:
		if not validProvider(provider):
			return toolError("invalid_argument", "provider must be a non-empty Tag Provider name without brackets or slashes.")
		if not validTypePath(typePath):
			return toolError("invalid_argument", "typePath must be a provider-relative UDT type path and must not contain the internal _types_ namespace.")
		provider = provider.strip()
		typePath = typePath.strip().strip("/")
		if maxResults is None:
			maxResults = 200
		if isinstance(maxResults, bool) or not isinstance(maxResults, (int, long)) or maxResults < 1 or maxResults > 200:
			return toolError("invalid_argument", "maxResults must be an integer from 1 to 200.")
		nativeRoot = "[" + provider + "]_types_"
		parts = typePath.split("/")
		parentPath = "/".join(parts[:-1])
		leafName = parts[-1]
		browseRoot = nativeRoot + ("/" + parentPath if parentPath else "")
		nativePath = nativeRoot + "/" + typePath
		probe = system.tag.browse(browseRoot, {"recursive": False, "name": leafName, "tagType": "UdtType", "maxResults": 2})
		found = False
		for native in probe.getResults():
			if unicode(native["fullPath"]) == nativePath:
				found = True
				break
		if not found:
			return toolError("not_found", "The requested UDT type was not found.")
		configuration = jsonValue(system.tag.getConfiguration(nativePath, True))
		if not configuration:
			return toolError("schema_mismatch", "The UDT type exists but returned no configuration.")
		count = countNodes(configuration)
		if count > maxResults:
			return toolError("limit_exceeded", "UDT definition exceeds maxResults; reduce definition breadth before retrieving it through MCP.")
		domain = {"provider": provider, "typePath": typePath, "definition": configuration[0], "summary": {"returned": count, "limit": int(maxResults)}, "meta": {"correlationId": correlationId}}
		domain = encodeNulls(domain)
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > 262144:
			return toolError("limit_exceeded", "Structured output exceeds the 256 KiB default limit.")
		return {"structuredContent": domain}
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " udt_type_get failed: " + unicode(exc))
		return toolError("upstream_error", "The UDT type definition could not be retrieved.")
