def onToolCalled(builder, providerRoot, rootName, siblingRootName, targets):
	from java.lang import Exception as JavaException, Thread
	import time

	def text(value):
		if value is None:
			return ""
		return unicode(value)

	def qualityList(codes):
		return [text(code) for code in codes]

	def allGood(codes):
		for code in codes:
			if code.find("Good") != 0:
				return False
		return True

	def atomicTag(entry):
		return {
			"name": entry["name"],
			"tagType": "AtomicTag",
			"valueSource": "memory",
			"dataType": entry["dataType"],
			"value": entry["value"],
			"enabled": True,
		}

	def folderDefinition(name):
		return {"name": name, "tagType": "Folder", "enabled": True}

	def folder(name, children):
		definition = folderDefinition(name)
		definition["tags"] = children
		return definition

	def children():
		return [atomicTag(entry) for entry in targets] + [
			folder("Nested", [atomicTag({"name": "Inner", "dataType": "Int4", "value": 0})]),
		]

	def siblingTarget():
		return {"name": "WriteTarget", "dataType": "Int4", "value": 0}

	# `system.tag.configure` needs a Tag path, so the provider is bracketed while
	# the caller passes the bare provider name the policy allowlist uses.
	bracketProvider = "[" + providerRoot + "]"

	def configureRoot(path, definitions):
		return qualityList(system.tag.configure(path, definitions, "o"))

	def configure():
		# The two-step create the ticket #6 probe proved live: the roots
		# themselves first, then each root's children. A single call carrying both
		# roots returned one QualityCode and created only the first one.
		detail = {}
		detail["rootQualityCodes"] = configureRoot(
			bracketProvider, [folderDefinition(rootName), folderDefinition(siblingRootName)],
		)
		detail["qualityCodes"] = configureRoot(bracketProvider + rootName, children())
		detail["siblingQualityCodes"] = configureRoot(
			bracketProvider + siblingRootName, [atomicTag(siblingTarget())],
		)
		detail["allGood"] = (
			allGood(detail["rootQualityCodes"])
			and allGood(detail["qualityCodes"])
			and allGood(detail["siblingQualityCodes"])
		)
		return detail

	def readValues(paths):
		values = []
		items = system.tag.readBlocking(paths, 5000)
		for index in range(len(paths)):
			item = items[index]
			values.append({"path": paths[index], "quality": text(item.quality), "value": text(item.value)})
		return values

	def waitForReadable(paths, deadlineSeconds):
		# A freshly configured provider can take a moment to serve new Tags; the
		# probe waits instead of reporting a write target that is not there yet.
		started = time.time()
		attempts = 0
		while True:
			attempts += 1
			values = readValues(paths)
			unreadable = 0
			for entry in values:
				if entry["quality"].find("Good") != 0:
					unreadable += 1
			if unreadable == 0 or time.time() - started >= deadlineSeconds:
				return {
					"readable": unreadable == 0,
					"attempts": attempts,
					"waitedMs": int((time.time() - started) * 1000),
					"values": values,
				}
			Thread.sleep(1000)

	try:
		if not isinstance(targets, (list, tuple)) or not targets:
			error = {"code": "invalid_argument", "message": "targets must be a non-empty array"}
			return {"content": builder.text(system.util.jsonEncode(error)), "isError": True}
		configured = configure()
		fixturePaths = []
		for entry in targets:
			fixturePaths.append(bracketProvider + rootName + "/" + entry["name"])
		siblingPath = bracketProvider + siblingRootName + "/WriteTarget"
		readBack = waitForReadable(fixturePaths + [siblingPath], 30.0)
		report = {
			"schemaVersion": 1,
			"probe": "tag_fixture_probe",
			"providerRoot": providerRoot,
			"rootName": rootName,
			"siblingRootName": siblingRootName,
			"configured": bool(configured["allGood"]),
			"configure": configured,
			"fixturePaths": fixturePaths,
			"siblingPath": siblingPath,
			"fixtureReadable": bool(readBack["readable"]),
			"readAttempts": readBack["attempts"],
			"readWaitedMs": readBack["waitedMs"],
			"initialValues": readBack["values"],
		}
		return {"structuredContent": report}
	except (Exception, JavaException) as exc:
		error = {"code": "upstream_error", "message": "tag_fixture_probe failed: " + text(type(exc).__name__) + ": " + text(exc)}
		return {"content": builder.text(system.util.jsonEncode(error)), "isError": True}
