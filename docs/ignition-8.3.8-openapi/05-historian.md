# historian

> Source SHA-256: `7ab205eff392dc38a547980fe94803c97f7c075102e7b983efc63f76b827b744`
> Endpoints: `9`

Read the matching endpoint line, then fetch only its `src:[start,end)` byte range from `./ignition-8.3-openapi.min.json`.

## `historian-config`

- **Modify Historian** — `PUT /data/api/v1/resources/com.inductiveautomation.historian/historian-provider` — Modify one or more Historian resources — `src:[560878,589391)`
- **Create Historian** — `POST /data/api/v1/resources/com.inductiveautomation.historian/historian-provider` — Create a new Historian resource — `src:[589399,618751)`
- **Delete Historian** — `DELETE /data/api/v1/resources/com.inductiveautomation.historian/historian-provider/{name}/{signature}` — Delete a Historian resource by name — `src:[618860,621603)`
- **Delete Historian (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.historian/historian-provider` — Delete multiple Historian resources by name — `src:[1264697,1267321)`
- **Get Historian Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.historian/historian-provider/{name}` — Retrieve configuration details about a specific Historian resource — `src:[1452112,1481157)`
- **List Historian Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.historian/historian-provider` — List all Historian resources, in verbose format, including… — `src:[3469623,3500563)`
- **Get Historian Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.historian/historian-provider` — List all Historian resources, including each resource’s name and… — `src:[4213072,4215963)`
- **Rename Historian** — `POST /data/api/v1/resources/rename/com.inductiveautomation.historian/historian-provider/{name}` — Change the name of a Historian resource, and update all… — `src:[4333023,4335994)`
- **Describe Historian Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.historian/historian-provider` — Provide information about the Historian resource type, including… — `src:[4555763,4559284)`
