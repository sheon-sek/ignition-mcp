# BACnet

> Source SHA-256: `7ab205eff392dc38a547980fe94803c97f7c075102e7b983efc63f76b827b744`
> Endpoints: `9`

Read the matching endpoint line, then fetch only its `src:[start,end)` byte range from `./ignition-8.3-openapi.min.json`.

## `local-devices`

- **Modify Local Devices** — `PUT /data/api/v1/resources/com.inductiveautomation.opcua.drivers.bacnet/BacnetIpLocalDeviceConfig` — Modify one or more Local Devices resources — `src:[641935,648252)`
- **Create Local Devices** — `POST /data/api/v1/resources/com.inductiveautomation.opcua.drivers.bacnet/BacnetIpLocalDeviceConfig` — Create a new Local Devices resource — `src:[648260,655416)`
- **Delete Local Devices** — `DELETE /data/api/v1/resources/com.inductiveautomation.opcua.drivers.bacnet/BacnetIpLocalDeviceConfig/{name}/{signature}` — Delete a Local Devices resource by name — `src:[655543,658291)`
- **Delete Local Devices (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.opcua.drivers.bacnet/BacnetIpLocalDeviceConfig` — Delete multiple Local Devices resources by name — `src:[1270155,1272784)`
- **Get Local Devices Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.opcua.drivers.bacnet/BacnetIpLocalDeviceConfig/{name}` — Retrieve configuration details about a specific Local Devices… — `src:[1486981,1491516)`
- **List Local Devices Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.opcua.drivers.bacnet/BacnetIpLocalDeviceConfig` — List all Local Devices resources, in verbose format, including… — `src:[3508268,3514698)`
- **Get Local Devices Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.opcua.drivers.bacnet/BacnetIpLocalDeviceConfig` — List all Local Devices resources, including each resource’s name… — `src:[4219060,4221956)`
- **Rename Local Devices** — `POST /data/api/v1/resources/rename/com.inductiveautomation.opcua.drivers.bacnet/BacnetIpLocalDeviceConfig/{name}` — Change the name of a Local Devices resource, and update all… — `src:[4339189,4342165)`
- **Describe Local Devices Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.opcua.drivers.bacnet/BacnetIpLocalDeviceConfig` — Provide information about the Local Devices resource type,… — `src:[4560867,4563341)`
