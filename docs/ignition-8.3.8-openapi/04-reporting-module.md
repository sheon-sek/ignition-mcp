# Reporting Module

> Source SHA-256: `7ab205eff392dc38a547980fe94803c97f7c075102e7b983efc63f76b827b744`
> Endpoints: `6`

Read the matching endpoint line, then fetch only its `src:[start,end)` byte range from `./ignition-8.3-openapi.min.json`.

## `reports-info`

- **Cancel A Report** — `DELETE /data/reporting/api/v1/cancel/{project}` — Cancel the report with the given name on the given project. — `src:[4982043,4983021)`
- **Completed Reports** — `GET /data/reporting/api/v1/reports/completed` — Information on reports that have been completed. — `src:[4983073,4986535)`
- **Currently Executing Reports** — `GET /data/reporting/api/v1/reports/current` — Information on reports that are currently running. — `src:[4986585,4990059)`
- **Published Reports** — `GET /data/reporting/api/v1/reports/published` — Information on reports that are published on this Ignition Gateway. — `src:[4990111,4993592)`
- **Report Totals** — `GET /data/reporting/api/v1/reports/totals` — Aggregate information on the reports on this Ignition Gateway. — `src:[4993641,4994449)`
- **Upcoming Reports** — `GET /data/reporting/api/v1/reports/upcoming` — Information on reports that are scheduled to be run. — `src:[4994500,4997965)`
