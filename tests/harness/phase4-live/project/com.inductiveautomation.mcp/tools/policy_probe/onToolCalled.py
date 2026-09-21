def onToolCalled(builder, policyPath, readTimeoutMs, missingPath, writeProbePath, writeProbeValue, configModuleId, configTypeId, configName):
	from java.lang import System, Exception as JavaException
	from java.security import MessageDigest
	import time

	def text(value):
		if value is None:
			return ""
		return unicode(value)

	def sha256(value):
		hasher = MessageDigest.getInstance("SHA-256")
		hasher.update(text(value).encode("utf-8"))
		digest = hasher.digest()
		parts = []
		for byte in digest:
			code = byte
			if code < 0:
				code = code + 256
			parts.append("%02x" % code)
		return "".join(parts)

	def readBlockingDetail(path):
		results = system.tag.readBlocking([path], readTimeoutMs)
		items = []
		for result in results:
			value = result.value
			# Jython exposes Java strings differently from CPython; coerce with
			# unicode() instead of isinstance() so the recorded value text is the
			# tag's own string whatever the interop type turns out to be.
			valueText = text(value)
			item = {
				"quality": text(result.quality),
				"valueType": text(type(value).__name__),
				"valueLength": len(valueText),
				"valueByteLength": len(valueText.encode("utf-8")),
				"valueSha256": sha256(valueText),
				"valuePrefix": valueText[:120],
			}
			if len(valueText) <= 4096:
				item["valueText"] = valueText
			items.append(item)
		return {"path": path, "count": len(results), "items": items}

	def configurationDetail(path):
		configuration = system.tag.getConfiguration(path, False)
		entries = []
		for entry in configuration:
			keys = []
			valueLength = 0
			if hasattr(entry, "keys"):
				keys = sorted([text(key) for key in entry.keys()])
				if "value" in entry:
					valueLength = len(text(entry["value"]))
			entries.append({"keys": keys, "valueLength": valueLength})
		return {"path": path, "count": len(configuration), "entries": entries}

	def providerRoot(path):
		start = path.find("[")
		end = path.find("]")
		if start == 0 and end > 1:
			return path[0:end + 1]
		return path

	def providerName(path):
		start = path.find("[")
		end = path.find("]")
		if start == 0 and end > start:
			return path[start + 1:end]
		return ""

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
		entry["name"] = name
		measurements.append(entry)
		return entry

	def readPolicy():
		return readBlockingDetail(policyPath)

	def readMissing():
		return readBlockingDetail(missingPath)

	def readProviderRootConfiguration():
		return configurationDetail(providerRoot(policyPath))

	def readPolicyConfiguration():
		return configurationDetail(policyPath)

	def writeInsideProvider():
		before = readBlockingDetail(writeProbePath)
		qualityCodes = system.tag.writeBlocking([writeProbePath], [writeProbeValue])
		after = readBlockingDetail(writeProbePath)
		return {
			"writeQualityCodes": [text(code) for code in qualityCodes],
			"beforeWrite": before,
			"afterWrite": after,
		}

	def readConfigurationResource():
		resource = system.config.getResource(moduleId=configModuleId, typeId=configTypeId, name=configName)
		detail = {
			"resourceClass": text(type(resource).__name__),
			"resourceText": text(resource)[:400],
			"resourceName": configName,
		}
		for attribute in ("name", "signature", "description", "enabled", "collection"):
			try:
				detail["resource" + attribute[0].upper() + attribute[1:]] = text(getattr(resource, attribute))
			except (Exception, JavaException):
				detail["resource" + attribute[0].upper() + attribute[1:]] = ""
		try:
			config = resource.config
			if hasattr(config, "keys"):
				detail["configKeys"] = sorted([text(key) for key in config.keys()])
			else:
				detail["configKeys"] = []
		except (Exception, JavaException):
			detail["configKeys"] = []
		return detail

	def listConfigurationResourceTypes():
		types = system.config.getResourceTypes()
		rendered = sorted([text(item) for item in types])
		return {"count": len(rendered), "items": rendered[:400]}

	def readEnvironmentVariable():
		value = System.getenv("IGNITION_MCP_DATABASE_QUERY_REGISTRY_JSON")
		return {"available": True, "present": value is not None}

	def scopeNamespaces():
		names = []
		try:
			names = sorted([text(name) for name in dir(system)])
		except (Exception, JavaException):
			names = []
		detail = {"namespaces": names[:120]}
		for key in ("config", "alarm", "file", "tag", "util"):
			attribute = "has" + key[0].upper() + key[1:]
			try:
				detail[attribute] = getattr(system, key) is not None
			except (Exception, JavaException):
				detail[attribute] = False
		return detail

	def projectName():
		return {"projectName": text(system.util.getProjectName())}

	try:
		policyRead = measure("tag.readBlocking.policy", readPolicy)
		parsedKind = ""
		parsedKeys = []
		policyText = ""
		if policyRead.get("ok") and policyRead.get("items"):
			policyText = policyRead["items"][0].get("valueText", "")
		if policyText:
			try:
				parsed = system.util.jsonDecode(policyText)
				parsedKind = text(type(parsed).__name__)
				if isinstance(parsed, dict):
					parsedKeys = sorted([text(key) for key in parsed.keys()])
			except (Exception, JavaException) as exc:
				parsedKind = "error: " + text(exc)
		policyRead["jsonKind"] = parsedKind
		policyRead["jsonKeys"] = parsedKeys
		policyRead["valueText"] = policyText

		measure("tag.readBlocking.missing", readMissing)
		measure("tag.getConfiguration.providerRoot", readProviderRootConfiguration)
		measure("tag.getConfiguration.policy", readPolicyConfiguration)
		measure("tag.writeBlocking.probe", writeInsideProvider)
		measure("system.config.getResource", readConfigurationResource)
		measure("system.config.getResourceTypes", listConfigurationResourceTypes)
		measure("java.lang.System.getenv", readEnvironmentVariable)
		measure("system.namespaces", scopeNamespaces)
		measure("system.util.getProjectName", projectName)
		report = {
			"schemaVersion": 1,
			"probe": "policy_probe",
			"policyPath": policyPath,
			"providerName": providerName(policyPath),
			"missingPath": missingPath,
			"writeProbePath": writeProbePath,
			"configResource": configModuleId + "/" + configTypeId + "/" + configName,
			"measurements": measurements,
		}
		return {"structuredContent": report}
	except (Exception, JavaException) as exc:
		error = {"code": "upstream_error", "message": "policy_probe failed: " + text(type(exc).__name__) + ": " + text(exc)}
		return {"content": builder.text(system.util.jsonEncode(error)), "isError": True}
