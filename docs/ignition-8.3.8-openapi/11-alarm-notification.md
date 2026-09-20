# Alarm Notification

> Source SHA-256: `7ab205eff392dc38a547980fe94803c97f7c075102e7b983efc63f76b827b744`
> Endpoints: `21`

Read the matching endpoint line, then fetch only its `src:[start,end)` byte range from `./ignition-8.3-openapi.min.json`.

## `alarm-notification-profile`

- **Modify Alarm Notification Profiles** — `PUT /data/api/v1/resources/com.inductiveautomation.alarm-notification/alarm-notification-profile` — Modify one or more Alarm Notification Profiles resources — `src:[224207,282854)`
- **Create Alarm Notification Profiles** — `POST /data/api/v1/resources/com.inductiveautomation.alarm-notification/alarm-notification-profile` — Create a new Alarm Notification Profiles resource — `src:[282862,342348)`
- **Delete Alarm Notification Profiles** — `DELETE /data/api/v1/resources/com.inductiveautomation.alarm-notification/alarm-notification-profile/{name}/{signature}` — Delete a Alarm Notification Profiles resource by name — `src:[342474,345263)`
- **Delete Alarm Notification Profiles (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.alarm-notification/alarm-notification-profile` — Delete multiple Alarm Notification Profiles resources by name — `src:[1240172,1242842)`
- **Get Alarm Notification Profiles Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.alarm-notification/alarm-notification-profile/{name}` — Retrieve configuration details about a specific Alarm… — `src:[1348717,1405327)`
- **List Alarm Notification Profiles Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.alarm-notification/alarm-notification-profile` — List all Alarm Notification Profiles resources, in verbose format… — `src:[3349236,3407741)`
- **Get Alarm Notification Profiles Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.alarm-notification/alarm-notification-profile` — List all Alarm Notification Profiles resources, including each… — `src:[4186162,4189099)`
- **Rename Alarm Notification Profiles** — `POST /data/api/v1/resources/rename/com.inductiveautomation.alarm-notification/alarm-notification-profile/{name}` — Change the name of a Alarm Notification Profiles resource, and… — `src:[4305312,4308329)`
- **Describe Alarm Notification Profiles Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.alarm-notification/alarm-notification-profile` — Provide information about the Alarm Notification Profiles… — `src:[4527837,4531404)`

## `pipeline-status`

- **Alarm Pipeline Detail** — `GET /data/alarm-notification/api/v1/pipeline` — Information about a single alarming pipeline — `src:[971,4696)`
- **Cancel Alarming Pipeline** — `DELETE /data/alarm-notification/api/v1/pipeline` — Cancel an alarming pipeline for the given alarm event ID. — `src:[4706,5844)`
- **Alarm Pipeline Overview** — `GET /data/alarm-notification/api/v1/pipelines` — Information about the alarming pipelines — `src:[5897,9090)`

## `roster-config`

- **Delete Rosters (multiple)** — `POST /data/api/v1/resources/delete/ignition/roster-config` — Delete multiple Rosters resources by name — `src:[1327053,1329670)`
- **Get Rosters Config** — `GET /data/api/v1/resources/find/ignition/roster-config/{name}` — Retrieve configuration details about a specific Rosters resource — `src:[1935298,1937877)`
- **Modify Rosters** — `PUT /data/api/v1/resources/ignition/roster-config` — Modify one or more Rosters resources — `src:[2820953,2826393)`
- **Create Rosters** — `POST /data/api/v1/resources/ignition/roster-config` — Create a new Rosters resource — `src:[2826401,2832680)`
- **Delete Rosters** — `DELETE /data/api/v1/resources/ignition/roster-config/{name}/{signature}` — Delete a Rosters resource by name — `src:[2832759,2835495)`
- **List Rosters Resources** — `GET /data/api/v1/resources/list/ignition/roster-config` — List all Rosters resources, in verbose format, including… — `src:[3996233,4000707)`
- **Get Rosters Names** — `GET /data/api/v1/resources/names/ignition/roster-config` — List all Rosters resources, including each resource’s name and… — `src:[4281523,4284407)`
- **Rename Rosters** — `POST /data/api/v1/resources/rename/ignition/roster-config/{name}` — Change the name of a Rosters resource, and update all references… — `src:[4397347,4400311)`
- **Describe Rosters Resource Type** — `GET /data/api/v1/resources/type/ignition/roster-config` — Provide information about the Rosters resource type, including… — `src:[4642854,4644756)`
