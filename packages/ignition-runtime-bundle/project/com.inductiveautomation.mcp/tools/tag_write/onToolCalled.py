def onToolCalled(builder, writes, timeout):
	from java.lang import Boolean, Number, Exception as JavaException
	from java.util import UUID, Date, Map, List
	import array
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
	# D10 input ceilings are pure validation over the request, and every count stops at
	# the ceiling or the aggregate budget it is checked against, so a reported amount is
	# a lower bound and every refusal says so.
	INPUT_CEILING_MESSAGE = "A write item is over the documented input ceiling; a reported byte amount is counted only up to that ceiling, so it is a lower bound. No item was executed."
	# D10 output: the Observed state carries its own budget, so a value it cannot
	# return never becomes the reason a completed write's outcomes disappear.
	OBSERVED_VALUE_MAX_BYTES = 8192
	OBSERVED_STATE_MAX_BYTES = 65536
	OBSERVED_DATASET_MAX_CELLS = 2000
	# How deep the Observed walk follows a value before it refuses it. A consumed
	# Document or nested array is legitimate, but a pathologically deep one must
	# reach the structured Observed budget error instead of exhausting the
	# interpreter stack while it is measured and then materialized.
	OBSERVED_MAX_DEPTH = 16
	# D10 output: a Native outcome's text is provider data with no size of its own.
	# An identifier is returned exactly or not at all, because a truncated one would
	# assert an identifier the provider never reported; the free-text diagnostic is
	# returned as a bounded prefix and marked.
	QUALITY_NAME_MAX_BYTES = 128
	QUALITY_LEVEL_MAX_BYTES = 128
	QUALITY_DIAGNOSTIC_MAX_BYTES = 512
	# The budget a quality text's size marker is *counted* with: the count is exact
	# for any text within it - well beyond a real provider's message - and stops
	# there otherwise, so the marker costs bounded work and is a lower bound when the
	# counter clamped.
	QUALITY_TEXT_COUNT_BYTES = 8192
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

	def exactQualityIdentifier(value, limit):
		# An identifier is returned exactly or not at all: a truncated one would
		# assert an identifier the provider never reported, which D10 forbids. The
		# count is bounded, so the reported size is exact for any realistic identifier
		# and a lower bound when the counter clamped.
		rendered = text(value)
		counted = utf8BytesBounded(rendered, limit)
		if counted > limit:
			return (None, utf8BytesBounded(rendered, QUALITY_TEXT_COUNT_BYTES))
		return (rendered, None)

	def quality(value):
		if value is None:
			raise TypeError("Native write result has no QualityCode")
		name = value.getName() if hasattr(value, "getName") else unicode(value)
		level = value.getLevel() if hasattr(value, "getLevel") else unicode(value)
		diagnostic = value.getDiagnosticMessage() if hasattr(value, "getDiagnosticMessage") else None
		diagnosticText = optionalText(diagnostic)
		# D10: the provider's QualityCode text has no size of its own, and the
		# per-item outcomes are what a caller acts on, so every text is bounded before
		# the item is built. `code` and `good` are always exact; a `name` or `level`
		# over its ceiling is omitted and its size reported rather than truncated; the
		# free-text diagnostic keeps a bounded prefix; and each marker is present
		# exactly when its text was bounded, so nothing about the outcome is silent.
		rendered = {"code": int(value.getCode()), "good": qualityIsGood(value)}
		rendered["name"], nameOverLimit = exactQualityIdentifier(name, QUALITY_NAME_MAX_BYTES)
		rendered["level"], levelOverLimit = exactQualityIdentifier(level, QUALITY_LEVEL_MAX_BYTES)
		if diagnosticText is None:
			rendered["diagnosticMessage"] = None
		else:
			diagnosticCounted = utf8BytesBounded(diagnosticText, QUALITY_DIAGNOSTIC_MAX_BYTES)
			rendered["diagnosticMessage"] = boundedPrefix(diagnosticText, QUALITY_DIAGNOSTIC_MAX_BYTES)
			if diagnosticCounted > QUALITY_DIAGNOSTIC_MAX_BYTES:
				rendered["diagnosticMessageOverLimitBytes"] = utf8BytesBounded(diagnosticText, QUALITY_TEXT_COUNT_BYTES)
		if nameOverLimit is not None:
			rendered["nameOverLimitBytes"] = nameOverLimit
		if levelOverLimit is not None:
			rendered["levelOverLimitBytes"] = levelOverLimit
		return rendered

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

	def utf8BytesBounded(value, budget):
		# The UTF-8 length of `value` counted one character at a time, so a value is
		# never encoded just to be measured: the count stops as soon as the budget is
		# spent, and the returned number is then a lower bound (at most one character
		# past the budget). A character costs 1, 2, 3 or 4 bytes by its code point.
		total = 0
		for character in text(value):
			code = ord(character)
			if code < 128:
				total += 1
			elif code < 2048:
				total += 2
			elif code < 65536:
				total += 3
			else:
				total += 4
			if total > budget:
				return total
		return total

	def boundedPrefix(value, limit):
		# A prefix of `value` costing at most `limit` UTF-8 bytes, counted per
		# character and cut on a character boundary, so the text is never encoded in
		# full to be cut and the prefix is well formed.
		rendered = text(value)
		total = 0
		index = 0
		for character in rendered:
			code = ord(character)
			if code < 128:
				cost = 1
			elif code < 2048:
				cost = 2
			elif code < 65536:
				cost = 3
			else:
				cost = 4
			if total + cost > limit:
				break
			total += cost
			index += 1
		return rendered[:index]

	def fits(budget, cost, rendered):
		# The walk's one decision: a cost against the budget left for it. A value that
		# does not fit is refused by its counted cost, and nothing is converted to find
		# out.
		if cost > budget:
			return ("bytes", cost, None)
		return (None, cost, rendered)

	def integerTextCost(value):
		# An upper bound on the decimal text an integer is emitted as, taken from its
		# width rather than from its digits, so a wide number is refused without being
		# converted to be measured. Jython's own long carries `bit_length`, a Java
		# BigInteger carries `bitLength`, and any remaining integral Number is inside 64
		# bits. One digit per 3.32 bits plus a sign is all a JSON encoder can write.
		if hasattr(value, "bit_length"):
			bits = value.bit_length()
		elif hasattr(value, "bitLength"):
			bits = value.bitLength()
		else:
			bits = 64
		cost = (bits * 30103) // 100000 + 2
		if hasattr(value, "signum"):
			negative = value.signum() < 0
		else:
			negative = value < 0
		if negative:
			cost += 1
		return cost

	def decimalTextCost(value):
		# The same bound for a Java BigDecimal, whose emitted shape wraps its unscaled
		# digits, a sign and the scale's exponent. The unscaled value is read as a width,
		# never as the decimal text.
		scale = value.scale()
		cost = 28 + integerTextCost(value.unscaledValue())
		if scale != 0:
			cost += len(unicode(abs(scale)))
		return cost

	def jsonTextCost(value, budget):
		# The emitted JSON cost of a string: its two quotes plus every character, counted
		# one character at a time so a provider text is never encoded to be measured. The
		# quote and the backslash take a two-character escape, a control character takes a
		# two- or a six-character one, and a non-ASCII character is charged the six bytes
		# an escaping encoder needs for it, which keeps the count an upper bound whatever
		# `system.util.jsonEncode` does. The count stops as soon as the budget is spent, so
		# the number is then a lower bound (at most one character past the budget).
		total = 2
		if total > budget:
			return total
		for character in text(value):
			code = ord(character)
			if character == '"' or character == '\\':
				total += 2
			elif code < 32:
				if code == 8 or code == 9 or code == 10 or code == 12 or code == 13:
					total += 2
				else:
					total += 6
			elif code < 128:
				total += 1
			else:
				total += 6
			if total > budget:
				return total
		return total

	def boundedSequence(values, budget, depth):
		# A bounded walk over an array-like value's members: each member pays one byte of
		# JSON punctuation before its own cost, so a wide list whose members are free (a
		# list of empty strings) still crosses the budget instead of being copied in full.
		# A `list`, a `tuple`, a Java `List`, a Java array - which Jython exposes as an
		# `array.array` - and a Java object that reports an array class are all accepted
		# shapes, and each is read lazily, so the walk stops at the first member that does
		# not fit.
		total = 2
		rendered = []
		for child in values:
			total += 1
			if total > budget:
				return ("bytes", total, None)
			reason, cost, childRendered = boundedValue(child, budget - total, depth - 1)
			total += cost
			if reason is not None:
				return (reason, total, None)
			rendered.append(childRendered)
		return (None, total, rendered)

	def boundedMapping(value, budget, depth):
		# The same walk for an object-like value: a `dict` or a Java `Map`. Its keys are
		# part of the emitted JSON, so they are counted like any other text, and neither
		# shape is copied before it is measured.
		javaMap = isinstance(value, Map)
		entries = value.entrySet() if javaMap else value
		total = 2
		rendered = {}
		for entry in entries:
			total += 1
			if total > budget:
				return ("bytes", total, None)
			if javaMap:
				key = entry.getKey()
				child = entry.getValue()
			else:
				key = entry
				child = value[entry]
			total += jsonTextCost(key, budget - total)
			if total > budget:
				return ("bytes", total, None)
			reason, cost, childRendered = boundedValue(child, budget - total, depth - 1)
			total += cost
			if reason is not None:
				return (reason, total, None)
			rendered[unicode(key)] = childRendered
		return (None, total, rendered)

	def boundedDataset(value, budget, depth):
		# A Dataset carries its column names as well as its cells, so both are counted: a
		# tiny cell under a very large name is exactly the shape a cell-only estimate lets
		# through. The cell count is checked first, so a wide Dataset is refused in
		# constant time instead of being walked, and every column, row and cell pays its
		# punctuation before its own cost.
		columns = int(value.getColumnCount())
		rows = int(value.getRowCount())
		cells = columns * rows
		if cells > OBSERVED_DATASET_MAX_CELLS:
			return ("cells", cells, None)
		total = 16
		columnNames = []
		for column in range(columns):
			total += 1
			if total > budget:
				return ("bytes", total, None)
			name = value.getColumnName(column)
			total += jsonTextCost(name, budget - total)
			if total > budget:
				return ("bytes", total, None)
			columnNames.append(unicode(name))
		renderedRows = []
		for row in range(rows):
			total += 2
			if total > budget:
				return ("bytes", total, None)
			rowValues = []
			for column in range(columns):
				total += 1
				if total > budget:
					return ("bytes", total, None)
				reason, cost, cell = boundedValue(value.getValueAt(row, column), budget - total, depth - 1)
				total += cost
				if reason is not None:
					return (reason, total, None)
				rowValues.append(cell)
			renderedRows.append(rowValues)
		return (None, total, {"columns": columnNames, "rows": renderedRows})

	def boundedValue(value, budget, depth):
		# The single Observed-state walk: it both measures `value` against `budget` and
		# renders it, so what is measured and what is returned cannot diverge. Every branch
		# below mirrors a shape a Tag value can carry, every collection member pays
		# punctuation before its own cost, and no leaf is converted before its cost is
		# inside the budget - a wide list, a Java array, an arbitrarily wide integer or a
		# large text is refused by its counted size instead of being copied to be measured.
		# Returns (reason, cost, rendered), with reason None when the value fits.
		if depth <= 0:
			return ("depth", budget + 1, None)
		if value is None:
			return fits(budget, 20, None)
		if isinstance(value, bool):
			return fits(budget, 5, value)
		if isinstance(value, Boolean):
			return fits(budget, 5, value.booleanValue())
		if isinstance(value, basestring):
			cost = jsonTextCost(value, budget)
			if cost > budget:
				return ("bytes", cost, None)
			return (None, cost, value)
		if isinstance(value, (int, long)):
			cost = integerTextCost(value)
			if cost > budget:
				return ("number", cost, None)
			return (None, cost, value)
		if isinstance(value, float):
			if math.isnan(value) or math.isinf(value):
				return fits(budget, 48, {"type": "non-finite-number", "text": unicode(value)})
			return fits(budget, 32, value)
		if isinstance(value, Number):
			typeName = unicode(value.getClass().getName())
			if typeName in ("java.lang.Byte", "java.lang.Short", "java.lang.Integer", "java.lang.Long"):
				return fits(budget, 24, long(unicode(value)))
			if typeName == "java.math.BigInteger":
				cost = integerTextCost(value)
				if cost > budget:
					return ("number", cost, None)
				return (None, cost, long(unicode(value)))
			if typeName == "java.math.BigDecimal":
				cost = decimalTextCost(value)
				if cost > budget:
					return ("number", cost, None)
				return (None, cost, {"type": "decimal", "text": unicode(value)})
			doubled = float(value.doubleValue())
			if math.isnan(doubled) or math.isinf(doubled):
				return fits(budget, 48, {"type": "non-finite-number", "text": unicode(doubled)})
			return fits(budget, 32, doubled)
		if isinstance(value, Date):
			return fits(budget, 48, unicode(value.toInstant().toString()))
		if hasattr(value, "getColumnCount") and hasattr(value, "getRowCount") and hasattr(value, "getValueAt"):
			return boundedDataset(value, budget, depth)
		if isinstance(value, dict) or isinstance(value, Map):
			return boundedMapping(value, budget, depth)
		if isinstance(value, (list, tuple, List)) or isinstance(value, array.array):
			return boundedSequence(value, budget, depth)
		if hasattr(value, "getClass") and value.getClass().isArray():
			return boundedSequence(value, budget, depth)
		raise TypeError("Unsupported Tag value type: " + unicode(type(value)))
	def numericCost(value):
		# An upper bound on the text a numeric write value is counted as: a boolean is
		# `true` or `false`, an integer and a Java BigInteger are counted from their width,
		# a BigDecimal from its unscaled width and scale, and any other Number - a double -
		# is inside 32 bytes.
		if isinstance(value, (bool, Boolean)):
			return 5
		if isinstance(value, (int, long)):
			return integerTextCost(value)
		if isinstance(value, Number):
			typeName = unicode(value.getClass().getName())
			if typeName == "java.math.BigInteger":
				return integerTextCost(value)
			if typeName == "java.math.BigDecimal":
				return decimalTextCost(value)
		return NUMERIC_INPUT_BYTES

	def inputValueBudget(value, budget):
		# One write value measured against `budget`, the aggregate budget left for its
		# item: `(problem, bytes)`, where `problem` is the per-value ceiling it breaks, if
		# any. The count stops at `budget`, so an over-budget item costs bounded work
		# however the items after it are shaped, and a string is counted up to its own
		# ceiling only when the budget still reaches that ceiling.
		if isinstance(value, basestring):
			counted = utf8BytesBounded(value, min(budget, VALUE_STRING_MAX_BYTES))
			if counted > VALUE_STRING_MAX_BYTES:
				return (("stringValueOverLimit", counted, VALUE_STRING_MAX_BYTES), counted)
			return (None, counted)
		if isinstance(value, (list, tuple, List)):
			if len(value) > ARRAY_MAX_ELEMENTS:
				return (("arrayElementsOverLimit", len(value), ARRAY_MAX_ELEMENTS), 0)
			total = 2
			for child in value:
				total += 1
				if total > budget:
					return (None, total)
				problem, counted = inputValueBudget(child, budget - total)
				total += counted
				if problem is not None:
					return (problem, total)
			return (None, total)
		return (None, numericCost(value))
	def observedNoun(value):
		if hasattr(value, "getColumnCount") and hasattr(value, "getRowCount") and hasattr(value, "getValueAt"):
			return "Dataset"
		return "value"

	def observedBudgetMessage(noun, reason, count):
		if reason == "depth":
			return "The observed " + noun + " nests deeper than the " + unicode(OBSERVED_MAX_DEPTH) + "-level Observed-state depth budget; it was not returned."
		if reason == "cells":
			return "The observed Dataset is " + unicode(count) + " cells, over the " + unicode(OBSERVED_DATASET_MAX_CELLS) + "-cell Observed-state budget; it was not returned."
		if reason == "number":
			return "The observed " + noun + " is a number whose decimal text is up to " + unicode(count) + " bytes, over the " + unicode(OBSERVED_VALUE_MAX_BYTES) + "-byte Observed-state value budget; it was not returned."
		return "The observed " + noun + " is at least " + unicode(count) + " bytes, over the " + unicode(OBSERVED_VALUE_MAX_BYTES) + "-byte Observed-state value budget; it was not returned."

	def observedEntryBudget(value, timestamp):
		# One Observed entry's budget: the Tag value is measured and rendered by the walk,
		# then the timestamp with what is left of the same per-value budget, because both
		# are part of what the entry returns. `(problem, cost, value, timestamp)`, where a
		# problem means nothing was materialized for the entry.
		reason, cost, rendered = boundedValue(value, OBSERVED_VALUE_MAX_BYTES, OBSERVED_MAX_DEPTH)
		if reason is not None:
			return (observedBudgetMessage(observedNoun(value), reason, cost), 0, None, None)
		reason, timestampCost, renderedTimestamp = boundedValue(timestamp, OBSERVED_VALUE_MAX_BYTES - cost, OBSERVED_MAX_DEPTH)
		if reason is not None:
			return (observedBudgetMessage("timestamp", reason, timestampCost), 0, None, None)
		return (None, cost + timestampCost, rendered, renderedTimestamp)
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
		# batch never reaches the policy read, let alone the Gateway. One walk measures
		# each item against the aggregate budget left for it and stops as soon as that
		# budget is crossed, so the batch costs bounded work however the items after the
		# refusal are shaped.
		totalInputBytes = 0
		for index in range(len(paths)):
			pathBytes = utf8BytesBounded(paths[index], min(INPUT_MAX_BYTES - totalInputBytes, PATH_MAX_BYTES))
			if pathBytes > PATH_MAX_BYTES:
				return toolError("limit_exceeded", INPUT_CEILING_MESSAGE, {"reason": "pathOverLength", "index": index, "path": boundedText(paths[index], 256), "requested": pathBytes, "limit": PATH_MAX_BYTES})
			totalInputBytes += pathBytes
			if totalInputBytes <= INPUT_MAX_BYTES:
				problem, valueBytes = inputValueBudget(values[index], INPUT_MAX_BYTES - totalInputBytes)
				totalInputBytes += valueBytes
				if problem is not None:
					return toolError("limit_exceeded", INPUT_CEILING_MESSAGE, {"reason": problem[0], "index": index, "path": boundedText(paths[index], 256), "requested": problem[1], "limit": problem[2]})
			if totalInputBytes > INPUT_MAX_BYTES:
				return toolError("limit_exceeded", "The write batch is at least " + unicode(totalInputBytes) + " bytes, over the " + unicode(INPUT_MAX_BYTES) + "-byte input budget; the count stops at the budget, so the amount is a lower bound. Split the request across calls.", {"reason": "inputOverByteBudget", "requested": totalInputBytes, "limit": INPUT_MAX_BYTES})
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
					# D10: the Observed state carries its own budget, decided by one walk that
					# measures and renders together, so nothing this entry returns was materialized
					# before its cost was inside the budget. A value or a timestamp the budget cannot
					# return is an explicit observed error, never a truncation.
					problem, cost, rendered, renderedTimestamp = observedEntryBudget(rawValue, value.getTimestamp())
					if problem is None and observedBudgetSpent:
						problem = "The Observed-state budget of " + unicode(OBSERVED_STATE_MAX_BYTES) + " bytes is already spent; this value was not returned."
					if problem is None and observedBytes + cost > OBSERVED_STATE_MAX_BYTES:
						problem = "The Observed state already holds " + unicode(observedBytes) + " bytes, so returning this value would pass the " + unicode(OBSERVED_STATE_MAX_BYTES) + "-byte budget; it was not returned."
						observedBudgetSpent = True
					if problem is not None:
						observed.append(observedError(path, "limit_exceeded", problem))
						continue
					observedBytes += cost
					observed.append({"path": path, "status": "ok", "value": rendered, "quality": quality(value.getQuality()), "timestamp": renderedTimestamp})
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
