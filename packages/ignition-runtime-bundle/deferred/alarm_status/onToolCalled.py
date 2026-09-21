def onToolCalled(builder, states, priorities, alarmPaths, sourcePaths, displayPaths, providers, allProperties, anyProperties, definedProperties, includeShelved, maxResults):
	from java.lang import Number, Exception as JavaException
	from java.util import UUID, Date, Map, List
	correlationId = unicode(UUID.randomUUID())
	logger = system.util.getLogger("IgnitionMCP.Runtime.AlarmStatus")

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

	def listOfText(value, name, allowed, maximum, maxLength):
		if value is None:
			return []
		if not isinstance(value, (list, tuple, List)) or len(value) > maximum:
			raise ValueError(name + " must be an array with at most " + unicode(maximum) + " items.")
		result = []
		for item in value:
			if not isinstance(item, basestring) or not item.strip():
				raise ValueError(name + " must contain non-empty strings.")
			item = item.strip()
			if len(item) > maxLength:
				raise ValueError(name + " items must not exceed " + unicode(maxLength) + " characters.")
			if allowed is not None and item not in allowed:
				raise ValueError(name + " contains an unsupported value: " + item)
			if item not in result:
				result.append(item)
		return result

	def mapValue(value):
		if isinstance(value, dict):
			return value
		if isinstance(value, Map):
			return dict((unicode(entry.getKey()), entry.getValue()) for entry in value.entrySet())
		raise ValueError("Property conditions must be objects.")

	def propertyConditions(value, name):
		if value is None:
			return []
		if not isinstance(value, (list, tuple, List)) or len(value) > 20:
			raise ValueError(name + " must contain at most 20 conditions.")
		result = []
		for raw in value:
			item = mapValue(raw)
			if set(item.keys()) != set(("property", "operator", "value")):
				raise ValueError(name + " conditions require property, operator, and value.")
			prop = item["property"]
			op = item["operator"]
			conditionValue = item["value"]
			if not isinstance(prop, basestring) or not prop.strip() or len(prop.strip()) > 128 or op not in ("=", "!=", "<", "<=", ">", ">="):
				raise ValueError(name + " contains an invalid property/operator; property names are limited to 128 characters.")
			if not isinstance(conditionValue, (basestring, bool, int, long, float, Number)):
				raise ValueError(name + " condition values must be scalar.")
			if isinstance(conditionValue, basestring) and len(conditionValue) > 4096:
				raise ValueError(name + " string condition values must not exceed 4096 characters.")
			result.append((prop.strip(), op, conditionValue))
		return result

	def eventValue(event, key):
		if event.contains(key):
			return event.get(key)
		return None

	try:
		states = listOfText(states, "states", ("ClearUnacked", "ClearAcked", "ActiveUnacked", "ActiveAcked"), 4, 32)
		priorities = listOfText(priorities, "priorities", ("Diagnostic", "Low", "Medium", "High", "Critical"), 5, 32)
		alarmPaths = listOfText(alarmPaths, "alarmPaths", None, 50, 2048)
		sourcePaths = listOfText(sourcePaths, "sourcePaths", None, 50, 2048)
		displayPaths = listOfText(displayPaths, "displayPaths", None, 50, 2048)
		providers = listOfText(providers, "providers", None, 20, 128)
		definedProperties = listOfText(definedProperties, "definedProperties", None, 20, 128)
		allProperties = propertyConditions(allProperties, "allProperties")
		anyProperties = propertyConditions(anyProperties, "anyProperties")
		if includeShelved is None:
			includeShelved = False
		if not isinstance(includeShelved, bool):
			return toolError("invalid_argument", "includeShelved must be boolean.")
		if maxResults is None:
			maxResults = 100
		if isinstance(maxResults, bool) or not isinstance(maxResults, (int, long)) or maxResults < 1 or maxResults > 500:
			return toolError("invalid_argument", "maxResults must be an integer from 1 to 500.")
		kwargs = {"includeShelved": bool(includeShelved)}
		if states: kwargs["state"] = states
		if priorities: kwargs["priority"] = priorities
		if alarmPaths: kwargs["path"] = alarmPaths
		if sourcePaths: kwargs["source"] = sourcePaths
		if displayPaths: kwargs["displaypath"] = displayPaths
		if providers: kwargs["provider"] = providers
		if definedProperties: kwargs["defined"] = definedProperties
		if allProperties: kwargs["all_properties"] = allProperties
		if anyProperties: kwargs["any_properties"] = anyProperties
		results = system.alarm.queryStatus(**kwargs)
		if len(results) > maxResults:
			return toolError("limit_exceeded", "Alarm status exceeds maxResults; add provider/path/state/priority filters or raise the limit within the hard ceiling.")
		items = []
		for event in results:
			items.append({
				"eventId": unicode(event.getId()),
				"name": unicode(event.getName()),
				"label": unicode(event.getLabel()),
				"source": unicode(event.getSource()),
				"displayPath": unicode(event.getDisplayPath()),
				"priority": unicode(event.getPriority()),
				"state": unicode(event.getState()),
				"eventTime": jsonValue(eventValue(event, "EventTime")),
				"active": bool(eventValue(event, "IsActive")) if event.contains("IsActive") else not bool(event.isCleared()),
				"acknowledged": bool(event.isAcked()),
				"cleared": bool(event.isCleared()),
				"shelved": bool(event.isShelved()),
				"activeTime": jsonValue(eventValue(event, "ActiveTime")),
				"clearTime": jsonValue(eventValue(event, "ClearTime")),
				"ackTime": jsonValue(eventValue(event, "AckTime")),
				"ackUser": jsonValue(eventValue(event, "AckUser")),
				"notes": jsonValue(event.getNotes())
			})
		domain = encodeNulls({"items": items, "summary": {"returned": len(items), "limit": int(maxResults)}, "meta": {"correlationId": correlationId}})
		encoded = system.util.jsonEncode(domain)
		if len(encoded.encode("utf-8")) > 262144:
			return toolError("limit_exceeded", "Structured output exceeds the 256 KiB default limit; narrow the alarm query.")
		return {"structuredContent": domain}
	except ValueError as exc:
		return toolError("invalid_argument", unicode(exc))
	except (Exception, JavaException) as exc:
		logger.error("correlationId=" + correlationId + " alarm_status failed: " + unicode(exc))
		return toolError("upstream_error", "The alarm status query could not be completed.")
