# Perspective

> Source SHA-256: `7ab205eff392dc38a547980fe94803c97f7c075102e7b983efc63f76b827b744`
> Endpoints: `50`

Read the matching endpoint line, then fetch only its `src:[start,end)` byte range from `./ignition-8.3-openapi.min.json`.

## `config-perspective-branding`

- **Modify Brand Customization** — `PUT /data/api/v1/resources/ignition/cobranding` — Modify one or more Brand Customization resources — `src:[2216934,2223114)`
- **Create Brand Customization** — `POST /data/api/v1/resources/ignition/cobranding` — Create the Brand Customization resource — `src:[2223122,2230126)`
- **Delete Brand Customization** — `DELETE /data/api/v1/resources/ignition/cobranding/{signature}` — Delete the Brand Customization resource. — `src:[2230195,2232495)`
- **Get Brand Customization Config** — `GET /data/api/v1/resources/singleton/ignition/cobranding` — Retrieve configuration details about the Brand Customization… — `src:[4451158,4454917)`
- **Describe Brand Customization Resource Type** — `GET /data/api/v1/resources/type/ignition/cobranding` — Provide information about the Brand Customization resource type,… — `src:[4595853,4598308)`

## `config-perspective-fonts`

- **Modify Fonts** — `PUT /data/api/v1/resources/com.inductiveautomation.perspective/fonts` — Modify one or more Fonts resources — `src:[1081777,1086107)`
- **Create Fonts** — `POST /data/api/v1/resources/com.inductiveautomation.perspective/fonts` — Create a new Fonts resource — `src:[1086115,1091284)`
- **Delete Fonts** — `DELETE /data/api/v1/resources/com.inductiveautomation.perspective/fonts/{name}/{signature}` — Delete a Fonts resource by name — `src:[1091382,1094125)`
- **Update Multiple Fonts Data Files** — `PUT /data/api/v1/resources/datafile/com.inductiveautomation.perspective/fonts/{name}` — Create or overwrite multiple data files for a named Fonts resource. — `src:[1176798,1179860)`
- **Get Fonts Data File** — `GET /data/api/v1/resources/datafile/com.inductiveautomation.perspective/fonts/{name}/{filename}` — Retrieve a data file from a specific Fonts resource — `src:[1179963,1181013)`
- **Update Fonts Data File** — `PUT /data/api/v1/resources/datafile/com.inductiveautomation.perspective/fonts/{name}/{filename}` — Update (overwrite) a data file for a named Fonts resource — `src:[1181020,1184412)`
- **Delete Fonts Data File** — `DELETE /data/api/v1/resources/datafile/com.inductiveautomation.perspective/fonts/{name}/{filename}` — Delete a data file for a named Fonts resource — `src:[1184422,1187510)`
- **Delete Fonts (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.perspective/fonts` — Delete multiple Fonts resources by name — `src:[1275557,1278181)`
- **Get Fonts Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.perspective/fonts/{name}` — Retrieve configuration details about a specific Fonts resource — `src:[1682775,1684499)`
- **List Fonts Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.perspective/fonts` — List all Fonts resources, in verbose format, including… — `src:[3707838,3711457)`
- **Get Fonts Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.perspective/fonts` — List all Fonts resources, including each resource’s name and… — `src:[4224992,4227883)`
- **Rename Fonts** — `POST /data/api/v1/resources/rename/com.inductiveautomation.perspective/fonts/{name}` — Change the name of a Fonts resource, and update all references to it — `src:[4345299,4348270)`
- **Describe Fonts Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.perspective/fonts` — Provide information about the Fonts resource type, including the… — `src:[4572474,4573853)`

## `config-perspective-icons`

- **Modify Icons** — `PUT /data/api/v1/resources/com.inductiveautomation.perspective/icons` — Modify one or more Icons resources — `src:[1094201,1099075)`
- **Create Icons** — `POST /data/api/v1/resources/com.inductiveautomation.perspective/icons` — Create a new Icons resource — `src:[1099083,1104796)`
- **Delete Icons** — `DELETE /data/api/v1/resources/com.inductiveautomation.perspective/icons/{name}/{signature}` — Delete a Icons resource by name — `src:[1104894,1107637)`
- **Update Multiple Icons Data Files** — `PUT /data/api/v1/resources/datafile/com.inductiveautomation.perspective/icons/{name}` — Create or overwrite multiple data files for a named Icons resource. — `src:[1187602,1190664)`
- **Get Icons Data File** — `GET /data/api/v1/resources/datafile/com.inductiveautomation.perspective/icons/{name}/{filename}` — Retrieve a data file from a specific Icons resource — `src:[1190767,1191817)`
- **Update Icons Data File** — `PUT /data/api/v1/resources/datafile/com.inductiveautomation.perspective/icons/{name}/{filename}` — Update (overwrite) a data file for a named Icons resource — `src:[1191824,1195216)`
- **Delete Icons Data File** — `DELETE /data/api/v1/resources/datafile/com.inductiveautomation.perspective/icons/{name}/{filename}` — Delete a data file for a named Icons resource — `src:[1195226,1198314)`
- **Delete Icons (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.perspective/icons` — Delete multiple Icons resources by name — `src:[1278265,1280889)`
- **Get Icons Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.perspective/icons/{name}` — Retrieve configuration details about a specific Icons resource — `src:[1684587,1686855)`
- **List Icons Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.perspective/icons` — List all Icons resources, in verbose format, including… — `src:[3711538,3715701)`
- **Get Icons Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.perspective/icons` — List all Icons resources, including each resource’s name and… — `src:[4227965,4230856)`
- **Rename Icons** — `POST /data/api/v1/resources/rename/com.inductiveautomation.perspective/icons/{name}` — Change the name of a Icons resource, and update all references to it — `src:[4348361,4351332)`
- **Describe Icons Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.perspective/icons` — Provide information about the Icons resource type, including the… — `src:[4573934,4575313)`

## `config-perspective-themes`

- **Modify Themes** — `PUT /data/api/v1/resources/com.inductiveautomation.perspective/themes` — Modify one or more Themes resources — `src:[1107714,1112901)`
- **Create Themes** — `POST /data/api/v1/resources/com.inductiveautomation.perspective/themes` — Create a new Themes resource — `src:[1112909,1118935)`
- **Delete Themes** — `DELETE /data/api/v1/resources/com.inductiveautomation.perspective/themes/{name}/{signature}` — Delete a Themes resource by name — `src:[1119034,1121780)`
- **Update Multiple Themes Data Files** — `PUT /data/api/v1/resources/datafile/com.inductiveautomation.perspective/themes/{name}` — Create or overwrite multiple data files for a named Themes resource. — `src:[1198407,1201472)`
- **Get Themes Data File** — `GET /data/api/v1/resources/datafile/com.inductiveautomation.perspective/themes/{name}/{filename}` — Retrieve a data file from a specific Themes resource — `src:[1201576,1202629)`
- **Update Themes Data File** — `PUT /data/api/v1/resources/datafile/com.inductiveautomation.perspective/themes/{name}/{filename}` — Update (overwrite) a data file for a named Themes resource — `src:[1202636,1206031)`
- **Delete Themes Data File** — `DELETE /data/api/v1/resources/datafile/com.inductiveautomation.perspective/themes/{name}/{filename}` — Delete a data file for a named Themes resource — `src:[1206041,1209132)`
- **Delete Themes (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.perspective/themes` — Delete multiple Themes resources by name — `src:[1280974,1283601)`
- **Get Themes Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.perspective/themes/{name}` — Retrieve configuration details about a specific Themes resource — `src:[1686944,1689525)`
- **List Themes Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.perspective/themes` — List all Themes resources, in verbose format, including… — `src:[3715783,3720259)`
- **Get Themes Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.perspective/themes` — List all Themes resources, including each resource’s name and… — `src:[4230939,4233833)`
- **Rename Themes** — `POST /data/api/v1/resources/rename/com.inductiveautomation.perspective/themes/{name}` — Change the name of a Themes resource, and update all references… — `src:[4351424,4354398)`
- **Describe Themes Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.perspective/themes` — Provide information about the Themes resource type, including the… — `src:[4575395,4576777)`
- **Copy Base Themes** — `POST /data/perspective/api/v1/themes/copy-base-themes` — Copies the base 'light' and 'dark' themes from system resources… — `src:[4980989,4981989)`

## `perspective-sessions`

- **Perspective Session Detail** — `GET /data/perspective/api/v1/session/{sessionId}` — Returns the details about a single session. — `src:[4968663,4969905)`
- **Perspective Views** — `GET /data/perspective/api/v1/session/{sessionId}/page/{pageId}/views` — Retrieves a list of all views active on a single page of a single… — `src:[4969981,4973389)`
- **Perspective Pages** — `GET /data/perspective/api/v1/session/{sessionId}/pages` — Retrieves a list of all pages in a session. — `src:[4973451,4976821)`
- **Terminate Perspective Session(s)** — `DELETE /data/perspective/api/v1/sessions` — Terminates one or more perspective sessions. — `src:[4976869,4977778)`
- **Perspective Sessions** — `GET /data/perspective/api/v1/sessions/` — Retrieves a list of all active perspective sessions. — `src:[4977824,4980928)`
