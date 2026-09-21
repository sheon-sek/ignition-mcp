def onToolCalled(builder, providerRoot, rootName, siblingRootName, targets):
	from java.lang import Exception as JavaException

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

	def folder(name, children):
		return {"name": name, "tagType": "Folder", "enabled": True, "tags": children}

	def children():
		return [atomicTag(entry) for entry in targets] + [
			folder("Nested", [atomicTag({"name": "Inner", "dataType": "Int4", "value": 0})]),
		]

	# `system.tag.configure` needs a Tag path, so the provider is bracketed while
	# the caller passes the bare provider name the policy allowlist uses.
	bracketProvider = "[" + providerRoot + "]"

	def configure():
		# The ticket #6 harness proved the two-form create; the same fallback is
		# kept here so a provider that refuses the nested tree still gets the
		# sibling root created instead of failing the stage.
		detail = {}
		codes = qualityList(system.tag.configure(bracketProvider, [tree()], "o"))
		detail["folderFallbackUsed"] = False
		detail["qualityCodes"] = codes
		if allGood(codes):
			return detail
		detail["folderFallbackUsed"] = True
		detail["rootQualityCodes"] = qualityList(system.tag.configure(
			bracketProvider, [folder(rootName, []), folder(siblingRootName, [])], "o",
		))
		detail["qualityCodes"] = qualityList(system.tag.configure(
			bracketProvider + rootName, children(), "o",
		))
		detail["siblingQualityCodes"] = qualityList(system.tag.configure(
			bracketProvider + siblingRootName, [atomicTag(siblingTarget())], "o",
		))
		return detail

	def tree():
		return folder(rootName, children())

	def siblingTarget():
		return {"name": "WriteTarget", "dataType": "Int4", "value": 0}

	def readValues(paths):
		values = []
		items = system.tag.readBlocking(paths, 5000)
		for index in range(len(paths)):
			item = items[index]
			values.append({"path": paths[index], "quality": text(item.quality), "value": text(item.value)})
		return values

	try:
		if not isinstance(targets, (list, tuple)) or not targets:
			error = {"code": "invalid_argument", "message": "targets must be a non-empty array"}
			return {"content": builder.text(system.util.jsonEncode(error)), "isError": True}
		configured = configure()
		siblingCodes = configured.get("siblingQualityCodes")
		fixturePaths = []
		for entry in targets:
			fixturePaths.append(bracketProvider + rootName + "/" + entry["name"])
		siblingPath = bracketProvider + siblingRootName + "/WriteTarget"
		initialValues = readValues(fixturePaths + [siblingPath])
		fixtureReadable = True
		for entry in initialValues:
			if entry["quality"].find("Good") != 0:
				fixtureReadable = False
		report = {
			"schemaVersion": 1,
			"probe": "tag_fixture_probe",
			"providerRoot": providerRoot,
			"rootName": rootName,
			"siblingRootName": siblingRootName,
			"configured": allGood(configured["qualityCodes"]) and (siblingCodes is None or allGood(siblingCodes)),
			"configure": configured,
			"fixturePaths": fixturePaths,
			"siblingPath": siblingPath,
			"fixtureReadable": fixtureReadable,
			"initialValues": initialValues,
		}
		return {"structuredContent": report}
	except (Exception, JavaException) as exc:
		error = {"code": "upstream_error", "message": "tag_fixture_probe failed: " + text(type(exc).__name__) + ": " + text(exc)}
		return {"content": builder.text(system.util.jsonEncode(error)), "isError": True}
