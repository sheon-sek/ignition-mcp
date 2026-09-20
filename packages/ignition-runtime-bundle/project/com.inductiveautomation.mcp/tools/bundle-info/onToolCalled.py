def onToolCalled(builder):
	bundleVersion = "0.1.0"
	gatewayVersion = str(system.util.getVersion())
	moduleVersion = "unknown"
	moduleBuild = None
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
	return {"structuredContent": {"bundleVersion": bundleVersion, "gatewayVersion": gatewayVersion, "mcpModuleVersion": moduleVersion, "mcpModuleBuild": moduleBuild, "compatibilityStatus": "UNKNOWN"}}
