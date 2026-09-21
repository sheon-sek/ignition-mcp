def onToolCalled(builder, writes, timeout):
	from java.lang import Boolean, Number, Exception as JavaException
	from java.util import UUID, Date, Map, List
	import math
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.TagWrite")

	# D30 1: the Runtime Target Policy is a deployment-owned document outside the
	# bundle. Ticket #6 characterized its storage and its bounded read: the small
	# companion length Tag is read first so an over-cap document is never
	# materialized, because a Tag value has no native size limit.
	POLICY_PROVIDER = "IgnitionMCPPolicy"
	POLICY_LENGTH_PATH = "[IgnitionMCPPolicy]RuntimeTargetPolicyLength"
	POLICY_PATH = "[IgnitionMCPPolicy]RuntimeTargetPolicy"
	POLICY_MAX_BYTES = 32768
	POLICY_READ_TIMEOUT_MS = 5000
	# D30 6: the Runtime plane cannot write the policy, and that is a product
	# rule because handler scope is not a security boundary (#6 evidence).
	RESERVED_PROVIDER = "IgnitionMCPPolicy"
	ALLOWLIST_KEY = "tag_write"
	WILDCARD = "*"
	AUDIT_ACTION = "ignition-mcp.tag_write"
	AUDIT_MODES = ("best_effort", "required", "off")
	DEFAULT_TIMEOUT_MS = 10000
	HARD_MAX_WRITES = 100
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
			raise TypeError("Native write result has no QualityCode")
		name = value.getName() if hasattr(value, "getName") else unicode(value)
		level = value.getLevel() if hasattr(value, "getLevel") else unicode(value)
		diagnostic = value.getDiagnosticMessage() if hasattr(value, "getDiagnosticMessage") else None
		return {"code": int(value.getCode()), "name": unicode(name), "level": unicode(level), "good": qualityIsGood(value), "diagnosticMessage": optionalText(diagnostic)}

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
		if hasattr(value, "getColumnCount") and hasattr(value, "getRowCount") and hasattr(value, "getValueAt"):
			columns = [unicode(value.getColumnName(c)) for c in range(value.getColumnCount())]
			rows = [[jsonValue(value.getValueAt(r, c)) for c in range(value.getColumnCount())] for r in range(value.getRowCount())]
			return {"columns": columns, "rows": rows}
		if isinstance(value, dict):
			return dict((unicode(k), jsonValue(v)) for k, v in value.items())
		if isinstance(value, Map):
			return dict((unicode(entry.getKey()), jsonValue(entry.getValue())) for entry in value.entrySet())
		if isinstance(value, (list, tuple, List)):
			return [jsonValue(child) for child in value]
		if hasattr(value, "getClass") and value.getClass().isArray():
			return [jsonValue(child) for child in value]
		raise TypeError("Unsupported Tag value type: " + unicode(type(value)))

	def validCurrentValuePath(value):
		if not isinstance(value, basestring) or not value.strip():
			return False
		value = value.strip()
		if not value.startswith("["):
			return False
		closing = value.find("]")
		if closing <= 1 or closing >= len(value) - 1:
			return False
		if value.startswith("[.]") or value.startswith("[~]") or value.startswith("[]"):
			return False
		body = value[closing + 1:]
		if "." in body or "[" in body or "]" in body:
			return False
		segments = [segment for segment in body.split("/") if segment]
		if "_types_" in segments:
			return False
		return True

	def providerName(value):
		if not isinstance(value, basestring) or not value.startswith("["):
			return ""
		closing = value.find("]")
		if closing <= 1:
			return ""
		return value[1:closing]

	def isScalar(value):
		if value is None:
			return False
		if isinstance(value, (bool, int, long, float, basestring)):
			return True
		if isinstance(value, (Boolean, Number)):
			return True
		return False

	def valueProblem(value):
		# D30 6: scalars and arrays of scalars only; Dataset and Document fail.
		if isScalar(value):
			return None
		if isinstance(value, (list, tuple, List)):
			for child in value:
				if not isScalar(child):
					return "arrayValueElements"
			return None
		if value is None:
			return "nullValue"
		if isinstance(value, (dict, Map)):
			return "documentValue"
		if hasattr(value, "getColumnCount") and hasattr(value, "getRowCount") and hasattr(value, "getValueAt"):
			return "datasetValue"
		return "unsupportedValueType"

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
			if entry == "" or " " in entry or providerName(entry) == "" or not validCurrentValuePath(entry):
				return None
			normalized.append(entry)
		return normalized

	def matchesAllowlist(path, entries):
		for entry in entries:
			if entry == WILDCARD:
				return True
			if path == entry or path.startswith(entry + "/"):
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
		if not isinstance(writes, (list, tuple, List)) or len(writes) == 0:
			return toolError("invalid_argument", "writes must be a non-empty array of {path, value} items.", {"reason": "writesNotAnArray"})
		if len(writes) > HARD_MAX_WRITES:
			return toolError("limit_exceeded", "writes exceeds the hard limit of 100 items.", {"reason": "writesOverHardLimit", "requested": len(writes), "limit": HARD_MAX_WRITES})
		if timeout is None:
			timeout = DEFAULT_TIMEOUT_MS
		if isinstance(timeout, bool) or not isinstance(timeout, (int, long)) or timeout < 0 or timeout > 30000:
			return toolError("invalid_argument", "timeout must be an integer from 0 to 30000 milliseconds.", {"reason": "timeoutOutOfRange"})
		paths = []
		values = []
		inputProblems = []
		for index in range(len(writes)):
			item = writes[index]
			if not isinstance(item, dict):
				inputProblems.append({"index": index, "reason": "itemNotAnObject"})
				continue
			keys = [text(key) for key in item.keys()]
			pathValue = item.get("path")
			if "path" not in keys or "value" not in keys or len(keys) != 2:
				inputProblems.append({"index": index, "path": boundedText(pathValue, 256), "reason": "itemKeysMustBePathAndValue"})
				continue
			if not validCurrentValuePath(pathValue):
				inputProblems.append({"index": index, "path": boundedText(pathValue, 256), "reason": "pathNotACurrentValuePath"})
				continue
			problem = valueProblem(item.get("value"))
			if problem is not None:
				inputProblems.append({"index": index, "path": boundedText(pathValue, 256), "reason": problem})
				continue
			paths.append(unicode(pathValue).strip())
			values.append(item.get("value"))
		if inputProblems:
			return toolError("invalid_argument", "Every write item must carry an absolute provider-qualified current Tag path and a scalar or scalar-array value; no item was executed.", {"reason": "preflightInputFailed", "items": inputProblems})
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
		stage = "preflight"
		policyProblems = []
		for index in range(len(paths)):
			path = paths[index]
			if providerName(path).lower() == RESERVED_PROVIDER.lower():
				# Refused by provider, before the allowlist is consulted, so an
				# explicit * cannot reach the policy document.
				policyProblems.append({"index": index, "path": path, "reason": "reservedProvider", "code": "permission_denied"})
				continue
			if not matchesAllowlist(path, entries):
				policyProblems.append({"index": index, "path": path, "reason": "targetNotAllowlisted", "code": "permission_denied"})
		if policyProblems:
			return toolError("permission_denied", "Every write target must be inside the Runtime Target Policy allowlist and outside the reserved policy provider; no item was executed.", {"reason": "preflightTargetRefused", "allowlistKey": ALLOWLIST_KEY, "items": policyProblems})
		if auditMode == "required" and not auditProfileAvailable(auditProfile):
			return toolError("operation_disabled", "The Runtime audit mode is required but the configured audit profile is unavailable; no item was executed.", {"reason": "auditProfileUnavailable", "auditProfile": boundedText(auditProfile, 128)})
		stage = "audit_attempt"
		targetText = ",".join(paths)
		attemptRecorded = auditWrite("attempt", targetText, "outcome=attempt requested=" + unicode(len(paths)))
		if auditMode == "required" and not attemptRecorded:
			return toolError("operation_disabled", "The Runtime audit mode is required but the attempt record could not be written; no item was executed.", {"reason": "auditAttemptFailed"})
		stage = "dispatch"
		dispatched = True
		codes = system.tag.writeBlocking(paths, values, int(timeout))
		stage = "outcome"
		if codes is None or len(codes) != len(paths):
			# The per-item outcome cannot be attributed positionally, so the whole
			# batch is indeterminate and is never replayed automatically (D08).
			resultRecorded = auditWrite("result", targetText, "outcome=outcome_unknown requested=" + unicode(len(paths)))
			return toolError("outcome_unknown", "The Tag write may have been dispatched but its per-item Native outcome could not be established.", {"reason": "nativeOutcomeUnavailable", "requested": len(paths), "returned": 0 if codes is None else len(codes), "auditRecorded": bool(attemptRecorded and resultRecorded)})
		items = []
		succeeded = 0
		outcomeUnknown = 0
		for index in range(len(paths)):
			# D30 6: only an item whose own Native outcome is indeterminate is
			# reported as outcome_unknown.
			try:
				code = codes[index]
				if code is None:
					raise TypeError("Native write result is null")
				items.append({"path": paths[index], "status": "executed", "quality": quality(code)})
				if qualityIsGood(code):
					succeeded += 1
			except (Exception, JavaException) as itemExc:
				logger.warn("correlationId=" + correlationId + " item=" + unicode(index) + " native outcome is indeterminate: " + text(itemExc))
				items.append({"path": paths[index], "status": "outcome_unknown"})
				outcomeUnknown += 1
		failed = len(paths) - succeeded - outcomeUnknown
		stage = "audit_result"
		resultRecorded = auditWrite("result", targetText, "outcome=executed succeeded=" + unicode(succeeded) + " failed=" + unicode(failed) + " outcomeUnknown=" + unicode(outcomeUnknown))
		auditRecorded = bool(attemptRecorded and resultRecorded)
		stage = "observed_read"
		observed = []
		try:
			observedReads = system.tag.readBlocking(paths, int(timeout))
		except (Exception, JavaException) as observedExc:
			logger.warn("correlationId=" + correlationId + " observed read failed: " + text(observedExc))
			observedReads = None
		if observedReads is None or len(observedReads) != len(paths):
			observed = [{"path": paths[index], "status": "error", "error": {"code": "upstream_error", "message": "The observed state could not be read.", "correlationId": correlationId}} for index in range(len(paths))]
		else:
			for index in range(len(paths)):
				try:
					value = observedReads[index]
					if value is None or not (hasattr(value, "getValue") and hasattr(value, "getQuality") and hasattr(value, "getTimestamp")):
						raise TypeError("Native item is not a QualifiedValue")
					observed.append({"path": paths[index], "status": "ok", "value": jsonValue(value.getValue()), "quality": quality(value.getQuality()), "timestamp": jsonValue(value.getTimestamp())})
				except (Exception, JavaException) as itemExc:
					logger.error("correlationId=" + correlationId + " observed item serialization failed: " + text(itemExc))
					observed.append({"path": paths[index], "status": "error", "error": {"code": "schema_mismatch", "message": "The observed Tag item could not be represented.", "correlationId": correlationId}})
		stage = "serialization"
		domain = {
			"items": items,
			"observed": observed,
			"summary": {"requested": len(paths), "succeeded": succeeded, "failed": failed, "outcomeUnknown": outcomeUnknown, "auditMode": auditMode, "auditRecorded": auditRecorded},
			"meta": {"correlationId": correlationId},
		}
		domain = encodeNulls(domain)
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > OUTPUT_MAX_BYTES:
			return toolError("limit_exceeded", "Structured output exceeds the 256 KiB default limit; write to fewer paths or use narrower values.", {"reason": "outputOverLimit"})
		return {"structuredContent": domain}
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " stage=" + stage + " tag_write failed: " + text(exc))
		if dispatched:
			# D08: never replay an uncertain mutation; the agent re-reads instead.
			return toolError("outcome_unknown", "The Tag write may have been dispatched but its outcome could not be established.", {"reason": "dispatchOutcomeUnknown", "stage": stage})
		return toolError("upstream_error", "The Tag write could not be completed.", {"reason": "handlerFailure", "stage": stage})
