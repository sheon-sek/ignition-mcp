def onToolCalled(builder, paths):
	from java.lang import Boolean, Number, Exception as JavaException
	from java.util import UUID, Date, Map, List
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.AlarmUnshelve")

	# D30 1: the Runtime Target Policy is a deployment-owned document outside the
	# bundle. Ticket #6 characterized its storage and its bounded read: the small
	# companion length Tag is read first so an over-cap document is never
	# materialized, because a Tag value has no native size limit.
	POLICY_PROVIDER = "IgnitionMCPPolicy"
	POLICY_LENGTH_PATH = "[IgnitionMCPPolicy]RuntimeTargetPolicyLength"
	POLICY_PATH = "[IgnitionMCPPolicy]RuntimeTargetPolicy"
	POLICY_MAX_BYTES = 32768
	POLICY_READ_TIMEOUT_MS = 5000
	# D30 1 (#6 recommendation): the Runtime plane cannot write the policy, and
	# that is a product rule. An Alarm path whose *provider* is the reserved one is
	# refused by the same rule, before Preflight, whatever the allowlist says.
	# The owner ruling matches the provider component only, never a later segment
	# that happens to spell the name (see pathProvider below).
	RESERVED_PROVIDER = "IgnitionMCPPolicy"
	ALLOWLIST_KEY = "alarm_unshelve"
	WILDCARD = "*"
	AUDIT_ACTION = "ignition-mcp.alarm_unshelve"
	AUDIT_MODES = ("best_effort", "required", "off")
	# D10 alarm path ceiling, as alarm_status applies it to the same language.
	PATH_MAX_LENGTH = 2048
	# D10 budgets: the project safe default is 20 targets, the deployment may
	# raise it through the Runtime Target Policy up to the 100-target hard
	# ceiling, and one aggregate input-byte budget bounds the whole request.
	DEFAULT_MAX_PATHS = 20
	HARD_MAX_PATHS = 100
	POLICY_MAX_PATHS_FIELD = "alarmMaxPaths"
	INPUT_MAX_BYTES = 65536
	# The Observed state bounds the two Gateway-provided strings it reports, so a
	# value it cannot return never becomes the reason an outcome disappears.
	OBSERVED_USER_MAX_CHARS = 256
	OBSERVED_EXPIRATION_MAX_CHARS = 64
	OBSERVED_STATE_MAX_BYTES = 65536
	# alarm_shelved_list's own output ceiling; the Observed read uses it to stay
	# bounded and reports an error item rather than materializing more.
	SHELVED_READ_LIMIT = 500
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

	def jsonValue(value):
		if value is None:
			return None
		if isinstance(value, Date):
			return unicode(value.toInstant().toString())
		return unicode(value)

	def qualityIsGood(value):
		if value is None:
			return False
		if hasattr(value, "isGood"):
			return bool(value.isGood())
		return unicode(value).find("Good") == 0

	def utf8Bytes(value):
		return len(text(value).encode("utf-8"))

	def observedError(path, code, message):
		return {"path": path, "status": "error", "error": {"code": code, "message": message, "correlationId": correlationId}}

	def observedEntryProblem(user, expiration):
		# D10 output: bound the two Gateway-provided strings the Observed state
		# reports. An over-budget entry is reported explicitly, never truncated.
		if isinstance(user, basestring) and len(user) > OBSERVED_USER_MAX_CHARS:
			return "The shelving identity is " + unicode(len(user)) + " characters, over the " + unicode(OBSERVED_USER_MAX_CHARS) + "-character Observed-state budget; it was not returned."
		if isinstance(expiration, basestring) and len(expiration) > OBSERVED_EXPIRATION_MAX_CHARS:
			return "The shelving expiration is " + unicode(len(expiration)) + " characters, over the " + unicode(OBSERVED_EXPIRATION_MAX_CHARS) + "-character Observed-state budget; it was not returned."
		return None

	OBSERVED_OMITTED_SERIALIZATION = "The Observed state was omitted because the structured result could not be serialized; the per-item outcomes above are complete."
	OBSERVED_OMITTED_CEILING = "The Observed state was omitted to keep the structured result inside the 256 KiB output ceiling; the per-item outcomes above are complete."

	def omittedObserved(reason):
		return [observedError(path, "schema_mismatch", reason) for path in exactPaths]

	def buildDomain(observedEntries):
		domain = {
			"items": items,
			"observed": observedEntries,
			"summary": {"requested": len(exactPaths), "executed": executed, "outcomeUnknown": outcomeUnknown, "auditMode": auditMode, "auditRecorded": auditRecorded},
			"meta": {"correlationId": correlationId},
		}
		return encodeNulls(domain)

	def encodeDomain(observedEntries):
		# The serializer is not allowed to decide a mutation's result: a failure
		# here is caught so the per-item outcomes still reach the caller.
		try:
			domain = buildDomain(observedEntries)
			return (domain, system.util.jsonEncode(domain))
		except (Exception, JavaException) as encodeExc:
			logger.warn("correlationId=" + correlationId + " structured result serialization failed: " + text(encodeExc))
			return (None, None)

	def alarmPathProblem(value):
		# D12: mutation targets are exact Alarm paths. A pattern without * matches
		# only the source it spells out (ticket #6), so "no wildcard" is what makes
		# a target exact, and a provider-qualified path is the rendered form
		# alarm_shelved_list reports for the same shelving.
		if not isinstance(value, basestring):
			return "pathNotText"
		path = value.strip()
		if path == "":
			return "pathEmpty"
		if len(path) > PATH_MAX_LENGTH:
			return "pathOverLength"
		if "*" in path:
			return "wildcardPath"
		for character in path:
			if character.isspace():
				return "pathWhitespace"
		if not path.startswith("prov:"):
			return "pathNotProviderQualified"
		remainder = path[len("prov:"):]
		if remainder == "" or remainder.startswith(":") or remainder.startswith("/"):
			return "pathNotProviderQualified"
		return None

	def pathProvider(value):
		# D30 1 owner ruling (reserved_provider_match: provider_component_only): the
		# rendered Alarm path is prov:<provider>:<Tag path>:/alm:<name>, so its
		# provider is the component between the scheme and the next separator.
		# Comparing that component is what keeps a Tag or Alarm segment that merely
		# spells the reserved name an ordinary, allowlist-checked target.
		if not isinstance(value, basestring) or not value.startswith("prov:"):
			return ""
		remainder = value[len("prov:"):]
		separator = remainder.find(":")
		if separator < 0:
			return remainder
		return remainder[:separator]

	def normalizeEntries(entries):
		# D30 1: allowlist entries are provider-qualified alarm path prefixes
		# matched at segment boundaries; * must be written explicitly.
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
			if alarmPathProblem(entry) is not None:
				return None
			normalized.append(entry)
		return normalized

	def matchesAllowlist(path, entries):
		# A segment boundary in an Alarm path is either separator, so
		# .../Exact never matches .../ExactSibling.
		for entry in entries:
			if entry == WILDCARD:
				return True
			if path == entry:
				return True
			if path.startswith(entry) and path[len(entry)] in ("/", ":"):
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
		if hasKey(document, "alarmShelveMaxSeconds"):
			shelveCap = document.get("alarmShelveMaxSeconds")
			if isinstance(shelveCap, bool) or not isinstance(shelveCap, (int, long)) or shelveCap <= 0:
				return "policyAlarmShelveMaxSeconds"
		if hasKey(document, POLICY_MAX_PATHS_FIELD):
			# D10: the deployment may raise the 20-target default, never above the
			# 100-target hard ceiling.
			maxPaths = document.get(POLICY_MAX_PATHS_FIELD)
			if isinstance(maxPaths, bool) or not isinstance(maxPaths, (int, long)) or maxPaths < 1 or maxPaths > HARD_MAX_PATHS:
				return "policyAlarmMaxPaths"
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

	stage = "input_validation"
	dispatched = False
	try:
		if not isinstance(paths, (list, tuple, List)) or len(paths) == 0:
			return toolError("invalid_argument", "paths must be a non-empty array of exact Alarm paths.", {"reason": "pathsNotAnArray"})
		if len(paths) > HARD_MAX_PATHS:
			return toolError("limit_exceeded", "paths exceeds the hard limit of 100 items.", {"reason": "pathsOverHardLimit", "requested": len(paths), "limit": HARD_MAX_PATHS})
		exactPaths = []
		inputProblems = []
		for index in range(len(paths)):
			problem = alarmPathProblem(paths[index])
			if problem is not None:
				inputProblems.append({"index": index, "path": boundedText(paths[index], 256), "reason": problem})
				continue
			exactPaths.append(unicode(paths[index]).strip())
		if inputProblems:
			return toolError("invalid_argument", "Every target must be a provider-qualified exact Alarm path with no wildcard; no path was unshelved.", {"reason": "preflightInputFailed", "items": inputProblems})
		# D10: one finite aggregate input-byte budget over the target list, checked
		# before the policy read so an over-budget request never reaches the Gateway.
		totalInputBytes = 0
		for path in exactPaths:
			totalInputBytes += utf8Bytes(path)
		if totalInputBytes > INPUT_MAX_BYTES:
			return toolError("limit_exceeded", "The unshelve batch is " + unicode(totalInputBytes) + " bytes, over the " + unicode(INPUT_MAX_BYTES) + "-byte input budget; split it across calls.", {"reason": "inputOverByteBudget", "requested": totalInputBytes, "limit": INPUT_MAX_BYTES})
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
		# validation above keeps its value inside the 100-target hard ceiling.
		effectiveMaxPaths = DEFAULT_MAX_PATHS
		if hasKey(policy, POLICY_MAX_PATHS_FIELD):
			effectiveMaxPaths = int(policy.get(POLICY_MAX_PATHS_FIELD))
		if len(exactPaths) > effectiveMaxPaths:
			return toolError("limit_exceeded", "paths exceeds the deployment's limit of " + unicode(effectiveMaxPaths) + " items; split the batch or raise " + POLICY_MAX_PATHS_FIELD + " in the Runtime Target Policy.", {"reason": "pathsOverPolicyLimit", "requested": len(exactPaths), "limit": effectiveMaxPaths})
		if auditMode == "required" and not auditProfileAvailable(auditProfile):
			# D30 6: the required-mode audit profile is checked before anything is
			# executed and before any audit row is attempted.
			return toolError("operation_disabled", "The Runtime audit mode is required but the configured audit profile is unavailable; no path was unshelved.", {"reason": "auditProfileUnavailable", "auditProfile": boundedText(auditProfile, 128)})
		stage = "preflight"
		policyProblems = []
		for index in range(len(exactPaths)):
			path = exactPaths[index]
			if pathProvider(path).lower() == RESERVED_PROVIDER.lower():
				# Refused by provider before the allowlist is consulted, so an
				# explicit * cannot reach the policy document; the provider
				# component is the only thing compared, so an allowed provider is
				# never refused for a later segment's name.
				policyProblems.append({"index": index, "path": path, "reason": "reservedProvider", "code": "permission_denied"})
				continue
			if not matchesAllowlist(path, entries):
				policyProblems.append({"index": index, "path": path, "reason": "targetNotAllowlisted", "code": "permission_denied"})
		if policyProblems:
			# D18: a denied mutation is audited. The decision row is the only row
			# a denial produces, and `off` mode still records nothing.
			refusedText = ",".join([problem["path"] for problem in policyProblems])
			decisionRecorded = auditWrite("decision", refusedText, "outcome=denied code=permission_denied refused=" + unicode(len(policyProblems)) + " requested=" + unicode(len(exactPaths)))
			if auditMode == "required" and not decisionRecorded:
				return toolError("operation_disabled", "The Runtime audit mode is required but the denied-mutation record could not be written; no path was unshelved.", {"reason": "auditAttemptFailed", "phase": "decision"})
			return toolError("permission_denied", "Every target must be inside the Runtime Target Policy allowlist and outside the reserved policy provider; no path was unshelved.", {"reason": "preflightTargetRefused", "allowlistKey": ALLOWLIST_KEY, "items": policyProblems, "auditRecorded": decisionRecorded})
		stage = "audit_attempt"
		targetText = ",".join(exactPaths)
		attemptRecorded = auditWrite("attempt", targetText, "outcome=attempt requested=" + unicode(len(exactPaths)))
		if auditMode == "required" and not attemptRecorded:
			return toolError("operation_disabled", "The Runtime audit mode is required but the attempt record could not be written; no path was unshelved.", {"reason": "auditAttemptFailed"})
		stage = "dispatch"
		# D30 3: items execute one at a time with per-item outcomes and no
		# rollback. system.alarm.unshelve reports no per-item result, so one call
		# per item is what makes an item's outcome attributable at all.
		dispatched = True
		items = []
		executed = 0
		outcomeUnknown = 0
		for index in range(len(exactPaths)):
			path = exactPaths[index]
			try:
				system.alarm.unshelve([path])
				items.append({"path": path, "status": "executed"})
				executed += 1
			except (Exception, JavaException) as itemExc:
				logger.warn("correlationId=" + correlationId + " item=" + unicode(index) + " unshelve outcome is indeterminate: " + text(itemExc))
				items.append({"path": path, "status": "outcome_unknown"})
				outcomeUnknown += 1
		stage = "audit_result"
		resultRecorded = auditWrite("result", targetText, "outcome=executed executed=" + unicode(executed) + " outcomeUnknown=" + unicode(outcomeUnknown))
		auditRecorded = bool(attemptRecorded and resultRecorded)
		stage = "observed_read"
		observed = []
		observedBytes = 0
		observedBudgetSpent = False
		shelvedValues = None
		try:
			shelvedValues = system.alarm.getShelvedPaths()
		except (Exception, JavaException) as observedExc:
			logger.warn("correlationId=" + correlationId + " shelved-state read failed: " + text(observedExc))
			shelvedValues = None
		if shelvedValues is None:
			observed = [observedError(path, "upstream_error", "The observed shelved state could not be read.") for path in exactPaths]
		elif len(shelvedValues) > SHELVED_READ_LIMIT:
			observed = [observedError(path, "upstream_error", "The shelved Alarm state exceeds the bounded read limit of 500 entries.") for path in exactPaths]
		else:
			shelvedByPath = {}
			for value in shelvedValues:
				try:
					shelvedByPath[unicode(value.getPath())] = value
				except (Exception, JavaException) as itemExc:
					logger.warn("correlationId=" + correlationId + " shelved entry could not be read: " + text(itemExc))
			for path in exactPaths:
				value = shelvedByPath.get(path)
				if value is None:
					observed.append({"path": path, "status": "ok", "shelved": False})
					continue
				try:
					userText = jsonValue(value.getUser())
					expirationText = jsonValue(value.getExpiration())
					# D10: the Observed state bounds what it reports, and an entry it
					# cannot return is an explicit observed error, never truncation.
					problem = observedEntryProblem(userText, expirationText)
					if problem is None and observedBytes + utf8Bytes(userText) + utf8Bytes(expirationText) + 64 > OBSERVED_STATE_MAX_BYTES:
						problem = "The Observed state already holds " + unicode(observedBytes) + " bytes, so returning this entry would pass the " + unicode(OBSERVED_STATE_MAX_BYTES) + "-byte budget; it was not returned."
						observedBudgetSpent = True
					if problem is not None:
						observed.append(observedError(path, "limit_exceeded", problem))
						continue
					observedBytes += utf8Bytes(userText) + utf8Bytes(expirationText) + 64
					observed.append({"path": path, "status": "ok", "shelved": True, "user": userText, "expiration": expirationText, "expired": bool(value.isExpired())})
				except (Exception, JavaException) as itemExc:
					logger.error("correlationId=" + correlationId + " shelved entry serialization failed: " + text(itemExc))
					observed.append(observedError(path, "schema_mismatch", "The observed shelving record could not be represented."))
		stage = "serialization"
		# The per-item outcomes are established facts by now, so neither the
		# Observed-state budget nor a serializer failure may replace them with a
		# Tool error. The result is rendered with the full Observed state and, when
		# that cannot be returned, without it.
		domain, encoded = encodeDomain(observed)
		if encoded is None:
			domain, encoded = encodeDomain(omittedObserved(OBSERVED_OMITTED_SERIALIZATION))
		elif len(encoded.encode("utf-8")) > OUTPUT_MAX_BYTES:
			domain, encoded = encodeDomain(omittedObserved(OBSERVED_OMITTED_CEILING))
		if encoded is None:
			return toolError("upstream_error", "The Alarm unshelve completed but its structured result could not be serialized.", {"reason": "serializationFailure", "stage": stage, "requested": len(exactPaths), "executed": executed, "outcomeUnknown": outcomeUnknown, "auditRecorded": auditRecorded})
		payloadBytes = len(encoded.encode("utf-8"))
		if payloadBytes > OUTPUT_MAX_BYTES:
			# D10: over-budget states what was requested, the limit, and what did
			# execute, so a completed mutation is never silent.
			return toolError("limit_exceeded", "The structured result is " + unicode(payloadBytes) + " bytes, over the " + unicode(OUTPUT_MAX_BYTES) + "-byte output ceiling, even without the Observed state; unshelve fewer or shorter paths.", {"reason": "outputOverLimit", "requestedBytes": payloadBytes, "limitBytes": OUTPUT_MAX_BYTES, "requested": len(exactPaths), "executed": executed, "outcomeUnknown": outcomeUnknown, "auditRecorded": auditRecorded})
		return {"structuredContent": domain}
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " stage=" + stage + " alarm_unshelve failed: " + text(exc))
		if dispatched:
			# D08: never replay an uncertain mutation; the agent re-reads instead.
			return toolError("outcome_unknown", "The Alarm unshelve may have been dispatched but its outcome could not be established.", {"reason": "dispatchOutcomeUnknown", "stage": stage})
		return toolError("upstream_error", "The Alarm unshelve could not be completed.", {"reason": "handlerFailure", "stage": stage})
