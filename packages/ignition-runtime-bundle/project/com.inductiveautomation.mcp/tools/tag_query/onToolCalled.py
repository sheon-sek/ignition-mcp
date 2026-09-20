def onToolCalled(builder, provider, pathPattern, namePattern, tagType, valueSource, includeUdtMembers, returnProperties, maxResults, continuation):
	from java.lang import Boolean, Number, Exception as JavaException
	from java.util import UUID, Date, Map, List
	import math
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.TagQuery")

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

	def nativeItems(value):
		if isinstance(value, dict):
			return value.items()
		if isinstance(value, Map):
			return [(entry.getKey(), entry.getValue()) for entry in value.entrySet()]
		return []

	def optionalText(value, name):
		if value is None:
			return None
		if not isinstance(value, basestring):
			raise ValueError(name + " must be a string when provided.")
		value = value.strip()
		return value if value else None

	try:
		if maxResults is None:
			maxResults = 100
		if isinstance(maxResults, bool) or not isinstance(maxResults, (int, long)) or maxResults < 1 or maxResults > 500:
			return toolError("invalid_argument", "maxResults must be an integer from 1 to 500.")
		continuation = optionalText(continuation, "continuation")
		if continuation is not None:
			result = system.tag.query(limit=int(maxResults), continuation=continuation)
		else:
			provider = optionalText(provider, "provider")
			if provider is None or any(char in provider for char in "[]/"):
				return toolError("invalid_argument", "provider must be a non-empty Tag Provider name without brackets or slashes.")
			pathPattern = optionalText(pathPattern, "pathPattern")
			namePattern = optionalText(namePattern, "namePattern")
			tagType = optionalText(tagType, "tagType")
			valueSource = optionalText(valueSource, "valueSource")
			if tagType is not None and tagType not in ("AtomicTag", "Folder", "UdtInstance"):
				return toolError("invalid_argument", "Unsupported tagType.")
			if valueSource is not None and valueSource not in ("memory", "opc", "expr", "db", "reference", "derived"):
				return toolError("invalid_argument", "Unsupported valueSource.")
			if includeUdtMembers is None:
				includeUdtMembers = True
			if not isinstance(includeUdtMembers, bool):
				return toolError("invalid_argument", "includeUdtMembers must be boolean.")
			if returnProperties is None:
				returnProperties = ["path", "tagType"]
			if not isinstance(returnProperties, (list, tuple, List)):
				return toolError("invalid_argument", "returnProperties must be an array.")
			allowedProperties = ("path", "name", "tagType", "dataType", "valueSource", "typeId", "quality")
			properties = []
			for prop in returnProperties:
				if not isinstance(prop, basestring) or prop not in allowedProperties:
					return toolError("invalid_argument", "returnProperties contains an unsupported property.")
				if prop not in properties:
					properties.append(prop)
			if "path" not in properties:
				properties.insert(0, "path")
			query = {"options": {"includeUdtMembers": bool(includeUdtMembers), "includeUdtDefinitions": False}, "condition": {}, "returnProperties": properties}
			if pathPattern is not None:
				query["condition"]["path"] = pathPattern
			if tagType is not None:
				query["condition"]["tagType"] = tagType
			if valueSource is not None:
				query["condition"]["valueSource"] = valueSource
			if namePattern is not None:
				query["condition"]["properties"] = {"op": "And", "conditions": [{"prop": "name", "comp": "Like", "value": namePattern}]}
			result = system.tag.query(provider, query, int(maxResults))
		items = []
		for native in result:
			values = dict((unicode(key), jsonValue(value)) for key, value in nativeItems(native))
			path = values.get("path")
			if path is None:
				return toolError("schema_mismatch", "Tag query result omitted path.")
			pathText = unicode(path)
			if "/_types_/" in pathText or pathText.endswith("]_types_") or pathText.startswith("_types_/"):
				continue
			if len(items) >= maxResults:
				return toolError("limit_exceeded", "Tag query exceeded the requested result limit; continue with the returned cursor or narrow the query.")
			items.append({"path": pathText, "properties": values})
		nextCursor = result.getContinuationPoint() if hasattr(result, "getContinuationPoint") else getattr(result, "continuationPoint", None)
		domain = {"items": items, "continuation": nextCursor, "summary": {"returned": len(items), "limit": int(maxResults), "hasMore": nextCursor is not None}, "meta": {"correlationId": correlationId}}
		domain = encodeNulls(domain)
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > 262144:
			return toolError("limit_exceeded", "Structured output exceeds the 256 KiB default limit; narrow the query or request fewer properties.")
		return {"structuredContent": domain}
	except ValueError as exc:
		return toolError("invalid_argument", unicode(exc))
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " tag_query failed: " + unicode(exc))
		return toolError("upstream_error", "The Tag query operation could not be completed.")
