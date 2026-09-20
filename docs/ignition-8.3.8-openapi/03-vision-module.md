# Vision Module

> Source SHA-256: `7ab205eff392dc38a547980fe94803c97f7c075102e7b983efc63f76b827b744`
> Endpoints: `3`

Read the matching endpoint line, then fetch only its `src:[start,end)` byte range from `./ignition-8.3-openapi.min.json`.

## `vision-sessions`

- **Session Details** — `GET /data/vision/api/v1/client/{id}` — Details of a specific client session. — `src:[5006868,5008032)`
- **Terminate Session** — `DELETE /data/vision/api/v1/client/{id}` — Terminate a specific client session. — `src:[5008042,5008696)`
- **Client Sessions** — `GET /data/vision/api/v1/clients` — Lists active client sessions. — `src:[5008735,5012298)`
