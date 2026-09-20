# SFC Module

> Source SHA-256: `7ab205eff392dc38a547980fe94803c97f7c075102e7b983efc63f76b827b744`
> Endpoints: `11`

Read the matching endpoint line, then fetch only its `src:[start,end)` byte range from `./ignition-8.3-openapi.min.json`.

## `charts-info`

- **Get general chart status** — `GET /data/sfc/api/v1/charts/status` — Get the status of all charts in the system — `src:[4998007,5000926)`
- **Get chart totals** — `GET /data/sfc/api/v1/charts/totals` — Get the number of defined and running charts — `src:[5000968,5001428)`
- **Get chart detail** — `GET /data/sfc/api/v1/charts/{projectName}/{chartPath}` — Get the detail of a specific chart — `src:[5001489,5005082)`
- **Cancel chart** — `DELETE /data/sfc/api/v1/charts/{uuid}/cancel` — Cancel a specific chart by UUID — `src:[5005134,5005667)`
- **Pause chart** — `POST /data/sfc/api/v1/charts/{uuid}/pause` — Pause a specific chart by UUID — `src:[5005716,5006244)`
- **Resume chart** — `POST /data/sfc/api/v1/charts/{uuid}/resume` — Resume a specific chart by UUID — `src:[5006294,5006825)`

## `sfc-config`

- **Modify SFC Settings** — `PUT /data/api/v1/resources/com.inductiveautomation.sfc/chart-settings` — Modify one or more SFC Settings resources — `src:[1121857,1126796)`
- **Create SFC Settings** — `POST /data/api/v1/resources/com.inductiveautomation.sfc/chart-settings` — Create the SFC Settings resource — `src:[1126804,1132567)`
- **Delete SFC Settings** — `DELETE /data/api/v1/resources/com.inductiveautomation.sfc/chart-settings/{signature}` — Delete the SFC Settings resource. — `src:[1132659,1134928)`
- **Get SFC Settings Config** — `GET /data/api/v1/resources/singleton/com.inductiveautomation.sfc/chart-settings` — Retrieve configuration details about the SFC Settings resource — `src:[4448576,4451094)`
- **Describe SFC Settings Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.sfc/chart-settings` — Provide information about the SFC Settings resource type,… — `src:[4576859,4578678)`
