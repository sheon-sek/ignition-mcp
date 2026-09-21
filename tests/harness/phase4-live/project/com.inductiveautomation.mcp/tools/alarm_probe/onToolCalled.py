def onToolCalled(builder, providerRoot, rootName, provider, noiseCount, cycles, repeats, ackUsername):
	from java.lang import Exception as JavaException, Thread
	import time

	def text(value):
		if value is None:
			return ""
		return unicode(value)

	def sleepSeconds(seconds):
		Thread.sleep(int(seconds * 1000))

	def median(values):
		ordered = sorted(values)
		if not ordered:
			return 0
		return ordered[len(ordered) // 2]

	def qualityList(codes):
		return [text(code) for code in codes]

	def allGood(codes):
		for code in codes:
			if code.find("Good") != 0:
				return False
		return True

	relativeRoot = rootName
	qualifiedRoot = "prov:" + provider + ":/tag:" + relativeRoot
	bracketRoot = "[" + provider + "]" + relativeRoot
	expectedActive = 4 + noiseCount

	def atomicTag(name, alarm):
		return {
			"name": name,
			"tagType": "AtomicTag",
			"valueSource": "memory",
			"dataType": "Int4",
			"value": 0,
			"enabled": True,
			"alarms": [alarm],
		}

	def fixtureTree(alarm):
		noise = []
		for index in range(noiseCount):
			noise.append(atomicTag("Noise%03d" % index, alarm))
		return {
			"name": rootName,
			"tagType": "Folder",
			"enabled": True,
			"tags": [
				atomicTag("Exact", alarm),
				atomicTag("ExactSibling", alarm),
				{
					"name": "Fold",
					"tagType": "Folder",
					"enabled": True,
					"tags": [atomicTag("ChildA", alarm), atomicTag("ChildB", alarm)],
				},
				{"name": "Noise", "tagType": "Folder", "enabled": True, "tags": noise},
			],
		}

	def configure(alarm):
		detail = {}
		codes = qualityList(system.tag.configure(providerRoot, [fixtureTree(alarm)], "o"))
		detail["qualityCodes"] = codes
		detail["folderFallbackUsed"] = False
		if allGood(codes):
			return detail
		# Some builds are happier creating the folder first and then the tree
		# under it; record which form worked instead of assuming one.
		folderCodes = qualityList(system.tag.configure(
			providerRoot, [{"name": rootName, "tagType": "Folder", "enabled": True}], "o",
		))
		childCodes = qualityList(system.tag.configure(
			providerRoot + relativeRoot, fixtureTree(alarm)["tags"], "o",
		))
		detail["folderFallbackUsed"] = True
		detail["folderQualityCodes"] = folderCodes
		detail["qualityCodes"] = childCodes
		return detail

	def fixturePaths():
		return [
			bracketRoot + "/Exact",
			bracketRoot + "/ExactSibling",
			bracketRoot + "/Fold/ChildA",
			bracketRoot + "/Fold/ChildB",
		]

	def noisePaths():
		paths = []
		for index in range(noiseCount):
			paths.append(bracketRoot + "/Noise/Noise%03d" % index)
		return paths

	def writeValues(paths, value):
		codes = []
		index = 0
		while index < len(paths):
			chunk = paths[index:index + 100]
			codes.extend(qualityList(system.tag.writeBlocking(chunk, [value] * len(chunk))))
			index = index + 100
		return codes

	def waitForActive(pattern, expected, deadlineSeconds):
		started = time.time()
		last = -1
		while time.time() - started < deadlineSeconds:
			last = len(system.alarm.queryStatus(path=[pattern]))
			if last >= expected:
				return {"reached": True, "count": last, "waitedMs": int((time.time() - started) * 1000)}
			sleepSeconds(1.0)
		return {"reached": False, "count": last, "waitedMs": int((time.time() - started) * 1000)}

	def timedQuery(patterns, states):
		attempts = []
		count = -1
		for _index in range(repeats):
			started = time.time()
			if patterns is None:
				results = system.alarm.queryStatus()
			elif states is None:
				results = system.alarm.queryStatus(path=patterns)
			else:
				results = system.alarm.queryStatus(path=patterns, state=states)
			attempts.append(int((time.time() - started) * 1000))
			count = len(results)
		return {
			"patterns": patterns if patterns is not None else [],
			"states": states if states is not None else [],
			"count": count,
			"elapsedMs": attempts,
			"minElapsedMs": min(attempts),
			"medianElapsedMs": median(attempts),
			"maxElapsedMs": max(attempts),
		}

	def makeQuery(patterns, states):
		def run():
			return timedQuery(patterns, states)
		return run

	def makeCount(patterns):
		def run():
			return {"count": len(system.alarm.queryStatus(path=patterns))}
		return run

	def makeSourceQuery(patterns):
		def run():
			results = system.alarm.queryStatus(source=patterns)
			return {"count": len(results)}
		return run

	def exactQueryCount():
		results = system.alarm.queryStatus(path=[qualifiedRoot + "/Exact"])
		return {"count": len(results), "results": results}

	def eventDetail():
		results = system.alarm.queryStatus(path=[qualifiedRoot + "/Exact"])
		events = []
		index = 0
		while index < len(results) and index < 3:
			event = results[index]
			item = {}
			for method in ("getId", "getName", "getLabel", "getSource", "getDisplayPath", "getPriority", "getState", "getNotes", "isAcked", "isCleared", "isActive", "isShelved"):
				try:
					item[method] = text(getattr(event, method)())
				except (Exception, JavaException) as exc:
					item[method] = "error: " + text(type(exc).__name__)
			try:
				item["containsIsActive"] = text(event.contains("IsActive"))
			except (Exception, JavaException) as exc:
				item["containsIsActive"] = "error: " + text(type(exc).__name__)
			events.append(item)
			index = index + 1
		methods = []
		if len(results) > 0:
			methods = sorted([text(name) for name in dir(results[0])])
		return {"count": len(results), "events": events, "eventMethods": methods[:60]}

	def acknowledgeExact():
		results = system.alarm.queryStatus(path=[qualifiedRoot + "/Exact"])
		identifiers = []
		index = 0
		while index < len(results) and index < 5:
			identifiers.append(unicode(results[index].getId()))
			index = index + 1
		if not identifiers:
			return {"attempted": 0, "remaining": []}
		remaining = system.alarm.acknowledge(identifiers, "ignition-mcp phase4 probe", ackUsername)
		after = system.alarm.queryStatus(path=[qualifiedRoot + "/Exact"])
		states = []
		for event in after:
			states.append(text(event.getState()))
		return {"attempted": len(identifiers), "remaining": [text(item) for item in remaining], "statesAfter": states}

	measurements = []

	def measure(name, callable):
		started = time.time()
		entry = {"name": name}
		try:
			detail = callable()
			entry["ok"] = True
			if isinstance(detail, dict):
				for key in detail:
					entry[key] = detail[key]
		except (Exception, JavaException) as exc:
			entry["ok"] = False
			entry["error"] = text(type(exc).__name__) + ": " + text(exc)
		entry["elapsedMs"] = int((time.time() - started) * 1000)
		measurements.append(entry)
		return entry

	alarm = {
		"name": "ProbeHi",
		"mode": "AboveValue",
		"setpointA": 1.0,
		"inclusiveA": False,
		"priority": "High",
		"ackMode": "Manual",
		"enabled": True,
		"notes": "ignition-mcp phase4 probe",
	}

	try:
		alarmWithoutAckMode = dict(alarm)
		alarmWithoutAckMode.pop("ackMode")
		alarmLegacyMode = dict(alarm)
		alarmLegacyMode["mode"] = "AboveSetpoint"
		alternatives = [
			{"label": "AboveValue+ackModeManual", "alarm": alarm},
			{"label": "AboveValue", "alarm": alarmWithoutAckMode},
			{"label": "AboveSetpoint", "alarm": alarmLegacyMode},
		]
		shapeResults = []
		accepted = ""
		for alternative in alternatives:
			entry = {"label": alternative["label"], "mode": alternative["alarm"]["mode"]}
			try:
				detail = configure(alternative["alarm"])
				entry["qualityCodes"] = detail["qualityCodes"]
				entry["folderFallbackUsed"] = detail["folderFallbackUsed"]
				entry["configured"] = allGood(detail["qualityCodes"])
			except (Exception, JavaException) as exc:
				entry["configured"] = False
				entry["error"] = text(type(exc).__name__) + ": " + text(exc)
			if entry.get("configured"):
				entry["writeQualityCodes"] = writeValues(fixturePaths() + noisePaths(), 5)
				entry["activation"] = waitForActive(qualifiedRoot + "/*", expectedActive, 30)
				entry["accepted"] = bool(entry["activation"]["reached"])
			shapeResults.append(entry)
			if entry.get("accepted"):
				accepted = alternative["label"]
				break
			if entry.get("configured"):
				writeValues(fixturePaths() + noisePaths(), 0)
				sleepSeconds(1.0)
		if not accepted:
			return {"structuredContent": {
				"schemaVersion": 1,
				"probe": "alarm_probe",
				"providerRoot": providerRoot,
				"rootName": rootName,
				"qualifiedRoot": qualifiedRoot,
				"expectedActive": expectedActive,
				"shapeResults": shapeResults,
				"accepted": "",
				"conclusion": "alarm_fixture_not_created",
				"measurements": measurements,
			}}

		queries = [
			{"label": "exact.qualified", "patterns": [qualifiedRoot + "/Exact"], "states": None},
			{"label": "exact.bracket", "patterns": [bracketRoot + "/Exact"], "states": None},
			{"label": "exact.bare", "patterns": [relativeRoot + "/Exact"], "states": None},
			{"label": "sibling.qualified", "patterns": [qualifiedRoot + "/ExactSibling"], "states": None},
			{"label": "folder.qualified", "patterns": [qualifiedRoot + "/Fold"], "states": None},
			{"label": "folder.partialLeaf", "patterns": [qualifiedRoot + "/Fold/Chi"], "states": None},
			{"label": "folder.trailingWildcard", "patterns": [qualifiedRoot + "/Fold/*"], "states": None},
			{"label": "root.trailingWildcard", "patterns": [qualifiedRoot + "/*"], "states": None},
			{"label": "root.trailingSlashStar", "patterns": [qualifiedRoot + "/*"], "states": ["ActiveUnacked"]},
			{"label": "root.bareWildcard", "patterns": ["*" + relativeRoot + "*"], "states": None},
			{"label": "system.unfiltered", "patterns": None, "states": None},
		]
		for query in queries:
			measure("queryStatus." + query["label"], makeQuery(query["patterns"], query["states"]))
		measure("queryStatus.exact.sourceForm", makeSourceQuery([qualifiedRoot + "/Exact"]))
		measure("queryStatus.exact.count", makeCount([qualifiedRoot + "/Exact"]))

		cycleResults = []
		for index in range(cycles):
			entry = {"cycle": index}
			try:
				writeValues([bracketRoot + "/Exact"], 5)
				sleepSeconds(1.5)
				entry["activeCount"] = len(system.alarm.queryStatus(path=[qualifiedRoot + "/Exact"]))
				writeValues([bracketRoot + "/Exact"], 0)
				sleepSeconds(1.5)
				entry["clearedCount"] = len(system.alarm.queryStatus(path=[qualifiedRoot + "/Exact"]))
				entry["ok"] = True
			except (Exception, JavaException) as exc:
				entry["ok"] = False
				entry["error"] = text(type(exc).__name__) + ": " + text(exc)
			cycleResults.append(entry)

		measure("queryStatus.exact.eventDetail", eventDetail)
		measure("alarm.acknowledge.exact", acknowledgeExact)
		report = {
			"schemaVersion": 1,
			"probe": "alarm_probe",
			"providerRoot": providerRoot,
			"rootName": rootName,
			"qualifiedRoot": qualifiedRoot,
			"bracketRoot": bracketRoot,
			"expectedActive": expectedActive,
			"noiseCount": noiseCount,
			"cycles": cycles,
			"repeats": repeats,
			"shapeResults": shapeResults,
			"accepted": accepted,
			"conclusion": "measured",
			"cycleResults": cycleResults,
			"measurements": measurements,
		}
		return {"structuredContent": report}
	except (Exception, JavaException) as exc:
		error = {"code": "upstream_error", "message": "alarm_probe failed: " + text(type(exc).__name__) + ": " + text(exc)}
		return {"content": builder.text(system.util.jsonEncode(error)), "isError": True}
