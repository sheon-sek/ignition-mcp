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
	# D10 budgets: the project safe default is 20 writes, the deployment may raise
	# it through the Runtime Target Policy up to the 100-write hard ceiling, and
	# every request is bounded by path, value and array-element ceilings plus one
	# finite aggregate input-byte budget.
	DEFAULT_MAX_WRITES = 20
	HARD_MAX_WRITES = 100
	POLICY_MAX_WRITES_FIELD = "tagWriteMaxWrites"
	PATH_MAX_BYTES = 2048
	VALUE_STRING_MAX_BYTES = 16384
	ARRAY_MAX_ELEMENTS = 1000
	INPUT_MAX_BYTES = 65536
	NUMERIC_INPUT_BYTES = 32
	# D10 output: the Observed state carries its own budget, so a value it cannot
	# return never becomes the reason a completed write's outcomes disappear.
	OBSERVED_VALUE_MAX_BYTES = 8192
	OBSERVED_STATE_MAX_BYTES = 65536
	OBSERVED_DATASET_MAX_CELLS = 2000
	# D10 output: a Native outcome's text is provider data with no size of its own.
	# The identifiers an outcome is matched on stay exact; the free-text diagnostic
	# is bounded and marked, so a verbose provider cannot make the per-item outcomes
	# impossible to return.
	QUALITY_NAME_MAX_BYTES = 128
	QUALITY_LEVEL_MAX_BYTES = 128
	QUALITY_DIAGNOSTIC_MAX_BYTES = 512
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
		diagnosticText = optionalText(diagnostic)
		diagnosticBytes = None if diagnosticText is None else utf8Bytes(diagnosticText)
		# D10: the provider's QualityCode text has no size of its own, and the
		# per-item outcomes are what a caller acts on, so the free text is bounded
		# before the item is built. The identifiers stay exact, the diagnostic keeps
		# a bounded prefix, and an over-limit diagnostic states the size it had - the
		# marker is present exactly when the text was bounded, so nothing about the
		# outcome is silent.
		rendered = {"code": int(value.getCode()), "name": boundedQualityText(name, QUALITY_NAME_MAX_BYTES), "level": boundedQualityText(level, QUALITY_LEVEL_MAX_BYTES), "good": qualityIsGood(value), "diagnosticMessage": None if diagnosticText is None else boundedQualityText(diagnosticText, QUALITY_DIAGNOSTIC_MAX_BYTES)}
		if diagnosticBytes is not None and diagnosticBytes > QUALITY_DIAGNOSTIC_MAX_BYTES:
			rendered["diagnosticMessageOverLimitBytes"] = diagnosticBytes
		return rendered

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

	def utf8Bytes(value):
		return len(text(value).encode("utf-8"))

	def boundedQualityText(value, limit):
		# A bounded prefix cut on a character boundary, so a UTF-8 character is never
		# split and the bytes the marker reports match the text that is returned.
		rendered = text(value)
		if utf8Bytes(rendered) <= limit:
			return rendered
		rendered = rendered[:limit]
		while rendered and utf8Bytes(rendered) > limit:
			rendered = rendered[:-1]
		return rendered

	def datasetCellBytes(value, limit):
		# Walk the Dataset's cells with an early exit: a cell can hold an arbitrarily
		# large string, so measuring by reading the value would defeat the budget the
		# measurement exists to enforce. Nothing here materializes a cell, and each
		# cell carries a small fixed allowance for the JSON punctuation and separators
		# a Dataset representation adds around it.
		total = 0
		for row in range(int(value.getRowCount())):
			for column in range(int(value.getColumnCount())):
				total += 4 + valueBytesBounded(value.getValueAt(row, column), limit)
				if total > limit:
					return total
		return total

	def scalarInputBytes(value):
		if isinstance(value, basestring):
			return utf8Bytes(value)
		return NUMERIC_INPUT_BYTES

	def inputBytes(value):
		if isinstance(value, (list, tuple, List)):
			total = 2
			for child in value:
				total += scalarInputBytes(child)
			return total
		return scalarInputBytes(value)

	def inputLimitProblem(path, value):
		# D10 input ceilings. Every one of them is pure validation over the
		# request, so an over-budget batch is refused before any native call.
		pathBytes = utf8Bytes(path)
		if pathBytes > PATH_MAX_BYTES:
			return ("pathOverLength", pathBytes, PATH_MAX_BYTES)
		if isinstance(value, (list, tuple, List)):
			if len(value) > ARRAY_MAX_ELEMENTS:
				return ("arrayElementsOverLimit", len(value), ARRAY_MAX_ELEMENTS)
			for child in value:
				if isinstance(child, basestring):
					childBytes = utf8Bytes(child)
					if childBytes > VALUE_STRING_MAX_BYTES:
						return ("stringValueOverLimit", childBytes, VALUE_STRING_MAX_BYTES)
			return None
		if isinstance(value, basestring):
			valueBytes = utf8Bytes(value)
			if valueBytes > VALUE_STRING_MAX_BYTES:
				return ("stringValueOverLimit", valueBytes, VALUE_STRING_MAX_BYTES)
		return None

	def valueBytesBounded(value, limit):
		# A structural size with an early exit, so measuring an Observed value the
		# provider returned cannot itself materialize or walk an unbounded value.
		if value is None:
			return 4
		if isinstance(value, basestring):
			return utf8Bytes(value)
		if isinstance(value, (bool, Boolean)):
			return 5
		if isinstance(value, (int, long, float, Number)):
			return 24
		if hasattr(value, "getColumnCount") and hasattr(value, "getRowCount") and hasattr(value, "getValueAt"):
			return datasetCellBytes(value, limit)
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
				total += utf8Bytes(entry.getKey()) + valueBytesBounded(entry.getValue(), limit)
				if total > limit:
					return total
			return total
		if isinstance(value, dict):
			total = 2
			for key in value:
				total += utf8Bytes(key) + valueBytesBounded(value[key], limit)
				if total > limit:
					return total
			return total
		return 64

	def observedValueProblem(value):
		# The per-value half of the Observed-state budget. An over-budget value is
		# reported as an explicit observed error, never truncated silently.
		if isinstance(value, basestring):
			size = utf8Bytes(value)
			if size > OBSERVED_VALUE_MAX_BYTES:
				return "The observed value is " + unicode(size) + " bytes, over the " + unicode(OBSERVED_VALUE_MAX_BYTES) + "-byte Observed-state value budget; it was not returned."
			return None
		if hasattr(value, "getColumnCount") and hasattr(value, "getRowCount") and hasattr(value, "getValueAt"):
			cells = int(value.getRowCount()) * int(value.getColumnCount())
			if cells > OBSERVED_DATASET_MAX_CELLS:
				return "The observed Dataset is " + unicode(cells) + " cells, over the " + unicode(OBSERVED_DATASET_MAX_CELLS) + "-cell Observed-state budget; it was not returned."
			size = datasetCellBytes(value, OBSERVED_VALUE_MAX_BYTES)
			if size > OBSERVED_VALUE_MAX_BYTES:
				return "The observed Dataset is " + unicode(size) + " bytes, over the " + unicode(OBSERVED_VALUE_MAX_BYTES) + "-byte Observed-state value budget; it was not returned."
			return None
		if not isinstance(value, (bool, Boolean, int, long, float, Number)):
			size = valueBytesBounded(value, OBSERVED_VALUE_MAX_BYTES)
			if size > OBSERVED_VALUE_MAX_BYTES:
				return "The observed value is " + unicode(size) + " bytes, over the " + unicode(OBSERVED_VALUE_MAX_BYTES) + "-byte Observed-state value budget; it was not returned."
		return None

	def observedError(path, code, message):
		return {"path": path, "status": "error", "error": {"code": code, "message": message, "correlationId": correlationId}}

	def omittedObserved(reason):
		return [observedError(paths[index], "schema_mismatch", reason) for index in range(len(paths))]

	OBSERVED_OMITTED_SERIALIZATION = "The Observed state was omitted because the structured result could not be serialized; the per-item outcomes above are complete."
	OBSERVED_OMITTED_CEILING = "The Observed state was omitted to keep the structured result inside the 256 KiB output ceiling; the per-item outcomes above are complete."

	def buildDomain(observedEntries):
		domain = {
			"items": items,
			"observed": observedEntries,
			"summary": {"requested": len(paths), "succeeded": succeeded, "failed": failed, "outcomeUnknown": outcomeUnknown, "auditMode": auditMode, "auditRecorded": auditRecorded},
			"meta": {"correlationId": correlationId},
		}
		return encodeNulls(domain)

	def encodeDomain(observedEntries):
		# The serializer is not allowed to decide a mutation's result: a failure
		# here is caught so the per-item Native outcomes still reach the caller.
		try:
			domain = buildDomain(observedEntries)
			return (domain, system.util.jsonEncode(domain))
		except (Exception, JavaException) as encodeExc:
			logger.warn("correlationId=" + correlationId + " structured result serialization failed: " + text(encodeExc))
			return (None, None)

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
		if hasKey(document, POLICY_MAX_WRITES_FIELD):
			# D10: the deployment may raise the 20-write default, never above the
			# 100-write hard ceiling.
			maxWrites = document.get(POLICY_MAX_WRITES_FIELD)
			if isinstance(maxWrites, bool) or not isinstance(maxWrites, (int, long)) or maxWrites < 1 or maxWrites > HARD_MAX_WRITES:
				return "policyTagWriteMaxWrites"
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
		# D10 input ceilings: pure validation over the request, so an over-budget
		# batch never reaches the policy read, let alone the Gateway.
		totalInputBytes = 0
		for index in range(len(paths)):
			problem = inputLimitProblem(paths[index], values[index])
			if problem is not None:
				return toolError("limit_exceeded", "A write item is over a documented D10 input ceiling; no item was executed.", {"reason": problem[0], "index": index, "path": boundedText(paths[index], 256), "requested": problem[1], "limit": problem[2]})
			totalInputBytes += utf8Bytes(paths[index]) + inputBytes(values[index])
		if totalInputBytes > INPUT_MAX_BYTES:
			return toolError("limit_exceeded", "The write batch is " + unicode(totalInputBytes) + " bytes, over the " + unicode(INPUT_MAX_BYTES) + "-byte input budget; split it across calls.", {"reason": "inputOverByteBudget", "requested": totalInputBytes, "limit": INPUT_MAX_BYTES})
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
		# D10: a usable Policy is what raises the 20-write project default, and the
		# validation above keeps its value inside the 100-write hard ceiling.
		effectiveMaxWrites = DEFAULT_MAX_WRITES
		if hasKey(policy, POLICY_MAX_WRITES_FIELD):
			effectiveMaxWrites = int(policy.get(POLICY_MAX_WRITES_FIELD))
		if len(paths) > effectiveMaxWrites:
			return toolError("limit_exceeded", "writes exceeds the deployment's limit of " + unicode(effectiveMaxWrites) + " items; split the batch or raise " + POLICY_MAX_WRITES_FIELD + " in the Runtime Target Policy.", {"reason": "writesOverPolicyLimit", "requested": len(paths), "limit": effectiveMaxWrites})
		if auditMode == "required" and not auditProfileAvailable(auditProfile):
			# D30 6: the required-mode audit profile is checked before anything is
			# executed and before any audit row is attempted.
			return toolError("operation_disabled", "The Runtime audit mode is required but the configured audit profile is unavailable; no item was executed.", {"reason": "auditProfileUnavailable", "auditProfile": boundedText(auditProfile, 128)})
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
			# D18: a denied mutation is audited. The decision row is the only row
			# a denial produces, and `off` mode still records nothing.
			refusedText = ",".join([problem["path"] for problem in policyProblems])
			decisionRecorded = auditWrite("decision", refusedText, "outcome=denied code=permission_denied refused=" + unicode(len(policyProblems)) + " requested=" + unicode(len(paths)))
			if auditMode == "required" and not decisionRecorded:
				return toolError("operation_disabled", "The Runtime audit mode is required but the denied-mutation record could not be written; no item was executed.", {"reason": "auditAttemptFailed", "phase": "decision"})
			return toolError("permission_denied", "Every write target must be inside the Runtime Target Policy allowlist and outside the reserved policy provider; no item was executed.", {"reason": "preflightTargetRefused", "allowlistKey": ALLOWLIST_KEY, "items": policyProblems, "auditRecorded": decisionRecorded})
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
		observedBytes = 0
		observedBudgetSpent = False
		try:
			observedReads = system.tag.readBlocking(paths, int(timeout))
		except (Exception, JavaException) as observedExc:
			logger.warn("correlationId=" + correlationId + " observed read failed: " + text(observedExc))
			observedReads = None
		if observedReads is None or len(observedReads) != len(paths):
			observed = [observedError(paths[index], "upstream_error", "The observed state could not be read.") for index in range(len(paths))]
		else:
			for index in range(len(paths)):
				path = paths[index]
				try:
					value = observedReads[index]
					if value is None or not (hasattr(value, "getValue") and hasattr(value, "getQuality") and hasattr(value, "getTimestamp")):
						raise TypeError("Native item is not a QualifiedValue")
					rawValue = value.getValue()
					# D10: the Observed state carries its own budget, checked before
					# the value is materialized for the result. An over-budget value
					# is reported as an explicit observed error, never truncated.
					problem = observedValueProblem(rawValue)
					if problem is None and observedBudgetSpent:
						problem = "The Observed-state budget of " + unicode(OBSERVED_STATE_MAX_BYTES) + " bytes is already spent; this value was not returned."
					if problem is None and observedBytes + valueBytesBounded(rawValue, OBSERVED_VALUE_MAX_BYTES) > OBSERVED_STATE_MAX_BYTES:
						problem = "The Observed state already holds " + unicode(observedBytes) + " bytes, so returning this value would pass the " + unicode(OBSERVED_STATE_MAX_BYTES) + "-byte budget; it was not returned."
						observedBudgetSpent = True
					if problem is not None:
						observed.append(observedError(path, "limit_exceeded", problem))
						continue
					rendered = jsonValue(rawValue)
					observedBytes += valueBytesBounded(rawValue, OBSERVED_VALUE_MAX_BYTES)
					observed.append({"path": path, "status": "ok", "value": rendered, "quality": quality(value.getQuality()), "timestamp": jsonValue(value.getTimestamp())})
				except (Exception, JavaException) as itemExc:
					logger.error("correlationId=" + correlationId + " observed item serialization failed: " + text(itemExc))
					observed.append(observedError(path, "schema_mismatch", "The observed Tag item could not be represented."))
		stage = "serialization"
		# The per-item Native outcomes are established facts by now, so neither an
		# over-budget Observed state nor a serializer failure may replace them with
		# a Tool error. The result is rendered with the full Observed state and,
		# when that cannot be returned, without it.
		domain, encoded = encodeDomain(observed)
		if encoded is None:
			domain, encoded = encodeDomain(omittedObserved(OBSERVED_OMITTED_SERIALIZATION))
		elif len(encoded.encode("utf-8")) > OUTPUT_MAX_BYTES:
			domain, encoded = encodeDomain(omittedObserved(OBSERVED_OMITTED_CEILING))
		if encoded is None:
			return toolError("upstream_error", "The Tag write completed but its structured result could not be serialized.", {"reason": "serializationFailure", "stage": stage, "requested": len(paths), "succeeded": succeeded, "failed": failed, "outcomeUnknown": outcomeUnknown, "auditRecorded": auditRecorded})
		payloadBytes = len(encoded.encode("utf-8"))
		if payloadBytes > OUTPUT_MAX_BYTES:
			# D10: over-budget states what was requested, the limit, and what did
			# execute, so a completed write is never silent.
			return toolError("limit_exceeded", "The structured result is " + unicode(payloadBytes) + " bytes, over the " + unicode(OUTPUT_MAX_BYTES) + "-byte output ceiling, even without the Observed state; write to fewer or shorter paths.", {"reason": "outputOverLimit", "requestedBytes": payloadBytes, "limitBytes": OUTPUT_MAX_BYTES, "requested": len(paths), "succeeded": succeeded, "failed": failed, "outcomeUnknown": outcomeUnknown, "auditRecorded": auditRecorded})
		return {"structuredContent": domain}
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " stage=" + stage + " tag_write failed: " + text(exc))
		if dispatched:
			# D08: never replay an uncertain mutation; the agent re-reads instead.
			return toolError("outcome_unknown", "The Tag write may have been dispatched but its outcome could not be established.", {"reason": "dispatchOutcomeUnknown", "stage": stage})
		return toolError("upstream_error", "The Tag write could not be completed.", {"reason": "handlerFailure", "stage": stage})
