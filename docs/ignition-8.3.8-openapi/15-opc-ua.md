# OPC UA

> Source SHA-256: `7ab205eff392dc38a547980fe94803c97f7c075102e7b983efc63f76b827b744`
> Endpoints: `43`

Read the matching endpoint line, then fetch only its `src:[start,end)` byte range from `./ignition-8.3-openapi.min.json`.

## `access-control`

- **Modify OPC UA Access Control Config** — `PUT /data/api/v1/resources/com.inductiveautomation.opcua/access-control` — Modify one or more OPC UA Access Control Config resources — `src:[658370,664695)`
- **Create OPC UA Access Control Config** — `POST /data/api/v1/resources/com.inductiveautomation.opcua/access-control` — Create the OPC UA Access Control Config resource — `src:[664703,671852)`
- **Delete OPC UA Access Control Config** — `DELETE /data/api/v1/resources/com.inductiveautomation.opcua/access-control/{signature}` — Delete the OPC UA Access Control Config resource. — `src:[671946,674251)`
- **Get OPC UA Access Control Config Config** — `GET /data/api/v1/resources/singleton/com.inductiveautomation.opcua/access-control` — Retrieve configuration details about the OPC UA Access Control… — `src:[4440085,4443989)`
- **Describe OPC UA Access Control Config Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.opcua/access-control` — Provide information about the OPC UA Access Control Config… — `src:[4563425,4565955)`

## `certificate-management`

- **Get Certificate** — `GET /data/opc-ua/api/v1/{routeType}/certificate` — Get the server or client certificate for the Ignition OPC-UA Module — `src:[4965826,4966912)`
- **Download Certificate** — `GET /data/opc-ua/api/v1/{routeType}/certificate/download` — Download Certificate for the given type. — `src:[4966976,4967569)`
- **Regenerate Certificate** — `POST /data/opc-ua/api/v1/{routeType}/certificate/regenerate` — Regenerate a certificate of the given type. — `src:[4967636,4968607)`

## `client-pki-certificate-management`

- **Trust Endpoint Certificate** — `POST /data/opc-ua/api/v1/client/pki/certificate/trust` — Trust the certificate for the given endpoint. — `src:[4952175,4952793)`
- **Get Rejected Client Certificates** — `GET /data/opc-ua/api/v1/client/pki/certificates/rejected` — A list of all rejected client certificates. — `src:[4952857,4953731)`
- **Download Rejected Client Certificate** — `GET /data/opc-ua/api/v1/client/pki/certificates/rejected/{signature}` — Download a rejected client certificate with the given signature. — `src:[4953807,4954561)`
- **Delete Rejected Client Certificate** — `DELETE /data/opc-ua/api/v1/client/pki/certificates/rejected/{signature}` — Delete a rejected client certificate with the given signature. — `src:[4954571,4955338)`
- **Get Trusted Client Certificates** — `GET /data/opc-ua/api/v1/client/pki/certificates/trusted` — A list of all trusted client certificates. — `src:[4955401,4956272)`
- **Upload Trusted Client Certificates** — `POST /data/opc-ua/api/v1/client/pki/certificates/trusted` — Upload a trusted client certificate. — `src:[4956280,4956900)`
- **Download Trusted Client Certificate** — `GET /data/opc-ua/api/v1/client/pki/certificates/trusted/{signature}` — Download a trusted client certificate with the given signature. — `src:[4956975,4957725)`
- **Trust Rejected Client Certificate** — `POST /data/opc-ua/api/v1/client/pki/certificates/trusted/{signature}` — Trust a previously rejected client certificate with the given… — `src:[4957733,4958508)`
- **Delete Trusted Client Certificate** — `DELETE /data/opc-ua/api/v1/client/pki/certificates/trusted/{signature}` — Delete a trusted client certificate with the given signature. — `src:[4958518,4959282)`

## `device`

- **Modify Devices** — `PUT /data/api/v1/resources/com.inductiveautomation.opcua/device` — Modify one or more Devices resources — `src:[674322,867447)`
- **Create Devices** — `POST /data/api/v1/resources/com.inductiveautomation.opcua/device` — Create a new Devices resource — `src:[867455,1061419)`
- **Delete Devices** — `DELETE /data/api/v1/resources/com.inductiveautomation.opcua/device/{name}/{signature}` — Delete a Devices resource by name — `src:[1061512,1064241)`
- **Update Multiple Devices Data Files** — `PUT /data/api/v1/resources/datafile/com.inductiveautomation.opcua/device/{name}` — Create or overwrite multiple data files for a named Devices… — `src:[1166055,1169103)`
- **Get Devices Data File** — `GET /data/api/v1/resources/datafile/com.inductiveautomation.opcua/device/{name}/{filename}` — Retrieve a data file from a specific Devices resource — `src:[1169201,1170237)`
- **Update Devices Data File** — `PUT /data/api/v1/resources/datafile/com.inductiveautomation.opcua/device/{name}/{filename}` — Update (overwrite) a data file for a named Devices resource — `src:[1170244,1173622)`
- **Delete Devices Data File** — `DELETE /data/api/v1/resources/datafile/com.inductiveautomation.opcua/device/{name}/{filename}` — Delete a data file for a named Devices resource — `src:[1173632,1176706)`
- **Delete Devices (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.opcua/device` — Delete multiple Devices resources by name — `src:[1272863,1275473)`
- **Get Devices Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.opcua/device/{name}` — Retrieve configuration details about a specific Devices resource — `src:[1491599,1682687)`
- **List Devices Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.opcua/device` — List all Devices resources, in verbose format, including… — `src:[3514774,3707757)`
- **Get Devices Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.opcua/device` — List all Devices resources, including each resource’s name and… — `src:[4222033,4224910)`
- **Rename Devices** — `POST /data/api/v1/resources/rename/com.inductiveautomation.opcua/device/{name}` — Change the name of a Devices resource, and update all references… — `src:[4342251,4345208)`
- **Describe Devices Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.opcua/device` — Provide information about the Devices resource type, including… — `src:[4566031,4569538)`

## `server`

- **Modify OPC UA Server Config** — `PUT /data/api/v1/resources/com.inductiveautomation.opcua/server-config` — Modify one or more OPC UA Server Config resources — `src:[1064319,1071407)`
- **Create OPC UA Server Config** — `POST /data/api/v1/resources/com.inductiveautomation.opcua/server-config` — Create the OPC UA Server Config resource — `src:[1071415,1079327)`
- **Delete OPC UA Server Config** — `DELETE /data/api/v1/resources/com.inductiveautomation.opcua/server-config/{signature}` — Delete the OPC UA Server Config resource. — `src:[1079420,1081701)`
- **Get OPC UA Server Config Config** — `GET /data/api/v1/resources/singleton/com.inductiveautomation.opcua/server-config` — Retrieve configuration details about the OPC UA Server Config… — `src:[4444077,4448489)`
- **Describe OPC UA Server Config Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.opcua/server-config` — Provide information about the OPC UA Server Config resource type,… — `src:[4569621,4572393)`

## `server-pki-certificate-management`

- **Get Rejected Server Certificates** — `GET /data/opc-ua/api/v1/server/pki/certificates/rejected` — A list of all rejected server certificates. — `src:[4959346,4960220)`
- **Download Rejected Server Certificate** — `GET /data/opc-ua/api/v1/server/pki/certificates/rejected/{signature}` — Download a rejected server certificate with the given signature. — `src:[4960296,4961050)`
- **Delete Rejected Server Certificate** — `DELETE /data/opc-ua/api/v1/server/pki/certificates/rejected/{signature}` — Delete a rejected server certificate with the given signature. — `src:[4961060,4961827)`
- **Get Trusted Server Certificates** — `GET /data/opc-ua/api/v1/server/pki/certificates/trusted` — A list of all trusted server certificates. — `src:[4961890,4962761)`
- **Upload Trusted Server Certificates** — `POST /data/opc-ua/api/v1/server/pki/certificates/trusted` — Upload a trusted server certificate. — `src:[4962769,4963389)`
- **Download Trusted Server Certificate** — `GET /data/opc-ua/api/v1/server/pki/certificates/trusted/{signature}` — Download a trusted server certificate with the given signature. — `src:[4963464,4964214)`
- **Trust Rejected Server Certificate** — `POST /data/opc-ua/api/v1/server/pki/certificates/trusted/{signature}` — Trust a previously rejected server certificate with the given… — `src:[4964222,4964997)`
- **Delete Trusted Server Certificate** — `DELETE /data/opc-ua/api/v1/server/pki/certificates/trusted/{signature}` — Delete a trusted server certificate with the given signature. — `src:[4965007,4965771)`
