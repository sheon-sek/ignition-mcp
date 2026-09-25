def onToolCalled(builder, items):
	from java.lang import Boolean, Number, Enum, Exception as JavaException
	from java.util import UUID, Date, Map, List
	import math
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.TagRename")

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
	ALLOWLIST_KEY = "tag_rename"
	WILDCARD = "*"
	# D30 6: a UDT definition lives in the provider's _types_ namespace and is
	# reachable only through an explicit _types_ allowlist entry; a bare * does not
	# cover it.
	UDT_NAMESPACE = "_types_"
	AUDIT_ACTION = "ignition-mcp.tag_rename"
	AUDIT_MODES = ("best_effort", "required", "off")
	# D30 2: the Tag config fingerprint. Repo-defined, versioned `tcf1` and
	# deterministic: SHA-256 over the canonical JSON text of the D28-encoded
	# configuration the caller's tag_get_config read published. The rule and its
	# golden vectors are contracts/shared/tag-config-fingerprint.json.
	FINGERPRINT_PREFIX = "tcf1:"
	FINGERPRINT_HEX = "0123456789abcdef"
	FINGERPRINT_LENGTH = 69
	# D30 4: the handler fixes the Gateway's collision policy. Abort is the policy
	# for a rename and a move, so a destination that appears in the race window is
	# never replaced.
	COLLISION_POLICY = "Abort"
	# D10 budgets: the project safe default is 20 targets, the deployment may raise
	# it through the Runtime Target Policy up to the 100-target hard ceiling, and
	# every request is bounded by both of its paths and one finite aggregate byte
	# ceiling.
	DEFAULT_MAX_ITEMS = 20
	HARD_MAX_ITEMS = 100
	POLICY_MAX_ITEMS_FIELD = "tagRenameMaxItems"
	PATH_MAX_BYTES = 2048
	INPUT_MAX_BYTES = 65536
	# D10 output: the Observed state carries its own budget, so a configuration it
	# cannot return never becomes the reason a completed change's outcomes
	# disappear, and a Native diagnostic never runs unbounded either.
	OBSERVED_CONFIGURATION_MAX_BYTES = 16384
	OBSERVED_STATE_MAX_BYTES = 65536
	DIAGNOSTIC_MAX_BYTES = 256
	OUTPUT_MAX_BYTES = 262144

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

	def validFingerprint(value):
		if not isinstance(value, basestring) or len(value) != FINGERPRINT_LENGTH:
			return False
		if not value.startswith(FINGERPRINT_PREFIX):
			return False
		for character in value[len(FINGERPRINT_PREFIX):]:
			if FINGERPRINT_HEX.find(character) < 0:
				return False
		return True

	def utf8Bytes(value):
		return len(text(value).encode("utf-8"))

	def valueBytesBounded(value, limit):
		# The Observed half of the same idea: measuring what the Gateway returned
		# must not itself materialize an unbounded value.
		if value is None:
			return 4
		if isinstance(value, basestring):
			return utf8Bytes(value)
		if isinstance(value, (bool, Boolean)):
			return 5
		if isinstance(value, (int, long, float, Number)):
			return 24
		if isinstance(value, (list, tuple, List)):
			total = 2
			for child in value:
				total += valueBytesBounded(child, limit)
				if total > limit:
					return total
			return total
		if isinstance(value, Map):
			total = 2
			for entry in value.entrySet():
				total += utf8Bytes(unicode(entry.getKey())) + valueBytesBounded(entry.getValue(), limit)
				if total > limit:
					return total
			return total
		if isinstance(value, dict):
			total = 2
			for key in value:
				total += utf8Bytes(unicode(key)) + valueBytesBounded(value[key], limit)
				if total > limit:
					return total
			return total
		return 64

	def observedConfigurationProblem(value):
		# The per-configuration half of the Observed-state budget. An over-budget
		# configuration is an explicit observed error, never a silent truncation and
		# never a reason to lose the item's Native outcome.
		size = valueBytesBounded(value, OBSERVED_CONFIGURATION_MAX_BYTES)
		if size > OBSERVED_CONFIGURATION_MAX_BYTES:
			return "The Observed configuration is " + unicode(size) + " bytes, over the " + unicode(OBSERVED_CONFIGURATION_MAX_BYTES) + "-byte Observed-state configuration budget; it was not returned."
		return None

	def observedError(path, code, message):
		return {"path": path, "status": "error", "error": {"code": code, "message": message, "correlationId": correlationId}}

	def omittedObserved(reason):
		return [observedError(path, "schema_mismatch", reason) for path in observedPaths]

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
				return "policyTagRenameMaxItems"
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

	def existenceProblem(value):
		# system.tag.exists answers a boolean; anything else is not an answer, and a
		# non-answer must not be read as "the target is there".
		if isinstance(value, bool):
			return None if value else "targetMissing"
		if isinstance(value, Boolean):
			return None if value.booleanValue() else "targetMissing"
		return "existenceCheckIndeterminate"

	def exists(path):
		# A Gateway answers system.tag.getConfiguration for a path that is not there
		# with a synthesized default node (the ticket #10 live run recorded the same
		# one on 8.3.8 and 8.3.9), so presence is always decided by system.tag.exists.
		return existenceProblem(system.tag.exists(path))

	def configurationFingerprint(path):
		nativeConfiguration = system.tag.getConfiguration(path, False, False)
		configuration = jsonValue(nativeConfiguration)
		if not isinstance(configuration, (list, tuple, List)) or len(configuration) == 0:
			return (None, "configurationUnavailable")
		encoded = encodeNulls(configuration)
		return (tagConfigFingerprint(encoded), None)

	def absentObserved(path, subject):
		# One path that is not there, which is the whole Observed state of a delete
		# and one half of a move or a rename. A present path where absence is the
		# promise is an explicit observed error, never a silent omission.
		try:
			existence = exists(path)
		except (Exception, JavaException) as exc:
			logger.warn("correlationId=" + correlationId + " observed presence check failed for " + path + ": " + text(exc))
			return observedError(path, "upstream_error", "The Observed state of " + subject + " could not be read.")
		if existence == "targetMissing":
			return {"path": path, "status": "ok", "absent": True}
		if existence is None:
			return observedError(path, "upstream_error", "The target is still present after the rename; it was not removed.")
		return observedError(path, "upstream_error", "The Observed state of " + subject + " could not be read.")
	stage = "input_validation"
	dispatched = False
	try:

		def validNewName(value):
			# A rename names a leaf, never a path: system.tag.rename takes the new name
			# and the target's own parent decides where it lands, so the new path is the
			# old parent plus this name and nothing else.
			if not isinstance(value, basestring) or not value.strip():
				return False
			value = value.strip()
			for character in ("/", ".", "[", "]", "*", "?", ":"):
				if character in value:
					return False
			return True

		def targetBase(value):
			index = value.rfind("/")
			if index <= value.find("]"):
				return value[0:value.find("]") + 1]
			return value[0:index]

		def renamedPath(value, newName):
			# A rename never changes the parent, so the new path is the old parent plus
			# the new name - which is what D30 6 measures against the Target allowlist.
			base = targetBase(value)
			if base.endswith("]"):
				return base + newName
			return base + "/" + newName
		def singleOutcome(value):
			# One path's Mutation answers one QualityCode; the Toolkit functions that
			# also take a path list answer a one-element list for a one-element list.
			# Either form is that item's outcome, and anything else is not an answer.
			if value is None:
				return None
			if isinstance(value, (list, tuple, List)):
				if len(value) != 1:
					return None
				return value[0]
			return value

		def collisionOutcome(path, nativeQuality):
			# D30 4 and 7: the check-to-dispatch race. Fixing collisionPolicy=Abort means the
			# Mutation can only have been refused, never applied, so a destination that is
			# there now is the collision another writer created in the window - conflict,
			# never a success and never replayed. Only a plain boolean true is an answer: a
			# check that raises or answers something else leaves the item with the Native
			# outcome the provider itself gave.
			if qualityIsGood(nativeQuality):
				return False
			return destinationAppeared(path)

		def destinationAppeared(path):
			# The post-dispatch half of the collision rule: a destination or new path
			# the Gateway answers as present after an aborted call is that collision.
			# A check that raises, or answers something that is not a boolean, is not an
			# answer, so the item stays outcome_unknown rather than being called either
			# way.
			try:
				present = system.tag.exists(path)
			except (Exception, JavaException) as exc:
				logger.warn("correlationId=" + correlationId + " post-dispatch presence check failed for " + path + ": " + text(exc))
				return False
			return present is True or (isinstance(present, Boolean) and present.booleanValue())

		def presentObserved(path, observedBytes, observedBudgetSpent):
			# One path that is there: its configuration and the D30 2 fingerprint the
			# caller's next Mutation uses as a token.
			nativeConfiguration = system.tag.getConfiguration(path, False, False)
			configuration = jsonValue(nativeConfiguration)
			if not isinstance(configuration, (list, tuple, List)) or len(configuration) == 0:
				raise TypeError("Observed configuration read returned no node")
			problem = observedConfigurationProblem(nativeConfiguration)
			if problem is None and observedBudgetSpent:
				problem = "The Observed-state budget of " + unicode(OBSERVED_STATE_MAX_BYTES) + " bytes is already spent; this configuration was not returned."
			if problem is None and observedBytes + valueBytesBounded(nativeConfiguration, OBSERVED_CONFIGURATION_MAX_BYTES) > OBSERVED_STATE_MAX_BYTES:
				problem = "The Observed state already holds " + unicode(observedBytes) + " bytes, so returning this configuration would pass the " + unicode(OBSERVED_STATE_MAX_BYTES) + "-byte budget; it was not returned."
				observedBudgetSpent = True
			if problem is not None:
				return (observedError(path, "limit_exceeded", problem), observedBytes, observedBudgetSpent)
			observedBytes += valueBytesBounded(nativeConfiguration, OBSERVED_CONFIGURATION_MAX_BYTES)
			return ({"path": path, "status": "ok", "absent": False, "fingerprint": tagConfigFingerprint(encodeNulls(configuration)), "configuration": configuration}, observedBytes, observedBudgetSpent)

		def absentObservedOrPresent(path, subject, observedBytes, observedBudgetSpent):
			# One path whose presence is the promise: its configuration and the D30 2
			# fingerprint are the Observed state, and absence is an explicit observed
			# error that names the path.
			try:
				presence = exists(path)
			except (Exception, JavaException) as exc:
				logger.warn("correlationId=" + correlationId + " observed presence check failed for " + path + ": " + text(exc))
				return (observedError(path, "upstream_error", "The Observed state of " + subject + " could not be read."), observedBytes, observedBudgetSpent)
			if presence == "targetMissing":
				return (observedError(path, "upstream_error", "The target is not there after the rename, so the change did not land where it was asked to."), observedBytes, observedBudgetSpent)
			if presence is not None:
				return (observedError(path, "upstream_error", "The Observed state of " + subject + " could not be read."), observedBytes, observedBudgetSpent)
			try:
				return presentObserved(path, observedBytes, observedBudgetSpent)
			except (Exception, JavaException) as exc:
				logger.warn("correlationId=" + correlationId + " observed read failed for " + path + ": " + text(exc))
				return (observedError(path, "upstream_error", "The Observed state of " + subject + " could not be read."), observedBytes, observedBudgetSpent)
		if not isinstance(items, (list, tuple, List)) or len(items) == 0:
			return toolError("invalid_argument", "items must be a non-empty array of {path, newName, expectedFingerprint} items.", {"reason": "itemsNotAnArray"})
		if len(items) > HARD_MAX_ITEMS:
			return toolError("limit_exceeded", "items exceeds the hard limit of 100 targets.", {"reason": "itemsOverHardLimit", "requested": len(items), "limit": HARD_MAX_ITEMS})
		paths = []
		newNames = []
		newPaths = []
		fingerprints = []
		inputProblems = []
		for index in range(len(items)):
			item = items[index]
			if not isinstance(item, dict):
				inputProblems.append({"index": index, "reason": "itemNotAnObject"})
				continue
			keys = [text(key) for key in item.keys()]
			pathValue = item.get("path")
			nameValue = item.get("newName")
			if "path" not in keys or "newName" not in keys or "expectedFingerprint" not in keys or len(keys) != 3:
				inputProblems.append({"index": index, "path": boundedText(pathValue, 256), "reason": "itemKeysMustBePathNewNameAndFingerprint"})
				continue
			if not validTargetPath(pathValue):
				inputProblems.append({"index": index, "path": boundedText(pathValue, 256), "reason": "pathNotAConfigPath"})
				continue
			if not validNewName(nameValue):
				inputProblems.append({"index": index, "path": boundedText(pathValue, 256), "reason": "newNameNotASingleSegment"})
				continue
			path = unicode(pathValue).strip()
			newName = unicode(nameValue).strip()
			expected = item.get("expectedFingerprint")
			if not validFingerprint(expected):
				inputProblems.append({"index": index, "path": path, "reason": "fingerprintNotATagConfigFingerprint"})
				continue
			paths.append(path)
			newNames.append(newName)
			newPaths.append(renamedPath(path, newName))
			fingerprints.append(unicode(expected))
		if inputProblems:
			return toolError("invalid_argument", "Every item must carry an absolute provider-qualified config path, the tcf1 fingerprint of that target's own tag_get_config read, and a new name that is one path segment; no item was executed.", {"reason": "preflightInputFailed", "items": inputProblems})
		# D10 input ceilings: pure validation over the request, so an over-budget
		# batch never reaches the policy read, let alone the Gateway.
		totalInputBytes = 0
		for index in range(len(paths)):
			pathBytes = utf8Bytes(paths[index])
			if pathBytes > PATH_MAX_BYTES:
				return toolError("limit_exceeded", "A target path is over the documented input ceiling; no item was executed.", {"reason": "pathOverLength", "index": index, "path": boundedText(paths[index], 256), "requested": pathBytes, "limit": PATH_MAX_BYTES})
			newPathBytes = utf8Bytes(newPaths[index])
			if newPathBytes > PATH_MAX_BYTES:
				return toolError("limit_exceeded", "The new path of a target is over the documented input ceiling; no item was executed.", {"reason": "pathOverLength", "index": index, "path": boundedText(newPaths[index], 256), "requested": newPathBytes, "limit": PATH_MAX_BYTES})
			totalInputBytes += pathBytes + newPathBytes + len(fingerprints[index])
		if totalInputBytes > INPUT_MAX_BYTES:
			return toolError("limit_exceeded", "The rename batch is " + unicode(totalInputBytes) + " bytes, over the " + unicode(INPUT_MAX_BYTES) + "-byte input budget; split it across calls.", {"reason": "inputOverByteBudget", "requested": totalInputBytes, "limit": INPUT_MAX_BYTES})
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
			return toolError("limit_exceeded", "items exceeds the deployment's limit of " + unicode(effectiveMaxItems) + " targets; split the batch or raise " + POLICY_MAX_ITEMS_FIELD + " in the Runtime Target Policy.", {"reason": "itemsOverPolicyLimit", "requested": len(paths), "limit": effectiveMaxItems})
		if auditMode == "required" and not auditProfileAvailable(auditProfile):
			# D30 6: the required-mode audit profile is checked before anything is
			# executed and before any audit row is attempted.
			return toolError("operation_disabled", "The Runtime audit mode is required but the configured audit profile is unavailable; no item was executed.", {"reason": "auditProfileUnavailable", "auditProfile": boundedText(auditProfile, 128)})
		stage = "preflight_targets"
		# D30 6: a rename checks the new path - the old parent plus the new name.
		# Owner ruling 1 still covers both ends: neither the target nor the path it
		# would become may sit inside the reserved policy provider.
		policyProblems = []
		for index in range(len(paths)):
			path = paths[index]
			newPath = newPaths[index]
			reservedSource = providerName(path).lower() == RESERVED_PROVIDER.lower()
			reservedDestination = providerName(newPath).lower() == RESERVED_PROVIDER.lower()
			if reservedSource or reservedDestination:
				policyProblems.append({"index": index, "path": path, "newPath": newPath, "refusedPath": path if reservedSource else newPath, "reason": "reservedProvider", "code": "permission_denied"})
				continue
			if isUdtDefinitionTarget(newPath):
				if not matchesUdtAllowlist(newPath, entries):
					policyProblems.append({"index": index, "path": path, "newPath": newPath, "refusedPath": newPath, "reason": "udtDefinitionNotAllowlisted", "code": "permission_denied"})
					continue
			if not matchesAllowlist(newPath, entries):
				policyProblems.append({"index": index, "path": path, "newPath": newPath, "refusedPath": newPath, "reason": "targetNotAllowlisted", "code": "permission_denied"})
		if policyProblems:
			# D18: a denied mutation is audited. The decision row is the only row a
			# denial produces, and `off` mode still records nothing.
			refusedText = ",".join([problem["path"] for problem in policyProblems])
			decisionRecorded = auditWrite("decision", refusedText, "outcome=denied code=permission_denied refused=" + unicode(len(policyProblems)) + " requested=" + unicode(len(paths)))
			if auditMode == "required" and not decisionRecorded:
				return toolError("operation_disabled", "The Runtime audit mode is required but the denied-mutation record could not be written; no item was executed.", {"reason": "auditAttemptFailed", "phase": "decision"})
			return toolError("permission_denied", "The new path of every target - its own parent plus the new name - must be inside the Runtime Target Policy allowlist (a UDT definition only under an explicit _types_ entry), and neither the target nor that new path may be inside the reserved policy provider; no item was executed.", {"reason": "preflightTargetRefused", "allowlistKey": ALLOWLIST_KEY, "items": policyProblems, "auditRecorded": decisionRecorded})
		stage = "preflight_fingerprint"
		# D30 2 and 3: every item's target existence and fingerprint, and the new
		# path's absence, are checked before any item executes.
		preconditionProblems = []
		for index in range(len(paths)):
			path = paths[index]
			newPath = newPaths[index]
			try:
				existence = exists(path)
			except (Exception, JavaException) as exc:
				logger.warn("correlationId=" + correlationId + " item=" + unicode(index) + " existence check failed: " + text(exc))
				preconditionProblems.append({"index": index, "path": path, "newPath": newPath, "refusedPath": path, "reason": "existenceCheckFailed", "code": "upstream_error"})
				continue
			if existence == "targetMissing":
				preconditionProblems.append({"index": index, "path": path, "newPath": newPath, "refusedPath": path, "reason": "targetMissing", "code": "not_found"})
				continue
			if existence is not None:
				preconditionProblems.append({"index": index, "path": path, "newPath": newPath, "refusedPath": path, "reason": existence, "code": "upstream_error"})
				continue
			try:
				actual, failure = configurationFingerprint(path)
			except (Exception, JavaException) as exc:
				logger.warn("correlationId=" + correlationId + " item=" + unicode(index) + " configuration read failed: " + text(exc))
				preconditionProblems.append({"index": index, "path": path, "newPath": newPath, "refusedPath": path, "reason": "configurationReadFailed", "code": "upstream_error"})
				continue
			if failure is not None:
				preconditionProblems.append({"index": index, "path": path, "newPath": newPath, "refusedPath": path, "reason": failure, "code": "upstream_error"})
				continue
			if actual != fingerprints[index]:
				preconditionProblems.append({"index": index, "path": path, "newPath": newPath, "refusedPath": path, "reason": "fingerprintMismatch", "code": "conflict", "expectedFingerprint": fingerprints[index], "observedFingerprint": actual})
				continue
			try:
				newPathExistence = exists(newPath)
			except (Exception, JavaException) as exc:
				logger.warn("correlationId=" + correlationId + " item=" + unicode(index) + " new path existence check failed: " + text(exc))
				preconditionProblems.append({"index": index, "path": path, "newPath": newPath, "refusedPath": newPath, "reason": "existenceCheckFailed", "code": "upstream_error"})
				continue
			if newPathExistence is None:
				preconditionProblems.append({"index": index, "path": path, "newPath": newPath, "refusedPath": newPath, "reason": "newPathExists", "code": "conflict"})
				continue
			if newPathExistence != "targetMissing":
				preconditionProblems.append({"index": index, "path": path, "newPath": newPath, "refusedPath": newPath, "reason": newPathExistence, "code": "upstream_error"})
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
			return toolError(code, "Every target must exist with the fingerprint of its own tag_get_config read, and the new path must be free: an occupied new path is conflict and a target that is not there is not_found; no item was executed.", {"reason": "preflightPreconditionFailed", "allowlistKey": ALLOWLIST_KEY, "items": preconditionProblems, "auditRecorded": decisionRecorded})
		# The Observed state reports two paths per item: the new path, which is
		# present, and the old path, which is gone.
		observedPaths = []
		for index in range(len(paths)):
			observedPaths.append(newPaths[index])
			observedPaths.append(paths[index])
		stage = "audit_attempt"
		targetText = ",".join(observedPaths)
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
				results.append({"path": paths[index], "newPath": newPaths[index], "status": "not_executed"})
				continue
			try:
				# One system.tag.rename call per item: the target and the new name,
				# never a full path, because the target's own parent decides where
				# the node lands.
				outcome = system.tag.rename(paths[index], newNames[index], COLLISION_POLICY)
			except (Exception, JavaException) as exc:
				# D30 4: Abort makes a new path that appeared in the race window fail
				# the call instead of replacing it. A new path the Gateway now answers
				# as present is that collision - conflict, never a success - and it is
				# attributable, so the rest of the batch still runs.
				if destinationAppeared(newPaths[index]):
					results.append({"path": paths[index], "newPath": newPaths[index], "status": "conflict", "reason": "newPathExists"})
					failed += 1
					continue
				logger.warn("correlationId=" + correlationId + " item=" + unicode(index) + " rename is indeterminate: " + text(exc))
				results.append({"path": paths[index], "newPath": newPaths[index], "status": "outcome_unknown"})
				outcomeUnknown += 1
				stopped = True
				continue
			single = singleOutcome(outcome)
			if single is None:
				results.append({"path": paths[index], "newPath": newPaths[index], "status": "outcome_unknown"})
				outcomeUnknown += 1
				continue
			if collisionOutcome(newPaths[index], single):
				# The new path appeared after the Preflight existence check and Abort left
				# it alone: the conflict D11 and D30 2 name, reported per item with the
				# provider's own outcome still attached.
				results.append({"path": paths[index], "newPath": newPaths[index], "status": "conflict", "reason": "newPathExists", "nativeOutcome": quality(single)})
				failed += 1
				continue
			results.append({"path": paths[index], "newPath": newPaths[index], "status": "executed", "nativeOutcome": quality(single)})
			if qualityIsGood(single):
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
			# Observed state: the new path is present and the old path is gone.
			entry, observedBytes, observedBudgetSpent = absentObservedOrPresent(newPaths[index], "the renamed target", observedBytes, observedBudgetSpent)
			observed.append(entry)
			observed.append(absentObserved(paths[index], "the renamed target"))
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
			return toolError("upstream_error", "The Tag rename completed but its structured result could not be serialized.", {"reason": "serializationFailure", "stage": stage, "requested": len(paths), "succeeded": succeeded, "failed": failed, "outcomeUnknown": outcomeUnknown, "auditRecorded": auditRecorded})
		payloadBytes = len(encoded.encode("utf-8"))
		if payloadBytes > OUTPUT_MAX_BYTES:
			# D10: over-budget states what was requested, the limit, and what did
			# execute, so a completed change is never silent.
			return toolError("limit_exceeded", "The structured result is " + unicode(payloadBytes) + " bytes, over the " + unicode(OUTPUT_MAX_BYTES) + "-byte output ceiling, even without the Observed state; rename fewer targets, and re-read with tag_get_config.", {"reason": "outputOverLimit", "requestedBytes": payloadBytes, "limitBytes": OUTPUT_MAX_BYTES, "requested": len(paths), "succeeded": succeeded, "failed": failed, "outcomeUnknown": outcomeUnknown, "auditRecorded": auditRecorded})
		return {"structuredContent": domain}
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " stage=" + stage + " tag_rename failed: " + text(exc))
		if dispatched:
			# D08: never replay an uncertain mutation; the agent re-reads instead.
			return toolError("outcome_unknown", "A Tag rename may have been dispatched but its outcome could not be established.", {"reason": "dispatchOutcomeUnknown", "stage": stage})
		return toolError("upstream_error", "The Tag rename could not be completed.", {"reason": "handlerFailure", "stage": stage})
