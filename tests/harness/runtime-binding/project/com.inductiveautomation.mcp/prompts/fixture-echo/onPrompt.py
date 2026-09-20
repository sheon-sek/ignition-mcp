def onPrompt(builder, arguments):
	return {"description": "phase0 prompt fixture", "content": builder.text("Echo: " + arguments["value"], role="user")}
