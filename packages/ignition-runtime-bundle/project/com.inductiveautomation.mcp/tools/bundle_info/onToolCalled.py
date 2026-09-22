def onToolCalled(builder):
	bundleVersion = "0.6.0"
	# Stamped at build time by tooling.native; never a secret.
	bundleSourceRevision = "__BUNDLE_SOURCE_REVISION__"
	gatewayVersion = str(system.util.getVersion())
	moduleVersion = "unknown"
	# D28: explicit logical null survives the pinned Module serializer.
	moduleBuild = {"$ignition": "null"}
	modules = system.util.getModules()
	for rowIndex in range(modules.getRowCount()):
		if str(modules.getValueAt(rowIndex, "Id")) == "com.inductiveautomation.mcp":
			moduleVersion = str(modules.getValueAt(rowIndex, "Version"))
			versionParts = moduleVersion.split(".")
			if len(versionParts) > 3:
				buildCandidate = versionParts[3].split("-")[0]
				if len(buildCandidate) == 10 and buildCandidate.isdigit():
					moduleBuild = buildCandidate
			break
	return {"structuredContent": {"bundleVersion": bundleVersion, "bundleSourceRevision": bundleSourceRevision, "gatewayVersion": gatewayVersion, "mcpModuleVersion": moduleVersion, "mcpModuleBuild": moduleBuild, "compatibilityStatus": "UNKNOWN"}}
