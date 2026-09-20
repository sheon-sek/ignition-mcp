def onToolCalled(builder, startTime, endTime, journalName, states, priorities, alarmPaths, sourcePaths, displayPaths, providers, allProperties, anyProperties, definedProperties, includeData, includeSystem, includeShelved, maxResults):
	from java.lang import Number, Exception as JavaException
	from java.util import UUID, Date, Map, List
	from java.time import Instant
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.AlarmJournal")

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
		if value is None or isinstance(value, (bool, int, long, float, basestring)):
			return value
		if isinstance(value, Number):
			return float(value.doubleValue())
		if isinstance(value, Date):
			return unicode(value.toInstant().toString())
		return unicode(value)

	def parseTime(value, name, defaultValue):
		if value is None or (isinstance(value, basestring) and not value.strip()):
			return defaultValue
		if not isinstance(value, basestring):
			raise ValueError(name + " must be an ISO-8601 timestamp string.")
		try:
			return Date(Instant.parse(value.strip()).toEpochMilli())
		except Exception:
			raise ValueError(name + " must be an ISO-8601 instant such as 2026-09-20T12:00:00Z.")

	def listOfText(value, name, allowed, maximum):
		if value is None:
			return []
		if not isinstance(value, (list, tuple, List)) or len(value) > maximum:
			raise ValueError(name + " must be an array with at most " + unicode(maximum) + " items.")
		result = []
		for item in value:
			if not isinstance(item, basestring) or not item.strip():
				raise ValueError(name + " must contain non-empty strings.")
			item = item.strip()
			if allowed is not None and item not in allowed:
				raise ValueError(name + " contains an unsupported value: " + item)
			if item not in result:
				result.append(item)
		return result

	def mapValue(value):
		if isinstance(value, dict): return value
		if isinstance(value, Map): return dict((unicode(entry.getKey()), entry.getValue()) for entry in value.entrySet())
		raise ValueError("Property conditions must be objects.")

	def propertyConditions(value, name):
		if value is None: return []
		if not isinstance(value, (list, tuple, List)) or len(value) > 20:
			raise ValueError(name + " must contain at most 20 conditions.")
		result = []
		for raw in value:
			item = mapValue(raw)
			if set(item.keys()) != set(("property", "operator", "value")):
				raise ValueError(name + " conditions require property, operator, and value.")
			prop, op, conditionValue = item["property"], item["operator"], item["value"]
			if not isinstance(prop, basestring) or not prop.strip() or op not in ("=", "!=", "<", "<=", ">", ">="):
				raise ValueError(name + " contains an invalid property/operator.")
			if not isinstance(conditionValue, (basestring, bool, int, long, float, Number)):
				raise ValueError(name + " condition values must be scalar.")
			result.append((prop.strip(), op, conditionValue))
		return result

	def eventValue(event, key):
		try: return event.get(key)
		except Exception: return None

	try:
		now = Date()
		end = parseTime(endTime, "endTime", now)
		start = parseTime(startTime, "startTime", Date(end.getTime() - 86400000))
		if start.getTime() > end.getTime():
			return toolError("invalid_argument", "startTime must be before or equal to endTime.")
		if end.getTime() - start.getTime() > 2678400000:
			return toolError("limit_exceeded", "Alarm journal range exceeds the 31-day hard limit; split the query into smaller windows.")
		journalName = journalName.strip() if isinstance(journalName, basestring) else ""
		states = listOfText(states, "states", ("ClearUnacked", "ClearAcked", "ActiveUnacked", "ActiveAcked", "Enabled", "Disabled"), 6)
		priorities = listOfText(priorities, "priorities", ("Diagnostic", "Low", "Medium", "High", "Critical"), 5)
		alarmPaths = listOfText(alarmPaths, "alarmPaths", None, 50)
		sourcePaths = listOfText(sourcePaths, "sourcePaths", None, 50)
		displayPaths = listOfText(displayPaths, "displayPaths", None, 50)
		providers = listOfText(providers, "providers", None, 20)
		definedProperties = listOfText(definedProperties, "definedProperties", None, 20)
		allProperties = propertyConditions(allProperties, "allProperties")
		anyProperties = propertyConditions(anyProperties, "anyProperties")
		for flagName, flagValue in (("includeData", includeData), ("includeSystem", includeSystem), ("includeShelved", includeShelved)):
			if flagValue is not None and not isinstance(flagValue, bool):
				return toolError("invalid_argument", flagName + " must be boolean.")
		if maxResults is None: maxResults = 100
		if isinstance(maxResults, bool) or not isinstance(maxResults, (int, long)) or maxResults < 1 or maxResults > 500:
			return toolError("invalid_argument", "maxResults must be an integer from 1 to 500.")
		kwargs = {"startDate": start, "endDate": end, "includeData": bool(includeData), "includeSystem": bool(includeSystem), "includeShelved": bool(includeShelved)}
		if journalName: kwargs["journalName"] = journalName
		if states: kwargs["state"] = states
		if priorities: kwargs["priority"] = priorities
		if alarmPaths: kwargs["path"] = alarmPaths
		if sourcePaths: kwargs["source"] = sourcePaths
		if displayPaths: kwargs["displaypath"] = displayPaths
		if providers: kwargs["provider"] = providers
		if definedProperties: kwargs["defined"] = definedProperties
		if allProperties: kwargs["all_properties"] = allProperties
		if anyProperties: kwargs["any_properties"] = anyProperties
		results = system.alarm.queryJournal(**kwargs)
		if len(results) > maxResults:
			return toolError("limit_exceeded", "Alarm journal query exceeds maxResults; narrow the time range or filters.")
		items = []
		for event in results:
			items.append({"eventId": unicode(event.getId()), "name": unicode(event.getName()), "label": unicode(event.getLabel()), "source": unicode(event.getSource()), "displayPath": unicode(event.getDisplayPath()), "priority": unicode(event.getPriority()), "eventState": jsonValue(eventValue(event, "EventState")), "eventTime": jsonValue(eventValue(event, "EventTime")), "isSystemEvent": bool(eventValue(event, "IsSystemEvent"))})
		domain = encodeNulls({"startTime": jsonValue(start), "endTime": jsonValue(end), "items": items, "summary": {"returned": len(items), "limit": int(maxResults)}, "meta": {"correlationId": correlationId}})
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > 262144:
			return toolError("limit_exceeded", "Structured output exceeds the 256 KiB default limit; narrow the journal query.")
		return {"structuredContent": domain}
	except ValueError as exc:
		return toolError("invalid_argument", unicode(exc))
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " alarm_journal failed: " + unicode(exc))
		return toolError("upstream_error", "The alarm journal query could not be completed.")
