def onToolCalled(builder, items):
	from java.lang import Boolean, Number, Enum, Exception as JavaException
	from java.util import UUID, Date, Map, List
	import math
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.TagUpdate")

	# D30 1: the Runtime Target Policy is a deployment-owned document outside the
	# bundle. Ticket #6 characterized its storage and its bounded read: the small
	# companion length Tag is read first so an over-cap document is never
	# materialized, because a Tag value has no native size limit.
	POLICY_PROVIDER = "IgnitionMCPPolicy"
	POLICY_LENGTH_PATH = "[IgnitionMCPPolicy]RuntimeTargetPolicyLength"
	POLICY_PATH = "[IgnitionMCPPolicy]RuntimeTargetPolicy"
	POLICY_MAX_BYTES = 32768
	POLICY_READ_TIMEOUT_MS = 5000
	# D30 1: the Runtime plane cannot write the policy, and that is a product rule
	# because handler scope is not a security boundary (#6 evidence).
	RESERVED_PROVIDER = "IgnitionMCPPolicy"
	ALLOWLIST_KEY = "tag_update"
	WILDCARD = "*"
	# D30 6: a UDT definition lives in the provider's _types_ namespace and is
	# reachable only through an explicit _types_ allowlist entry; a bare * does not
	# cover it.
	UDT_NAMESPACE = "_types_"
	AUDIT_ACTION = "ignition-mcp.tag_update"
	AUDIT_MODES = ("best_effort", "required", "off")
	# D30 2: the Tag config fingerprint. Repo-defined, versioned `tcf1` and
	# deterministic: SHA-256 over the canonical JSON text of the D28-encoded
	# configuration the caller's tag_get_config read published. The rule and its
	# golden vectors are contracts/shared/tag-config-fingerprint.json.
	FINGERPRINT_PREFIX = "tcf1:"
	FINGERPRINT_HEX = "0123456789abcdef"
	FINGERPRINT_LENGTH = 69
	# Ticket #10 live evidence: a Gateway answers system.tag.getConfiguration for a
	# path that is not there with a synthesized default node, so a CONFIG Mutation's
	# existence check is a separate, dedicated primitive. `system.tag.exists` is it.
	# The handler fixes the Gateway's collision policy: MergeOverwrite is what makes
	# this a merge-update, and Preflight has already read every target, so a merge
	# cannot create one that the read found missing.
	COLLISION_POLICY = "MergeOverwrite"
	# D10 budgets: the project safe default is 20 targets, the deployment may raise
	# it through the Runtime Target Policy up to the 100-target hard ceiling, and
	# every request is bounded by path, configuration-string, array, depth, per-item
	# byte and one finite aggregate byte ceiling.
	DEFAULT_MAX_ITEMS = 20
	HARD_MAX_ITEMS = 100
	POLICY_MAX_ITEMS_FIELD = "tagUpdateMaxItems"
	PATH_MAX_BYTES = 2048
	CONFIG_STRING_MAX_BYTES = 16384
	CONFIG_ARRAY_MAX_ELEMENTS = 1000
	CONFIG_MAX_DEPTH = 8
	CONFIG_MAX_BYTES = 32768
	INPUT_MAX_BYTES = 65536
	NUMERIC_INPUT_BYTES = 32
	# D10 output: the Observed state carries its own budget, so a configuration it
	# cannot return never becomes the reason a completed change's outcomes
	# disappear, and a Native diagnostic never runs unbounded either. A
	# configuration is read back non-recursively, so its depth is bounded by the
	# gateway's own node shape; the ceiling is four times the input depth ceiling
	# and exists so the walk over the raw native value can never recurse without a
	# bound, not to measure a legitimate read-back.
	OBSERVED_CONFIGURATION_MAX_BYTES = 16384
	OBSERVED_CONFIGURATION_MAX_DEPTH = 32
	OBSERVED_STATE_MAX_BYTES = 65536
	DIAGNOSTIC_MAX_BYTES = 256
	OUTPUT_MAX_BYTES = 262144
	# D10: an over-budget refusal states the requested amount, the applicable
	# limit, and how to split or reduce the request. That last part is a stable
	# `advice` detail field and the same sentence is repeated in the message, so a
	# caller reading only the message still learns what to change.
	ITEMS_HARD_ADVICE = "Split the batch into several calls of at most " + unicode(HARD_MAX_ITEMS) + " targets; the hard ceiling cannot be raised."
	PATH_ADVICE = "Shorten the target path to at most " + unicode(PATH_MAX_BYTES) + " UTF-8 bytes, or update the Tag through a shorter parent path."
	INPUT_BYTES_ADVICE = "Split the batch across several calls so each request stays inside the " + unicode(INPUT_MAX_BYTES) + "-byte input budget."
	CONFIG_ADVICE = {
		"configStringOverLimit": "Shorten the string value to at most " + unicode(CONFIG_STRING_MAX_BYTES) + " UTF-8 bytes, or move the text to a separate call.",
		"configArrayOverLimit": "Prune the array to at most " + unicode(CONFIG_ARRAY_MAX_ELEMENTS) + " elements, or split the configuration across several calls.",
		"configOverDepth": "Flatten the configuration to at most " + unicode(CONFIG_MAX_DEPTH) + " levels, and update a deeper child through its own target path.",
		"configOverByteBudget": "Reduce the configuration to at most " + unicode(CONFIG_MAX_BYTES) + " bytes, or split it across several calls.",
	}
	OBSERVED_DEPTH_ADVICE = "The item's Native outcome above is unaffected; a configuration nested deeper than " + unicode(OBSERVED_CONFIGURATION_MAX_DEPTH) + " levels is not returned as Observed state, so re-read this target with tag_get_config or update a shallower target."
	OBSERVED_BYTES_ADVICE = "The item's Native outcome above is unaffected; re-read this target with tag_get_config, or update fewer targets per call so the Observed state fits."
	OBSERVED_STATE_ADVICE = "The item's Native outcome above is unaffected; update fewer targets per call and re-read the ones you need with tag_get_config."
	OUTPUT_ADVICE = "Update fewer targets per call, use narrower configurations, and re-read the changed targets with tag_get_config."

	def toolError(code, message, details):
		error = {"code": code, "message": message, "correlationId": correlationId}
		if details is not None:
			error["details"] = details
		logger.warn("correlationId=" + correlationId + " code=" + code + " " + message)
		return {"content": builder.text(system.util.jsonEncode(error)), "isError": True}

	def text(value):
		return "" if value is None else unicode(value)

	def boundedText(value, limit):
		rendered = text(value)
		if len(rendered) > limit:
			return rendered[:limit] + "..."
		return rendered

	def encodeNulls(value):
		# D28 ignition-null-v1: escape reserved-key objects to avoid collisions.
		if value is None:
			return {"$ignition": "null"}
		if isinstance(value, dict):
			if "$ignition" in value:
				return {"$ignition": "object", "entries": [[key, encodeNulls(child)] for key, child in sorted(value.items())]}
			return dict((key, encodeNulls(child)) for key, child in value.items())
		if isinstance(value, (list, tuple)):
			return [encodeNulls(child) for child in value]
		return value

	def optionalText(value):
		return None if value is None else unicode(value)

	def qualityIsGood(value):
		if value is None:
			return False
		if hasattr(value, "isGood"):
			return bool(value.isGood())
		return unicode(value).find("Good") == 0

	def quality(value):
		if value is None:
			raise TypeError("Native result has no QualityCode")
		name = value.getName() if hasattr(value, "getName") else unicode(value)
		level = value.getLevel() if hasattr(value, "getLevel") else unicode(value)
		diagnostic = value.getDiagnosticMessage() if hasattr(value, "getDiagnosticMessage") else None
		# D10: a Gateway diagnostic is free text, so it is bounded like any other
		# part of the structured result.
		return {"code": int(value.getCode()), "name": boundedText(name, DIAGNOSTIC_MAX_BYTES), "level": boundedText(level, DIAGNOSTIC_MAX_BYTES), "good": qualityIsGood(value), "diagnosticMessage": optionalText(boundedText(diagnostic, DIAGNOSTIC_MAX_BYTES) if diagnostic is not None else None)}

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
		# Object keys sort by code point; an integer keeps its exact decimal form
		# and a float its shortest round-trip form.
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

	def tagConfigFingerprint(encodedConfiguration):
		from java.security import MessageDigest
		digest = MessageDigest.getInstance("SHA-256")
		digest.update(canonicalJson(encodedConfiguration).encode("utf-8"))
		return FINGERPRINT_PREFIX + digest.digest().tostring().encode("hex")

	def utf8Bytes(value):
		return len(text(value).encode("utf-8"))

	def configSize(value, depth):
		# (bounded size, problem) with problem = (reason, requested, limit). The walk
		# stops at the first ceiling it crosses, so an over-budget request is never
		# measured in full.
		if depth > CONFIG_MAX_DEPTH:
			return (0, ("configOverDepth", depth, CONFIG_MAX_DEPTH))
		if value is None or isinstance(value, bool) or isinstance(value, Boolean):
			return (5, None)
		if isinstance(value, basestring):
			size = utf8Bytes(value)
			if size > CONFIG_STRING_MAX_BYTES:
				return (size, ("configStringOverLimit", size, CONFIG_STRING_MAX_BYTES))
			return (size, None)
		if isinstance(value, (int, long, float, Number)):
			return (NUMERIC_INPUT_BYTES, None)
		if isinstance(value, (dict, Map)):
			keys = value.keys() if isinstance(value, dict) else [entry.getKey() for entry in value.entrySet()]
			total = 2
			for key in keys:
				childSize, problem = configSize(value.get(key), depth + 1)
				if problem is not None:
					return (0, problem)
				total += utf8Bytes(unicode(key)) + childSize
				if total > CONFIG_MAX_BYTES:
					return (total, ("configOverByteBudget", total, CONFIG_MAX_BYTES))
			return (total, None)
		if isinstance(value, (list, tuple, List)):
			if len(value) > CONFIG_ARRAY_MAX_ELEMENTS:
				return (0, ("configArrayOverLimit", len(value), CONFIG_ARRAY_MAX_ELEMENTS))
			total = 2
			for child in value:
				childSize, problem = configSize(child, depth + 1)
				if problem is not None:
					return (0, problem)
				total += childSize
				if total > CONFIG_MAX_BYTES:
					return (total, ("configOverByteBudget", total, CONFIG_MAX_BYTES))
			return (total, None)
		return (64, None)

	def observedConfigurationSize(value, depth, limit):
		# D10: the Observed read-back is walked raw -- before any conversion -- with
		# a depth ceiling as well as a byte ceiling, so a deeply nested or very large
		# native value is refused here and never reaches the recursive jsonValue
		# below. Measuring what the Gateway returned must not itself materialize an
		# unbounded value. The walk stops at the first ceiling it crosses, so an
		# over-budget read-back is never measured in full. Returns (size, problem)
		# with problem = (reason, requested, limit, advice).
		if depth > OBSERVED_CONFIGURATION_MAX_DEPTH:
			return (0, ("observedConfigurationOverDepth", depth, OBSERVED_CONFIGURATION_MAX_DEPTH, OBSERVED_DEPTH_ADVICE))
		if value is None:
			return (4, None)
		if isinstance(value, basestring):
			return (utf8Bytes(value), None)
		if isinstance(value, (bool, Boolean)):
			return (5, None)
		if isinstance(value, (int, long, float, Number)):
			return (24, None)
		if isinstance(value, (list, tuple, List)):
			total = 2
			for child in value:
				childSize, problem = observedConfigurationSize(child, depth + 1, limit)
				if problem is not None:
					return (0, problem)
				total += childSize
				if total > limit:
					return (total, ("observedConfigurationOverBytes", total, limit, OBSERVED_BYTES_ADVICE))
			return (total, None)
		if isinstance(value, Map):
			total = 2
			for entry in value.entrySet():
				childSize, problem = observedConfigurationSize(entry.getValue(), depth + 1, limit)
				if problem is not None:
					return (0, problem)
				total += utf8Bytes(unicode(entry.getKey())) + childSize
				if total > limit:
					return (total, ("observedConfigurationOverBytes", total, limit, OBSERVED_BYTES_ADVICE))
			return (total, None)
		if isinstance(value, dict):
			total = 2
			for key in value:
				childSize, problem = observedConfigurationSize(value[key], depth + 1, limit)
				if problem is not None:
					return (0, problem)
				total += utf8Bytes(unicode(key)) + childSize
				if total > limit:
					return (total, ("observedConfigurationOverBytes", total, limit, OBSERVED_BYTES_ADVICE))
			return (total, None)
		return (64, None)

	def observedProblemMessage(problem):
		# The per-configuration half of the Observed-state budget. D10: the refusal
		# states the requested amount, the applicable limit and the reduction advice.
		reason, requested, limit, advice = problem
		if reason == "observedConfigurationOverDepth":
			head = "The Observed configuration is nested " + unicode(requested) + " levels deep, over the " + unicode(limit) + "-level Observed-state depth budget"
		elif reason == "observedStateOverByteBudget":
			head = "Returning this configuration would take the Observed state to " + unicode(requested) + " bytes, over the " + unicode(limit) + "-byte Observed-state budget"
		elif reason == "observedStateBudgetSpent":
			head = "The " + unicode(limit) + "-byte Observed-state budget is already spent, so this " + unicode(requested) + "-byte configuration is over it"
		else:
			head = "The Observed configuration is " + unicode(requested) + " bytes, over the " + unicode(limit) + "-byte Observed-state budget"
		return head + "; it was not returned. " + advice

	def observedError(path, code, message):
		return {"path": path, "status": "error", "error": {"code": code, "message": message, "correlationId": correlationId}}

	def omittedObserved(reason):
		return [observedError(paths[index], "schema_mismatch", reason) for index in range(len(paths))]

	OBSERVED_OMITTED_SERIALIZATION = "The Observed state was omitted because the structured result could not be serialized; the per-item outcomes above are complete."
	OBSERVED_OMITTED_CEILING = "The Observed state was omitted to keep the structured result inside the 256 KiB output ceiling; the per-item outcomes above are complete."

	def buildDomain(observedEntries, outcomeCounts):
		domain = {
			"items": results,
			"observed": observedEntries,
			"summary": {"requested": len(paths), "succeeded": outcomeCounts[0], "failed": outcomeCounts[1], "outcomeUnknown": outcomeCounts[2], "notExecuted": outcomeCounts[3], "auditMode": auditMode, "auditRecorded": auditRecorded},
			"meta": {"correlationId": correlationId},
		}
		return encodeNulls(domain)

	def encodeDomain(observedEntries, outcomeCounts):
		# The serializer is not allowed to decide a mutation's result: a failure
		# here is caught so the per-item Native outcomes still reach the caller.
		try:
			domain = buildDomain(observedEntries, outcomeCounts)
			return (domain, system.util.jsonEncode(domain))
		except (Exception, JavaException) as encodeExc:
			logger.warn("correlationId=" + correlationId + " structured result serialization failed: " + text(encodeExc))
			return (None, None)

	def validFingerprint(value):
		if not isinstance(value, basestring) or len(value) != FINGERPRINT_LENGTH:
			return False
		if not value.startswith(FINGERPRINT_PREFIX):
			return False
		for character in value[len(FINGERPRINT_PREFIX):]:
			if FINGERPRINT_HEX.find(character) < 0:
				return False
		return True

	def providerName(value):
		if not isinstance(value, basestring) or not value.startswith("["):
			return ""
		closing = value.find("]")
		if closing <= 1:
			return ""
		return value[1:closing]

	def targetSegments(value):
		# The segment list of a config path, provider bracket excluded.
		closing = value.find("]")
		if closing <= 0:
			return []
		return [segment for segment in value[closing + 1:].split("/") if segment]

	def validTargetPath(value):
		if not isinstance(value, basestring) or not value.strip():
			return False
		value = value.strip()
		if not value.startswith("["):
			return False
		closing = value.find("]")
		if closing <= 1 or value.startswith("[.]") or value.startswith("[~]") or value.startswith("[]"):
			return False
		body = value[closing + 1:]
		if body == "":
			return False
		if "." in body or "[" in body or "]" in body or "*" in body or "?" in body or ":" in body:
			return False
		segments = [segment for segment in body.split("/") if segment]
		if len(segments) != len(body.split("/")):
			return False
		return True

	def targetLeaf(value):
		segments = targetSegments(value)
		return segments[len(segments) - 1] if segments else ""

	def targetBase(value):
		index = value.rfind("/")
		if index <= value.find("]"):
			return value[0:value.find("]") + 1]
		return value[0:index]

	def isUdtDefinitionTarget(value):
		# D30 6 names `[provider]_types_/...`, so the grammar is positional: only the
		# first post-provider segment selects the definition namespace. A folder that
		# merely happens to be called `_types_` deeper in the path is an ordinary
		# target, matched by the ordinary allowlist.
		segments = targetSegments(value)
		return len(segments) > 0 and segments[0] == UDT_NAMESPACE

	def normalizeEntries(entries):
		# D30 1: allowlist entries are provider-qualified prefixes matched at
		# segment boundaries; * must be written explicitly.
		if not isinstance(entries, (list, tuple, List)):
			return None
		normalized = []
		for entry in entries:
			if not isinstance(entry, basestring):
				return None
			entry = entry.strip()
			if entry == WILDCARD:
				normalized.append(WILDCARD)
				continue
			if entry == "" or " " in entry or providerName(entry) == "" or not validTargetPath(entry):
				return None
			normalized.append(entry)
		return normalized

	def matchesEntry(path, entry):
		return path == entry or path.startswith(entry + "/")

	def matchesAllowlist(path, entries):
		for entry in entries:
			if entry == WILDCARD:
				return True
			if matchesEntry(path, entry):
				return True
		return False

	def matchesUdtAllowlist(path, entries):
		# D30 6: a UDT definition target needs an explicit _types_ entry; a bare *
		# does not cover it, and the entry itself has to name the _types_ segment the
		# same positional way the target does.
		for entry in entries:
			if entry == WILDCARD:
				continue
			if not isUdtDefinitionTarget(entry):
				continue
			if matchesEntry(path, entry):
				return True
		return False

	def hasKey(document, key):
		# An explicit JSON null must be refused, not read as "absent": the policy
		# document contract says a null-valued property is simply omitted.
		try:
			if hasattr(document, "has_key"):
				return bool(document.has_key(key))
			return key in document
		except (Exception, JavaException):
			return False

	def policyProblem(document):
		if not isinstance(document, (dict, Map)):
			return "policyMalformed"
		if document.get("schemaVersion") != 1:
			return "policySchemaVersion"
		allowlists = document.get("allowlists")
		if not isinstance(allowlists, (dict, Map)):
			return "policyAllowlists"
		for key in allowlists:
			# The document shape is validated for every key, but the *grammar* of
			# an entry belongs to the Tool that reads it: an Alarm source pattern
			# and a Tag path prefix are different languages. Each handler therefore
			# validates its own key strictly and the others only as strings.
			entries = allowlists.get(key)
			if not isinstance(entries, (list, tuple, List)):
				return "policyAllowlists"
			for entry in entries:
				if not isinstance(entry, basestring) or not entry.strip():
					return "policyAllowlists"
		own = allowlists.get(ALLOWLIST_KEY)
		if own is not None and normalizeEntries(own) is None:
			return "policyAllowlists"
		serviceIdentity = document.get("serviceIdentity")
		if not (isinstance(serviceIdentity, basestring) and serviceIdentity.strip()):
			return "policyServiceIdentity"
		if document.get("auditMode") not in AUDIT_MODES:
			return "policyAuditMode"
		if hasKey(document, "auditProfile"):
			auditProfile = document.get("auditProfile")
			if not (isinstance(auditProfile, basestring) and auditProfile.strip()):
				return "policyAuditProfile"
		if hasKey(document, POLICY_MAX_ITEMS_FIELD):
			# D10: the deployment may raise the 20-target default, never above the
			# 100-target hard ceiling.
			maxItems = document.get(POLICY_MAX_ITEMS_FIELD)
			if isinstance(maxItems, bool) or not isinstance(maxItems, (int, long)) or maxItems < 1 or maxItems > HARD_MAX_ITEMS:
				return "policyTagUpdateMaxItems"
		if hasKey(document, "alarmShelveMaxSeconds"):
			shelveCap = document.get("alarmShelveMaxSeconds")
			if isinstance(shelveCap, bool) or not isinstance(shelveCap, (int, long)) or shelveCap <= 0:
				return "policyAlarmShelveMaxSeconds"
		return None

	def readPolicy():
		# Two-step gated read (ticket #6): declared length first, then the document.
		lengthRead = system.tag.readBlocking([POLICY_LENGTH_PATH], POLICY_READ_TIMEOUT_MS)
		if lengthRead is None or len(lengthRead) != 1:
			return (None, "declaredLengthUnavailable")
		lengthItem = lengthRead[0]
		if not qualityIsGood(lengthItem.quality):
			return (None, "declaredLengthUnavailable")
		try:
			declared = long(lengthItem.value)
		except (Exception, JavaException):
			return (None, "declaredLengthInvalid")
		if declared <= 0:
			return (None, "declaredLengthInvalid")
		if declared > POLICY_MAX_BYTES:
			return (None, "declaredLengthOversize")
		policyRead = system.tag.readBlocking([POLICY_PATH], POLICY_READ_TIMEOUT_MS)
		if policyRead is None or len(policyRead) != 1:
			return (None, "policyUnavailable")
		policyItem = policyRead[0]
		if not qualityIsGood(policyItem.quality):
			return (None, "policyUnavailable")
		policyText = text(policyItem.value)
		if len(policyText.encode("utf-8")) != declared:
			return (None, "policyLengthMismatch")
		try:
			document = system.util.jsonDecode(policyText)
		except (Exception, JavaException):
			return (None, "policyMalformed")
		problem = policyProblem(document)
		if problem is not None:
			return (None, problem)
		return (document, None)

	def auditProfileAvailable(name):
		# D30 6: in required mode the handler checks the audit profile before it
		# executes, and never writes a probe row.
		try:
			resource = system.config.getResource(moduleId="ignition", typeId="audit-profile", name=name)
		except (Exception, JavaException) as exc:
			logger.warn("correlationId=" + correlationId + " audit profile check failed: " + text(exc))
			return False
		if resource is None:
			return False
		enabled = None
		if hasattr(resource, "enabled"):
			enabled = resource.enabled
		if enabled is not None and not bool(enabled):
			return False
		return True

	def auditWrite(phase, actionTarget, summary):
		# D18: off records nothing; best_effort and required log failures, and a
		# failed post-mutation write never turns a success into a failure.
		if auditMode == "off":
			return False
		try:
			system.util.audit(
				action=AUDIT_ACTION,
				actionTarget=boundedText(actionTarget, 512),
				actionValue=boundedText("tool=" + ALLOWLIST_KEY + " phase=" + phase + " correlationId=" + correlationId + " serviceIdentity=" + serviceIdentity + " " + summary, 1024),
				auditProfile=auditProfile,
				actor=serviceIdentity)
			return True
		except (Exception, JavaException) as exc:
			logger.warn("correlationId=" + correlationId + " audit phase=" + phase + " failed: " + text(exc))
			return False

	def configProblem(config, leaf):
		# D30 6 and the class split: a value write belongs to tag_write (CONTROL),
		# a child is its own target with its own fingerprint, and renaming is
		# tag_rename's job, which checks the new path against the allowlist.
		if not isinstance(config, (dict, Map)):
			return "configNotAnObject"
		keys = [text(key) for key in config.keys()]
		if not keys:
			return "configEmpty"
		if "value" in keys:
			return "configValueIsAControlWrite"
		if "tags" in keys:
			return "configNestedTagsNotAllowed"
		if "name" in keys and text(config.get("name")) != leaf:
			return "configNameChangeNotAllowed"
		return None

	def existenceProblem(value):
		# system.tag.exists answers a boolean; anything else is not an answer, and a
		# non-answer must not be read as "the target is there".
		if isinstance(value, bool):
			return None if value else "targetMissing"
		if isinstance(value, Boolean):
			return None if value.booleanValue() else "targetMissing"
		return "existenceCheckIndeterminate"

	def configurationFingerprint(path):
		# The configuration read alone is not an existence check: a Gateway answers a
		# path that is not there with a synthesized default node (the ticket #10 live
		# run recorded the same one on 8.3.8 and 8.3.9), so `system.tag.exists` decides.
		nativeConfiguration = system.tag.getConfiguration(path, False, False)
		configuration = jsonValue(nativeConfiguration)
		if not isinstance(configuration, (list, tuple, List)) or len(configuration) == 0:
			return (None, "configurationUnavailable")
		encoded = encodeNulls(configuration)
		return (tagConfigFingerprint(encoded), None)

	stage = "input_validation"
	dispatched = False
	try:
		if not isinstance(items, (list, tuple, List)) or len(items) == 0:
			return toolError("invalid_argument", "items must be a non-empty array of {path, expectedFingerprint, config} items.", {"reason": "itemsNotAnArray"})
		if len(items) > HARD_MAX_ITEMS:
			return toolError("limit_exceeded", "items is " + unicode(len(items)) + " targets, over the hard limit of " + unicode(HARD_MAX_ITEMS) + "; no item was executed. " + ITEMS_HARD_ADVICE, {"reason": "itemsOverHardLimit", "requested": len(items), "limit": HARD_MAX_ITEMS, "advice": ITEMS_HARD_ADVICE})
		paths = []
		fingerprints = []
		configurations = []
		inputProblems = []
		for index in range(len(items)):
			item = items[index]
			if not isinstance(item, dict):
				inputProblems.append({"index": index, "reason": "itemNotAnObject"})
				continue
			keys = [text(key) for key in item.keys()]
			pathValue = item.get("path")
			if "path" not in keys or "expectedFingerprint" not in keys or "config" not in keys or len(keys) != 3:
				inputProblems.append({"index": index, "path": boundedText(pathValue, 256), "reason": "itemKeysMustBePathFingerprintAndConfig"})
				continue
			if not validTargetPath(pathValue):
				inputProblems.append({"index": index, "path": boundedText(pathValue, 256), "reason": "pathNotAConfigPath"})
				continue
			path = unicode(pathValue).strip()
			expected = item.get("expectedFingerprint")
			if not validFingerprint(expected):
				inputProblems.append({"index": index, "path": path, "reason": "fingerprintNotATagConfigFingerprint"})
				continue
			problem = configProblem(item.get("config"), targetLeaf(path))
			if problem is not None:
				inputProblems.append({"index": index, "path": path, "reason": problem})
				continue
			paths.append(path)
			fingerprints.append(unicode(expected))
			configurations.append(item.get("config"))
		if inputProblems:
			return toolError("invalid_argument", "Every item must carry an absolute provider-qualified config path, the tcf1 fingerprint of that target's own tag_get_config read, and a non-empty configuration object; no item was executed.", {"reason": "preflightInputFailed", "items": inputProblems})
		# D10 input ceilings: pure validation over the request, so an over-budget
		# batch never reaches the policy read, let alone the Gateway.
		totalInputBytes = 0
		for index in range(len(paths)):
			pathBytes = utf8Bytes(paths[index])
			if pathBytes > PATH_MAX_BYTES:
				return toolError("limit_exceeded", "A target path is " + unicode(pathBytes) + " bytes, over the " + unicode(PATH_MAX_BYTES) + "-byte path ceiling; no item was executed. " + PATH_ADVICE, {"reason": "pathOverLength", "index": index, "path": boundedText(paths[index], 256), "requested": pathBytes, "limit": PATH_MAX_BYTES, "advice": PATH_ADVICE})
			configBytes, problem = configSize(configurations[index], 1)
			if problem is not None:
				advice = CONFIG_ADVICE[problem[0]]
				return toolError("limit_exceeded", "A configuration is over the D10 " + problem[0] + " input ceiling with " + unicode(problem[1]) + " requested against a limit of " + unicode(problem[2]) + "; no item was executed. " + advice, {"reason": problem[0], "index": index, "path": boundedText(paths[index], 256), "requested": problem[1], "limit": problem[2], "advice": advice})
			totalInputBytes += pathBytes + configBytes + len(fingerprints[index])
		if totalInputBytes > INPUT_MAX_BYTES:
			return toolError("limit_exceeded", "The update batch is " + unicode(totalInputBytes) + " bytes, over the " + unicode(INPUT_MAX_BYTES) + "-byte input budget; no item was executed. " + INPUT_BYTES_ADVICE, {"reason": "inputOverByteBudget", "requested": totalInputBytes, "limit": INPUT_MAX_BYTES, "advice": INPUT_BYTES_ADVICE})
		stage = "policy_read"
		policy, policyFailure = readPolicy()
		if policy is None:
			return toolError("operation_disabled", "The Runtime Target Policy is missing or unusable; Runtime Mutations stay disabled.", {"reason": policyFailure, "policyPath": POLICY_PATH})
		allowlists = policy.get("allowlists")
		entries = normalizeEntries(allowlists.get(ALLOWLIST_KEY))
		if entries is None:
			entries = []
		serviceIdentity = unicode(policy.get("serviceIdentity")).strip()
		auditMode = unicode(policy.get("auditMode"))
		auditProfile = policy.get("auditProfile")
		if auditProfile is not None:
			auditProfile = unicode(auditProfile).strip()
		# D10: a usable Policy is what raises the 20-target project default, and the
		# document validation above keeps its value inside the 100-target ceiling.
		effectiveMaxItems = DEFAULT_MAX_ITEMS
		if hasKey(policy, POLICY_MAX_ITEMS_FIELD):
			effectiveMaxItems = int(policy.get(POLICY_MAX_ITEMS_FIELD))
		if len(paths) > effectiveMaxItems:
			policyAdvice = "Split the batch into several calls of at most " + unicode(effectiveMaxItems) + " targets, or raise " + POLICY_MAX_ITEMS_FIELD + " in the Runtime Target Policy."
			return toolError("limit_exceeded", "items is " + unicode(len(paths)) + " targets, over the deployment's limit of " + unicode(effectiveMaxItems) + "; no item was executed. " + policyAdvice, {"reason": "itemsOverPolicyLimit", "requested": len(paths), "limit": effectiveMaxItems, "advice": policyAdvice})
		if auditMode == "required" and not auditProfileAvailable(auditProfile):
			# D30 6: the required-mode audit profile is checked before anything is
			# executed and before any audit row is attempted.
			return toolError("operation_disabled", "The Runtime audit mode is required but the configured audit profile is unavailable; no item was executed.", {"reason": "auditProfileUnavailable", "auditProfile": boundedText(auditProfile, 128)})
		stage = "preflight_targets"
		policyProblems = []
		for index in range(len(paths)):
			path = paths[index]
			if providerName(path).lower() == RESERVED_PROVIDER.lower():
				# Refused by provider, before the allowlist is consulted, so an
				# explicit * cannot reach the policy document.
				policyProblems.append({"index": index, "path": path, "reason": "reservedProvider", "code": "permission_denied"})
				continue
			if isUdtDefinitionTarget(path):
				if not matchesUdtAllowlist(path, entries):
					policyProblems.append({"index": index, "path": path, "reason": "udtDefinitionNotAllowlisted", "code": "permission_denied"})
					continue
			if not matchesAllowlist(path, entries):
				policyProblems.append({"index": index, "path": path, "reason": "targetNotAllowlisted", "code": "permission_denied"})
		if policyProblems:
			# D18: a denied mutation is audited. The decision row is the only row a
			# denial produces, and `off` mode still records nothing.
			refusedText = ",".join([problem["path"] for problem in policyProblems])
			decisionRecorded = auditWrite("decision", refusedText, "outcome=denied code=permission_denied refused=" + unicode(len(policyProblems)) + " requested=" + unicode(len(paths)))
			if auditMode == "required" and not decisionRecorded:
				return toolError("operation_disabled", "The Runtime audit mode is required but the denied-mutation record could not be written; no item was executed.", {"reason": "auditAttemptFailed", "phase": "decision"})
			return toolError("permission_denied", "Every target must be inside the Runtime Target Policy allowlist (a UDT definition only under an explicit _types_ entry) and outside the reserved policy provider; no item was executed.", {"reason": "preflightTargetRefused", "allowlistKey": ALLOWLIST_KEY, "items": policyProblems, "auditRecorded": decisionRecorded})
		stage = "preflight_fingerprint"
		# D30 2 and 3: every target is checked to exist and its token is compared
		# before any item executes. A target that is not there is not_found (so this
		# Tool never creates one) and a mismatch is conflict.
		preconditionProblems = []
		for index in range(len(paths)):
			path = paths[index]
			try:
				present = system.tag.exists(path)
			except (Exception, JavaException) as exc:
				logger.warn("correlationId=" + correlationId + " target=" + unicode(index) + " existence check failed: " + text(exc))
				preconditionProblems.append({"index": index, "path": path, "reason": "existenceCheckFailed", "code": "upstream_error"})
				continue
			existence = existenceProblem(present)
			if existence == "targetMissing":
				preconditionProblems.append({"index": index, "path": path, "reason": "targetMissing", "code": "not_found"})
				continue
			if existence is not None:
				preconditionProblems.append({"index": index, "path": path, "reason": existence, "code": "upstream_error"})
				continue
			try:
				actual, failure = configurationFingerprint(path)
			except (Exception, JavaException) as exc:
				logger.warn("correlationId=" + correlationId + " target=" + unicode(index) + " configuration read failed: " + text(exc))
				preconditionProblems.append({"index": index, "path": path, "reason": "configurationReadFailed", "code": "upstream_error"})
				continue
			if failure is not None:
				preconditionProblems.append({"index": index, "path": path, "reason": failure, "code": "upstream_error"})
				continue
			if actual != fingerprints[index]:
				preconditionProblems.append({"index": index, "path": path, "reason": "fingerprintMismatch", "code": "conflict", "expectedFingerprint": fingerprints[index], "observedFingerprint": actual})
		if preconditionProblems:
			# D30 7 decides the code; the first failing item in item order decides
			# which one, and every failing item is listed.
			code = unicode(preconditionProblems[0].get("code", "conflict"))
			# D18: a denied mutation is audited, whatever the reason. A refused
			# Preflight dispatches nothing and writes one decision row.
			refusedText = ",".join([problem["path"] for problem in preconditionProblems])
			decisionRecorded = auditWrite("decision", refusedText, "outcome=denied code=" + code + " refused=" + unicode(len(preconditionProblems)) + " requested=" + unicode(len(paths)))
			if auditMode == "required" and not decisionRecorded:
				return toolError("operation_disabled", "The Runtime audit mode is required but the denied-mutation record could not be written; no item was executed.", {"reason": "auditAttemptFailed", "phase": "decision"})
			return toolError(code, "Every target must exist and its Tag config fingerprint must match the token from its own tag_get_config read; no item was executed.", {"reason": "preflightPreconditionFailed", "allowlistKey": ALLOWLIST_KEY, "items": preconditionProblems, "auditRecorded": decisionRecorded})
		stage = "audit_attempt"
		targetText = ",".join(paths)
		attemptRecorded = auditWrite("attempt", targetText, "outcome=attempt requested=" + unicode(len(paths)))
		if auditMode == "required" and not attemptRecorded:
			return toolError("operation_disabled", "The Runtime audit mode is required but the attempt record could not be written; no item was executed.", {"reason": "auditAttemptFailed"})
		stage = "dispatch"
		dispatched = True
		results = []
		succeeded = 0
		failed = 0
		outcomeUnknown = 0
		stopped = False
		for index in range(len(paths)):
			if stopped:
				# D30 3: no rollback, and no item after an unattributable dispatch
				# is attempted.
				results.append({"path": paths[index], "status": "not_executed"})
				continue
			definition = {}
			for key in configurations[index].keys():
				definition[text(key)] = configurations[index].get(key)
			# The definition's name is the target's own leaf: a caller cannot
			# rename through this Tool (tag_rename owns the new path's Target).
			definition["name"] = targetLeaf(paths[index])
			try:
				codes = system.tag.configure(targetBase(paths[index]), [definition], COLLISION_POLICY)
			except (Exception, JavaException) as exc:
				logger.warn("correlationId=" + correlationId + " target=" + unicode(index) + " configure is indeterminate: " + text(exc))
				results.append({"path": paths[index], "status": "outcome_unknown"})
				outcomeUnknown += 1
				stopped = True
				continue
			if codes is None or len(codes) != 1 or codes[0] is None:
				results.append({"path": paths[index], "status": "outcome_unknown"})
				outcomeUnknown += 1
				continue
			results.append({"path": paths[index], "status": "executed", "nativeOutcome": quality(codes[0])})
			if qualityIsGood(codes[0]):
				succeeded += 1
			else:
				failed += 1
		stage = "audit_result"
		resultRecorded = auditWrite("result", targetText, "outcome=executed succeeded=" + unicode(succeeded) + " failed=" + unicode(failed) + " outcomeUnknown=" + unicode(outcomeUnknown))
		auditRecorded = bool(attemptRecorded and resultRecorded)
		stage = "observed_read"
		observed = []
		observedBytes = 0
		observedBudgetSpent = False
		for index in range(len(paths)):
			path = paths[index]
			try:
				nativeConfiguration = system.tag.getConfiguration(path, False, False)
				# D10: the raw native read-back is walked with both of its ceilings
				# before it is converted, so a deeply nested or oversized configuration
				# is refused as an explicit per-item Observed error instead of being
				# materialized for the result -- and the item's Native outcome above is
				# unaffected. Only a read-back that passed the walk is converted.
				observedSize, problem = observedConfigurationSize(nativeConfiguration, 1, OBSERVED_CONFIGURATION_MAX_BYTES)
				if problem is None and observedBudgetSpent:
					problem = ("observedStateBudgetSpent", observedSize, OBSERVED_STATE_MAX_BYTES, OBSERVED_STATE_ADVICE)
				if problem is None and observedBytes + observedSize > OBSERVED_STATE_MAX_BYTES:
					problem = ("observedStateOverByteBudget", observedBytes + observedSize, OBSERVED_STATE_MAX_BYTES, OBSERVED_STATE_ADVICE)
					observedBudgetSpent = True
				if problem is not None:
					observed.append(observedError(path, "limit_exceeded", observedProblemMessage(problem)))
					continue
				configuration = jsonValue(nativeConfiguration)
				if not isinstance(configuration, (list, tuple, List)) or len(configuration) == 0:
					raise TypeError("Observed configuration read returned no node")
				# The fingerprint is taken over the D28-encoded configuration, which is
				# exactly what the domain-level encoding below publishes, so the raw
				# value is stored and the encoding happens once.
				observedBytes += observedSize
				observed.append({"path": path, "status": "ok", "fingerprint": tagConfigFingerprint(encodeNulls(configuration)), "configuration": configuration})
			except (Exception, JavaException) as itemExc:
				logger.warn("correlationId=" + correlationId + " observed read failed for target=" + unicode(index) + ": " + text(itemExc))
				observed.append(observedError(path, "upstream_error", "The observed Tag configuration could not be read."))
		stage = "serialization"
		notExecuted = 0
		for result in results:
			if result["status"] == "not_executed":
				notExecuted += 1
		outcomeCounts = (succeeded, failed, outcomeUnknown, notExecuted)
		# The per-item Native outcomes are established facts by now, so neither an
		# over-budget Observed state nor a serializer failure may replace them with
		# a Tool error. The result is rendered with the full Observed state and,
		# when that cannot be returned, without it.
		domain, encoded = encodeDomain(observed, outcomeCounts)
		if encoded is None:
			domain, encoded = encodeDomain(omittedObserved(OBSERVED_OMITTED_SERIALIZATION), outcomeCounts)
		elif len(encoded.encode("utf-8")) > OUTPUT_MAX_BYTES:
			domain, encoded = encodeDomain(omittedObserved(OBSERVED_OMITTED_CEILING), outcomeCounts)
		if encoded is None:
			return toolError("upstream_error", "The Tag configuration change completed but its structured result could not be serialized.", {"reason": "serializationFailure", "stage": stage, "requested": len(paths), "succeeded": succeeded, "failed": failed, "outcomeUnknown": outcomeUnknown, "auditRecorded": auditRecorded})
		payloadBytes = len(encoded.encode("utf-8"))
		if payloadBytes > OUTPUT_MAX_BYTES:
			# D10: over-budget states what was requested, the limit, and what did
			# execute, so a completed change is never silent.
			return toolError("limit_exceeded", "The structured result is " + unicode(payloadBytes) + " bytes, over the " + unicode(OUTPUT_MAX_BYTES) + "-byte output ceiling, even without the Observed state; the change itself is done. " + OUTPUT_ADVICE, {"reason": "outputOverLimit", "requestedBytes": payloadBytes, "limitBytes": OUTPUT_MAX_BYTES, "advice": OUTPUT_ADVICE, "requested": len(paths), "succeeded": succeeded, "failed": failed, "outcomeUnknown": outcomeUnknown, "auditRecorded": auditRecorded})
		return {"structuredContent": domain}
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " stage=" + stage + " tag_update failed: " + text(exc))
		if dispatched:
			# D08: never replay an uncertain mutation; the agent re-reads instead.
			return toolError("outcome_unknown", "A Tag configuration change may have been dispatched but its outcome could not be established.", {"reason": "dispatchOutcomeUnknown", "stage": stage})
		return toolError("upstream_error", "The Tag configuration update could not be completed.", {"reason": "handlerFailure", "stage": stage})
