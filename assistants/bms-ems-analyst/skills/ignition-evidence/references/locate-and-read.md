# Locate and read

## Finding Tag paths

- Start broad: `tag_browse` on the provider root (for example `[default]`) and walk the folders. Sites usually file Tags by building, then system, then equipment (`Site/DH1/CRAH-01/...`).
- Search by name: `tag_query` with `provider` and `namePattern` (`*UPS*`, `*CRAH*`, `*ATS*`, `*CH-0*`). Filter with `tagType=UdtInstance` to find the equipment objects themselves.
- Learn one equipment type: `udt_type_list`, then `udt_type_get` on the matching type (`UPS`, `Chiller`, `Breaker`). The UDT lists every member Tag an instance has, which tells you what can be measured without guessing.
- `tag_get_config` on a single Tag shows its source (OPC item path, BACnet object, expression or memory), its scaling, its history settings and its alarm definitions. A memory or expression Tag is not a field measurement. Note that when you weigh it.

## Reading the present

- `tag_read` takes up to 500 paths per call.
- Read the `[System]` provider for Gateway-side health: browse `[System]Gateway` for device and OPC connection status, database status, and Gateway performance (CPU, memory, clock drift).
- A Tag whose timestamp is much older than its neighbours is either stale or unchanging. The `ot-data-layer` skill tells you how to tell which.

## Configuration behind the data

`config_resource_search` with keywords (`opc`, `device`, `database`, `historian`), then `config_resource_list` or `config_resource_get`, shows how device connections and data sources are configured.

`database_query_list` shows the approved Named Queries. The site may have added alarm history, work orders, energy reports or maintenance logs there. Run the relevant one with `database_query`.
