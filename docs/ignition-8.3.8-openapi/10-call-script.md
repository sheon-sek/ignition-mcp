# Call Script

> Source SHA-256: `7ab205eff392dc38a547980fe94803c97f7c075102e7b983efc63f76b827b744`
> Endpoints: `8`

Read the matching endpoint line, then fetch only its `src:[start,end)` byte range from `./ignition-8.3-openapi.min.json`.

## `call-script`

- **Modify Call Script** — `PUT /data/api/v1/resources/com.inductiveautomation.sip-notification/script-settings` — Modify one or more Call Script resources — `src:[1135019,1146260)`
- **Create Call Script** — `POST /data/api/v1/resources/com.inductiveautomation.sip-notification/script-settings` — Create a new Call Script resource — `src:[1146268,1158348)`
- **Delete Call Script** — `DELETE /data/api/v1/resources/com.inductiveautomation.sip-notification/script-settings/{name}/{signature}` — Delete a Call Script resource by name — `src:[1158461,1161203)`
- **Delete Call Script (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.sip-notification/script-settings` — Delete multiple Call Script resources by name — `src:[1283700,1286323)`
- **Get Call Script Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.sip-notification/script-settings/{name}` — Retrieve configuration details about a specific Call Script resource — `src:[1689628,1698263)`
- **List Call Script Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.sip-notification/script-settings` — List all Call Script resources, in verbose format, including… — `src:[3720355,3730885)`
- **Get Call Script Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.sip-notification/script-settings` — List all Call Script resources, including each resource’s name… — `src:[4233930,4236820)`
- **Describe Call Script Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.sip-notification/script-settings` — Provide information about the Call Script resource type,… — `src:[4578774,4583707)`
