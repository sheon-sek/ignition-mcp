## 目标 
 
详细分析 jsgorana/ignition-mcp: MCP server for Inductive Automation Ignition — tags, alarms, history, projects, and full Perspective deployment  和 WhiskeyHouse/ignition-mcp: Ignition (8.3 and above) MCP server work with the new REST API 这两个Repo。 
 
已知共同点为他们一部分tool使用Ignition Native REST，一部分使用WebDev，这是Ignition端的限制导致无法完全只从其中一端实现所有需要的功能。 
 
现在我们需要拿这两个源代码，做结合和优化，额外自己开发一套独立出来的用最新FastMCP跑起来的Ignition专用MCP Server，让其他AI Agent/MCP Client能以streamable-http的方式使用并操作Ignition。 
 
最大的不同的他们在限制下被迫使用WebDev实现的功能我们必须以Ignition内建MCP Module的方式实现，这个Ignition MCP Module是Ignition官方内建的Module，安装到Ignition后建立一个独立的MCP Server，用户可以在Ignition Designer通过Ignition的python script(Jython 2.7)和调用Ignition内建python functions撰写MCP tool。 
 
因此我们的Repo会有两个package，一个结合以上两个Repo重组构建的FastMCP，和一套Ignition内建MCP的tools的实现（用户可以打包成ZIP直接Import到Designer就能使用了）。 
 
最终用户会有两个MCP连接可以连接上使用，一个是我们自己的FastMCP的MCP Server（通过调用Ignition Native REST实现，一个是 Ignition官方内建MCP Module的MCP Server。 
 
## 技能载入 
 
加载项目的Sources里的 `ignition-mcp-tools-skill`，这个技能详细说明了怎么开发Ignition内建MCP Tool。
 
--- 
 
在这个session，你把之前我们做过的所有Ignition AI MCP/Agent的东西先暂时遗忘掉不要考虑进来。 现在开始新的旅程，先分析并研究这个方向的可行性，是否还有更好的Solution，有什么坑是需要提前避免的等等