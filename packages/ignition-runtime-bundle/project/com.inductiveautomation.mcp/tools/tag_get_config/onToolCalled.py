def onToolCalled(builder, path, recursive, overridesOnly, maxResults):
	from java.lang import Boolean, Number, Enum, Exception as JavaException
	from java.util import UUID, Date, Map, List
	import math
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.TagGetConfig")

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
		if hasattr(value, "iteritems"):
			return dict((unicode(key), jsonValue(child)) for key, child in value.iteritems())
		if isinstance(value, Map):
			return dict((unicode(entry.getKey()), jsonValue(entry.getValue())) for entry in value.entrySet())
		if isinstance(value, (list, tuple, List)):
			return [jsonValue(child) for child in value]
		if isinstance(value, Enum):
			return unicode(value)
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

	UDT_NAMESPACE = "_types_"

	def validPath(value):
		if not isinstance(value, basestring) or not value.strip():
			return False
		value = value.strip()
		if not value.startswith("["):
			return False
		closing = value.find("]")
		if closing <= 1 or value.startswith("[.]") or value.startswith("[~]") or value.startswith("[]"):
			return False
		return True

	def isUdtDefinitionPath(value):
		# D30 6 names `[provider]_types_/...`, so the grammar is positional: only the
		# first post-provider segment selects the definition namespace. A folder that
		# merely happens to be called `_types_` deeper in the path is an ordinary
		# path, and reading it recursively stays an ordinary read.
		closing = value.find("]")
		if closing <= 0:
			return False
		segments = [segment for segment in value[closing + 1:].split("/") if segment]
		return len(segments) > 0 and segments[0] == UDT_NAMESPACE

	# D30 2: the Tag config fingerprint. Repo-defined, versioned `tcf1` and
	# deterministic: SHA-256 over the canonical JSON text of the D28-encoded
	# configuration this read returns. The rule and its golden vectors are the
	# shared contract contracts/shared/tag-config-fingerprint.json, and tag_update
	# recomputes it over its own default read of one exact target path.
	FINGERPRINT_PREFIX = "tcf1:"

	def quoteJsonString(value):
		# Only " and \ are escaped, and a control character is always the
		# six-character \u00xx form, so the Python copy of this rule agrees with
		# this one byte for byte.
		parts = ['"']
		for character in value:
			if character == '"':
				parts.append('\\"')
			elif character == "\\":
				parts.append("\\\\")
			elif character < " ":
				parts.append("\\u%04x" % ord(character))
			else:
				parts.append(character)
		parts.append('"')
		return "".join(parts)

	def canonicalJson(value):
		# Object keys sort by code point; an integer keeps its exact decimal
		# form and a float its shortest round-trip form.
		if value is None:
			return "null"
		if isinstance(value, bool):
			return "true" if value else "false"
		if isinstance(value, basestring):
			return quoteJsonString(value)
		if isinstance(value, (int, long)):
			return unicode(value)
		if isinstance(value, float):
			return repr(value)
		if isinstance(value, (list, tuple)):
			return "[" + ",".join([canonicalJson(child) for child in value]) + "]"
		if isinstance(value, dict):
			keys = sorted(value.keys())
			return "{" + ",".join([quoteJsonString(unicode(key)) + ":" + canonicalJson(value[key]) for key in keys]) + "}"
		raise TypeError("Unsupported canonical JSON value: " + unicode(type(value)))

	def tagConfigFingerprint(value):
		from java.security import MessageDigest
		digest = MessageDigest.getInstance("SHA-256")
		digest.update(canonicalJson(value).encode("utf-8"))
		return FINGERPRINT_PREFIX + digest.digest().tostring().encode("hex")

	stage = "validation"
	try:
		if not validPath(path):
			return toolError("invalid_argument", "path must be an absolute provider-qualified Tag path.")
		path = path.strip()
		if recursive is None:
			recursive = False
		if overridesOnly is None:
			overridesOnly = False
		if not isinstance(recursive, bool) or not isinstance(overridesOnly, bool):
			return toolError("invalid_argument", "recursive and overridesOnly must be boolean.")
		if isUdtDefinitionPath(path) and recursive:
			# D30 6: an exact definition read is what publishes the Tag config
			# fingerprint a Tag CONFIG Mutation compares, so it is allowed; the
			# subtree view of the definition namespace stays udt_type_get's.
			return toolError("invalid_argument", "a UDT definition is read one exact definition at a time: set recursive=false, or use udt_type_get for the subtree view.")
		if maxResults is None:
			maxResults = 50
		if isinstance(maxResults, bool) or not isinstance(maxResults, (int, long)) or maxResults < 1 or maxResults > 200:
			return toolError("invalid_argument", "maxResults must be an integer from 1 to 200.")
		stage = "native_read"
		nativeConfiguration = system.tag.getConfiguration(path, bool(recursive), bool(overridesOnly))
		stage = "result_normalization"
		configuration = jsonValue(nativeConfiguration)
		stage = "fingerprint"
		fingerprint = tagConfigFingerprint(encodeNulls(configuration))
		stage = "result_count"
		count = countNodes(configuration)
		if count > maxResults:
			return toolError("limit_exceeded", "Tag configuration exceeds maxResults; use a narrower path or disable recursive retrieval.")
		domain = {"path": path, "recursive": bool(recursive), "overridesOnly": bool(overridesOnly), "fingerprint": fingerprint, "configuration": configuration, "summary": {"returned": count, "limit": int(maxResults)}, "meta": {"correlationId": correlationId}}
		domain = encodeNulls(domain)
		stage = "serialization"
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > 262144:
			return toolError("limit_exceeded", "Structured output exceeds the 256 KiB default limit; use a narrower path or disable recursive retrieval.")
		return {"structuredContent": domain}
	except (Exception, JavaException) as exc:
		exceptionType = unicode(type(exc))
		logger.error("correlationId=" + correlationId + " stage=" + stage + " exceptionType=" + exceptionType + " tag_get_config failed: " + unicode(exc))
		return toolError("upstream_error", "The Tag configuration read could not be completed.")
