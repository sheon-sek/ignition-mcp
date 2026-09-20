# MCP Module

> Source SHA-256: `7ab205eff392dc38a547980fe94803c97f7c075102e7b983efc63f76b827b744`
> Endpoints: `10`

Read the matching endpoint line, then fetch only its `src:[start,end)` byte range from `./ignition-8.3-openapi.min.json`.

## `mcp-management`

- **Invalidate MCP Session** — `DELETE /data/mcp/{name}` — Invalidates the MCP session associated with the request's… — `src:[4951454,4952114)`

## `server-config`

- **Modify MCP Server Config** — `PUT /data/api/v1/resources/com.inductiveautomation.mcp/server-config` — Modify one or more MCP Server Config resources — `src:[621679,629904)`
- **Create MCP Server Config** — `POST /data/api/v1/resources/com.inductiveautomation.mcp/server-config` — Create a new MCP Server Config resource — `src:[629912,638976)`
- **Delete MCP Server Config** — `DELETE /data/api/v1/resources/com.inductiveautomation.mcp/server-config/{name}/{signature}` — Delete a MCP Server Config resource by name — `src:[639074,641830)`
- **Delete MCP Server Config (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.mcp/server-config` — Delete multiple MCP Server Config resources by name — `src:[1267405,1270042)`
- **Get MCP Server Config Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.mcp/server-config/{name}` — Retrieve configuration details about a specific MCP Server Config… — `src:[1481245,1486864)`
- **List MCP Server Config Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.mcp/server-config` — List all MCP Server Config resources, in verbose format,… — `src:[3500644,3508158)`
- **Get MCP Server Config Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.mcp/server-config` — List all MCP Server Config resources, including each resource’s… — `src:[4216045,4218949)`
- **Rename MCP Server Config** — `POST /data/api/v1/resources/rename/com.inductiveautomation.mcp/server-config/{name}` — Change the name of a MCP Server Config resource, and update all… — `src:[4336085,4339069)`
- **Describe MCP Server Config Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.mcp/server-config` — Provide information about the MCP Server Config resource type,… — `src:[4559365,4560757)`
