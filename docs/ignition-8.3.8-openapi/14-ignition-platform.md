# Ignition Platform

> Source SHA-256: `7ab205eff392dc38a547980fe94803c97f7c075102e7b983efc63f76b827b744`
> Endpoints: `400`

Read the matching endpoint line, then fetch only its `src:[start,end)` byte range from `./ignition-8.3-openapi.min.json`.

## `alarm-journal-resources`

- **Delete Alarm Journal Settings (multiple)** — `POST /data/api/v1/resources/delete/ignition/alarm-journal` — Delete multiple Alarm Journal Settings resources by name — `src:[1286388,1289045)`
- **Get Alarm Journal Settings Config** — `GET /data/api/v1/resources/find/ignition/alarm-journal/{name}` — Retrieve configuration details about a specific Alarm Journal… — `src:[1698332,1722399)`
- **Modify Alarm Journal Settings** — `PUT /data/api/v1/resources/ignition/alarm-journal` — Modify one or more Alarm Journal Settings resources — `src:[2105595,2132523)`
- **Create Alarm Journal Settings** — `POST /data/api/v1/resources/ignition/alarm-journal` — Create a new Alarm Journal Settings resource — `src:[2132531,2160298)`
- **Delete Alarm Journal Settings** — `DELETE /data/api/v1/resources/ignition/alarm-journal/{name}/{signature}` — Delete a Alarm Journal Settings resource by name — `src:[2160377,2163153)`
- **List Alarm Journal Settings Resources** — `GET /data/api/v1/resources/list/ignition/alarm-journal` — List all Alarm Journal Settings resources, in verbose format,… — `src:[3730947,3756909)`
- **Get Alarm Journal Settings Names** — `GET /data/api/v1/resources/names/ignition/alarm-journal` — List all Alarm Journal Settings resources, including each… — `src:[4236883,4239807)`
- **Rename Alarm Journal Settings** — `POST /data/api/v1/resources/rename/ignition/alarm-journal/{name}` — Change the name of a Alarm Journal Settings resource, and update… — `src:[4354470,4357474)`
- **Describe Alarm Journal Settings Resource Type** — `GET /data/api/v1/resources/type/ignition/alarm-journal` — Provide information about the Alarm Journal Settings resource type… — `src:[4583769,4587323)`

## `api-token`

- **Generate API Token** — `POST /data/api/v1/api-token/generate` — Generates a key and hash pair suitable for a new API Token. — `src:[18218,19844)`

## `audit`

- **Audit Log** — `GET /data/api/v1/audit/log/{name}` — Retrieve audit events for a given audit profile — `src:[19885,26022)`
- **Get Remote Audit Profile Names** — `GET /data/api/v1/audit/remote-profiles/{serverId}` — Returns a list of available audit log profile names for the… — `src:[26079,29232)`

## `config-alarm`

- **Delete Holiday (multiple)** — `POST /data/api/v1/resources/delete/ignition/holiday` — Delete multiple Holiday resources by name — `src:[1310825,1313441)`
- **Delete Schedule (multiple)** — `POST /data/api/v1/resources/delete/ignition/schedule` — Delete multiple Schedule resources by name — `src:[1329730,1332348)`
- **Get Holiday Config** — `GET /data/api/v1/resources/find/ignition/holiday/{name}` — Retrieve configuration details about a specific Holiday resource — `src:[1806030,1808060)`
- **Get Schedule Config** — `GET /data/api/v1/resources/find/ignition/schedule/{name}` — Retrieve configuration details about a specific Schedule resource — `src:[1937941,1945805)`
- **Modify Holiday** — `PUT /data/api/v1/resources/ignition/holiday` — Modify one or more Holiday resources — `src:[2484140,2488776)`
- **Create Holiday** — `POST /data/api/v1/resources/ignition/holiday` — Create a new Holiday resource — `src:[2488784,2494259)`
- **Delete Holiday** — `DELETE /data/api/v1/resources/ignition/holiday/{name}/{signature}` — Delete a Holiday resource by name — `src:[2494332,2497067)`
- **Modify Schedule** — `PUT /data/api/v1/resources/ignition/schedule` — Modify one or more Schedule resources — `src:[2835547,2845448)`
- **Create Schedule** — `POST /data/api/v1/resources/ignition/schedule` — Create a new Schedule resource — `src:[2845456,2856196)`
- **Delete Schedule** — `DELETE /data/api/v1/resources/ignition/schedule/{name}/{signature}` — Delete a Schedule resource by name — `src:[2856270,2859007)`
- **List Holiday Resources** — `GET /data/api/v1/resources/list/ignition/holiday` — List all Holiday resources, in verbose format, including… — `src:[3855637,3859562)`
- **List Schedule Resources** — `GET /data/api/v1/resources/list/ignition/schedule` — List all Schedule resources, in verbose format, including… — `src:[4000764,4010523)`
- **Get Holiday Names** — `GET /data/api/v1/resources/names/ignition/holiday` — List all Holiday resources, including each resource’s name and… — `src:[4263705,4266588)`
- **Get Schedule Names** — `GET /data/api/v1/resources/names/ignition/schedule` — List all Schedule resources, including each resource’s name and… — `src:[4284465,4287350)`
- **Rename Holiday** — `POST /data/api/v1/resources/rename/ignition/holiday/{name}` — Change the name of a Holiday resource, and update all references… — `src:[4378995,4381958)`
- **Rename Schedule** — `POST /data/api/v1/resources/rename/ignition/schedule/{name}` — Change the name of a Schedule resource, and update all references… — `src:[4400378,4403343)`
- **Describe Holiday Resource Type** — `GET /data/api/v1/resources/type/ignition/holiday` — Provide information about the Holiday resource type, including… — `src:[4625929,4627300)`
- **Describe Schedule Resource Type** — `GET /data/api/v1/resources/type/ignition/schedule` — Provide information about the Schedule resource type, including… — `src:[4644813,4648328)`

## `config-api-token`

- **Delete API Token (multiple)** — `POST /data/api/v1/resources/delete/ignition/api-token` — Delete multiple API Token resources by name — `src:[1289106,1291730)`
- **Get API Token Config** — `GET /data/api/v1/resources/find/ignition/api-token/{name}` — Retrieve configuration details about a specific API Token resource — `src:[1722464,1726578)`
- **Modify API Token** — `PUT /data/api/v1/resources/ignition/api-token` — Modify one or more API Token resources — `src:[2163206,2169926)`
- **Create API Token** — `POST /data/api/v1/resources/ignition/api-token` — Create a new API Token resource — `src:[2169934,2177493)`
- **Delete API Token** — `DELETE /data/api/v1/resources/ignition/api-token/{name}/{signature}` — Delete a API Token resource by name — `src:[2177568,2180311)`
- **List API Token Resources** — `GET /data/api/v1/resources/list/ignition/api-token` — List all API Token resources, in verbose format, including… — `src:[3756967,3762976)`
- **Get API Token Names** — `GET /data/api/v1/resources/names/ignition/api-token` — List all API Token resources, including each resource’s name and… — `src:[4239866,4242757)`
- **Rename API Token** — `POST /data/api/v1/resources/rename/ignition/api-token/{name}` — Change the name of a API Token resource, and update all… — `src:[4357542,4360513)`
- **Describe API Token Resource Type** — `GET /data/api/v1/resources/type/ignition/api-token` — Provide information about the API Token resource type, including… — `src:[4587381,4592196)`

## `config-audit-profiles`

- **Delete Audit Profiles (multiple)** — `POST /data/api/v1/resources/delete/ignition/audit-profile` — Delete multiple Audit Profiles resources by name — `src:[1291795,1294434)`
- **Get Audit Profiles Config** — `GET /data/api/v1/resources/find/ignition/audit-profile/{name}` — Retrieve configuration details about a specific Audit Profiles… — `src:[1726647,1740200)`
- **Modify Audit Profiles** — `PUT /data/api/v1/resources/ignition/audit-profile` — Modify one or more Audit Profiles resources — `src:[2180368,2196782)`
- **Create Audit Profiles** — `POST /data/api/v1/resources/ignition/audit-profile` — Create a new Audit Profiles resource — `src:[2196790,2214043)`
- **Delete Audit Profiles** — `DELETE /data/api/v1/resources/ignition/audit-profile/{name}/{signature}` — Delete a Audit Profiles resource by name — `src:[2214122,2216880)`
- **List Audit Profiles Resources** — `GET /data/api/v1/resources/list/ignition/audit-profile` — List all Audit Profiles resources, in verbose format, including… — `src:[3763038,3778486)`
- **Get Audit Profiles Names** — `GET /data/api/v1/resources/names/ignition/audit-profile` — List all Audit Profiles resources, including each resource’s name… — `src:[4242820,4245726)`
- **Rename Audit Profiles** — `POST /data/api/v1/resources/rename/ignition/audit-profile/{name}` — Change the name of a Audit Profiles resource, and update all… — `src:[4360585,4363571)`
- **Describe Audit Profiles Resource Type** — `GET /data/api/v1/resources/type/ignition/audit-profile` — Provide information about the Audit Profiles resource type,… — `src:[4592258,4595794)`

## `config-databases`

- **Update Multiple JDBC Driver Data Files** — `PUT /data/api/v1/resources/datafile/ignition/database-driver/{name}` — Create or overwrite multiple data files for a named JDBC Driver… — `src:[1209207,1212273)`
- **Get JDBC Driver Data File** — `GET /data/api/v1/resources/datafile/ignition/database-driver/{name}/{filename}` — Retrieve a data file from a specific JDBC Driver resource — `src:[1212359,1213413)`
- **Update JDBC Driver Data File** — `PUT /data/api/v1/resources/datafile/ignition/database-driver/{name}/{filename}` — Update (overwrite) a data file for a named JDBC Driver resource — `src:[1213420,1216816)`
- **Delete JDBC Driver Data File** — `DELETE /data/api/v1/resources/datafile/ignition/database-driver/{name}/{filename}` — Delete a data file for a named JDBC Driver resource — `src:[1216826,1219918)`
- **Delete Database Connection (multiple)** — `POST /data/api/v1/resources/delete/ignition/database-connection` — Delete multiple Database Connection resources by name — `src:[1294505,1297149)`
- **Delete JDBC Driver (multiple)** — `POST /data/api/v1/resources/delete/ignition/database-driver` — Delete multiple JDBC Driver resources by name — `src:[1297216,1299844)`
- **Delete Database Translator (multiple)** — `POST /data/api/v1/resources/delete/ignition/database-translator` — Delete multiple Database Translator resources by name — `src:[1299915,1302559)`
- **Get Database Connection Config** — `GET /data/api/v1/resources/find/ignition/database-connection/{name}` — Retrieve configuration details about a specific Database… — `src:[1740275,1762450)`
- **Get JDBC Driver Config** — `GET /data/api/v1/resources/find/ignition/database-driver/{name}` — Retrieve configuration details about a specific JDBC Driver resource — `src:[1762521,1766303)`
- **Get Database Translator Config** — `GET /data/api/v1/resources/find/ignition/database-translator/{name}` — Retrieve configuration details about a specific Database… — `src:[1766378,1771470)`
- **Modify Database Connection** — `PUT /data/api/v1/resources/ignition/database-connection` — Modify one or more Database Connection resources — `src:[2232558,2254617)`
- **Create Database Connection** — `POST /data/api/v1/resources/ignition/database-connection` — Create a new Database Connection resource — `src:[2254625,2277523)`
- **Delete Database Connection** — `DELETE /data/api/v1/resources/ignition/database-connection/{name}/{signature}` — Delete a Database Connection resource by name — `src:[2277608,2280371)`
- **Modify JDBC Driver** — `PUT /data/api/v1/resources/ignition/database-driver` — Modify one or more JDBC Driver resources — `src:[2280430,2286249)`
- **Create JDBC Driver** — `POST /data/api/v1/resources/ignition/database-driver` — Create a new JDBC Driver resource — `src:[2286257,2292915)`
- **Delete JDBC Driver** — `DELETE /data/api/v1/resources/ignition/database-driver/{name}/{signature}` — Delete a JDBC Driver resource by name — `src:[2292996,2295743)`
- **Modify Database Translator** — `PUT /data/api/v1/resources/ignition/database-translator` — Modify one or more Database Translator resources — `src:[2295806,2303504)`
- **Create Database Translator** — `POST /data/api/v1/resources/ignition/database-translator` — Create a new Database Translator resource — `src:[2303512,2312049)`
- **Delete Database Translator** — `DELETE /data/api/v1/resources/ignition/database-translator/{name}/{signature}` — Delete a Database Translator resource by name — `src:[2312134,2314897)`
- **List Database Connection Resources** — `GET /data/api/v1/resources/list/ignition/database-connection` — List all Database Connection resources, in verbose format,… — `src:[3778554,3802624)`
- **List JDBC Driver Resources** — `GET /data/api/v1/resources/list/ignition/database-driver` — List all JDBC Driver resources, in verbose format, including… — `src:[3802688,3808365)`
- **List Database Translator Resources** — `GET /data/api/v1/resources/list/ignition/database-translator` — List all Database Translator resources, in verbose format,… — `src:[3808433,3815420)`
- **Get Database Connection Names** — `GET /data/api/v1/resources/names/ignition/database-connection` — List all Database Connection resources, including each resource’s… — `src:[4245795,4248706)`
- **Get JDBC Driver Names** — `GET /data/api/v1/resources/names/ignition/database-driver` — List all JDBC Driver resources, including each resource’s name… — `src:[4248771,4251666)`
- **Get Database Translator Names** — `GET /data/api/v1/resources/names/ignition/database-translator` — List all Database Translator resources, including each resource’s… — `src:[4251735,4254646)`
- **Rename Database Connection** — `POST /data/api/v1/resources/rename/ignition/database-connection/{name}` — Change the name of a Database Connection resource, and update all… — `src:[4363649,4366640)`
- **Rename JDBC Driver** — `POST /data/api/v1/resources/rename/ignition/database-driver/{name}` — Change the name of a JDBC Driver resource, and update all… — `src:[4366714,4369689)`
- **Rename Database Translator** — `POST /data/api/v1/resources/rename/ignition/database-translator/{name}` — Change the name of a Database Translator resource, and update all… — `src:[4369767,4372758)`
- **Describe Database Connection Resource Type** — `GET /data/api/v1/resources/type/ignition/database-connection` — Provide information about the Database Connection resource type,… — `src:[4598376,4601252)`
- **Describe JDBC Driver Resource Type** — `GET /data/api/v1/resources/type/ignition/database-driver` — Provide information about the JDBC Driver resource type,… — `src:[4601316,4602699)`
- **Describe Database Translator Resource Type** — `GET /data/api/v1/resources/type/ignition/database-translator` — Provide information about the Database Translator resource type,… — `src:[4602767,4604166)`

## `config-edge-system-properties`

- **Modify Edge System Properties** — `PUT /data/api/v1/resources/ignition/edge-system-properties` — Modify one or more Edge System Properties resources — `src:[2314963,2320902)`
- **Create Edge System Properties** — `POST /data/api/v1/resources/ignition/edge-system-properties` — Create the Edge System Properties resource — `src:[2320910,2327673)`
- **Delete Edge System Properties** — `DELETE /data/api/v1/resources/ignition/edge-system-properties/{signature}` — Delete the Edge System Properties resource. — `src:[2327754,2330062)`
- **Get Edge System Properties Config** — `GET /data/api/v1/resources/singleton/ignition/edge-system-properties` — Retrieve configuration details about the Edge System Properties… — `src:[4454993,4458256)`
- **Describe Edge System Properties Resource Type** — `GET /data/api/v1/resources/type/ignition/edge-system-properties` — Provide information about the Edge System Properties resource type… — `src:[4604237,4606448)`

## `config-email-profile`

- **Delete Email Profile Settings (multiple)** — `POST /data/api/v1/resources/delete/ignition/email-profile` — Delete multiple Email Profile Settings resources by name — `src:[1302624,1305278)`
- **Get Email Profile Settings Config** — `GET /data/api/v1/resources/find/ignition/email-profile/{name}` — Retrieve configuration details about a specific Email Profile… — `src:[1771539,1796613)`
- **Modify Email Profile Settings** — `PUT /data/api/v1/resources/ignition/email-profile` — Modify one or more Email Profile Settings resources — `src:[2330119,2357230)`
- **Create Email Profile Settings** — `POST /data/api/v1/resources/ignition/email-profile` — Create a new Email Profile Settings resource — `src:[2357238,2385188)`
- **Delete Email Profile Settings** — `DELETE /data/api/v1/resources/ignition/email-profile/{name}/{signature}` — Delete a Email Profile Settings resource by name — `src:[2385267,2388040)`
- **List Email Profile Settings Resources** — `GET /data/api/v1/resources/list/ignition/email-profile` — List all Email Profile Settings resources, in verbose format,… — `src:[3815482,3842451)`
- **Get Email Profile Settings Names** — `GET /data/api/v1/resources/names/ignition/email-profile` — List all Email Profile Settings resources, including each… — `src:[4254709,4257630)`
- **Rename Email Profile Settings** — `POST /data/api/v1/resources/rename/ignition/email-profile/{name}` — Change the name of a Email Profile Settings resource, and update… — `src:[4372830,4375831)`
- **Describe Email Profile Settings Resource Type** — `GET /data/api/v1/resources/type/ignition/email-profile` — Provide information about the Email Profile Settings resource type… — `src:[4606510,4610061)`

## `config-gateway-network`

- **Delete Incoming Connection Settings (multiple)** — `POST /data/api/v1/resources/delete/ignition/gateway-network-incoming` — Delete multiple Incoming Connection Settings resources by name — `src:[1305354,1308022)`
- **Delete Outgoing Connection Settings (multiple)** — `POST /data/api/v1/resources/delete/ignition/gateway-network-outgoing` — Delete multiple Outgoing Connection Settings resources by name — `src:[1308098,1310766)`
- **Get Incoming Connection Settings Config** — `GET /data/api/v1/resources/find/ignition/gateway-network-incoming/{name}` — Retrieve configuration details about a specific Incoming… — `src:[1796693,1799681)`
- **Get Outgoing Connection Settings Config** — `GET /data/api/v1/resources/find/ignition/gateway-network-outgoing/{name}` — Retrieve configuration details about a specific Outgoing… — `src:[1799761,1805967)`
- **Delete Incoming Connection Settings** — `DELETE /data/api/v1/resources/ignition/gateway-network-incoming/{name}/{signature}` — Delete a Incoming Connection Settings resource by name — `src:[2388130,2390917)`
- **Modify Outgoing Connection Settings** — `PUT /data/api/v1/resources/ignition/gateway-network-outgoing` — Modify one or more Outgoing Connection Settings resources — `src:[2390985,2398973)`
- **Create Outgoing Connection Settings** — `POST /data/api/v1/resources/ignition/gateway-network-outgoing` — Create a new Outgoing Connection Settings resource — `src:[2398981,2407808)`
- **Delete Outgoing Connection Settings** — `DELETE /data/api/v1/resources/ignition/gateway-network-outgoing/{name}/{signature}` — Delete a Outgoing Connection Settings resource by name — `src:[2407898,2410685)`
- **Modify Proxy Rules** — `PUT /data/api/v1/resources/ignition/gateway-network-proxy-rules` — Modify one or more Proxy Rules resources — `src:[2410756,2416839)`
- **Create Proxy Rules** — `POST /data/api/v1/resources/ignition/gateway-network-proxy-rules` — Create the Proxy Rules resource — `src:[2416847,2423754)`
- **Delete Proxy Rules** — `DELETE /data/api/v1/resources/ignition/gateway-network-proxy-rules/{signature}` — Delete the Proxy Rules resource. — `src:[2423840,2426119)`
- **Modify Queue Settings** — `PUT /data/api/v1/resources/ignition/gateway-network-queue-settings` — Modify one or more Queue Settings resources — `src:[2426193,2431198)`
- **Create Queue Settings** — `POST /data/api/v1/resources/ignition/gateway-network-queue-settings` — Create the Queue Settings resource — `src:[2431206,2437035)`
- **Delete Queue Settings** — `DELETE /data/api/v1/resources/ignition/gateway-network-queue-settings/{signature}` — Delete the Queue Settings resource. — `src:[2437124,2439409)`
- **Modify General Gateway Network Settings** — `PUT /data/api/v1/resources/ignition/gateway-network-settings` — Modify one or more General Gateway Network Settings resources — `src:[2439477,2452502)`
- **Create General Gateway Network Settings** — `POST /data/api/v1/resources/ignition/gateway-network-settings` — Create the General Gateway Network Settings resource — `src:[2452510,2466359)`
- **Delete General Gateway Network Settings** — `DELETE /data/api/v1/resources/ignition/gateway-network-settings/{signature}` — Delete the General Gateway Network Settings resource. — `src:[2466442,2468763)`
- **List Incoming Connection Settings Resources** — `GET /data/api/v1/resources/list/ignition/gateway-network-incoming` — List all Incoming Connection Settings resources, in verbose format… — `src:[3842524,3847407)`
- **List Outgoing Connection Settings Resources** — `GET /data/api/v1/resources/list/ignition/gateway-network-outgoing` — List all Outgoing Connection Settings resources, in verbose format… — `src:[3847480,3855581)`
- **Get Incoming Connection Settings Names** — `GET /data/api/v1/resources/names/ignition/gateway-network-incoming` — List all Incoming Connection Settings resources, including each… — `src:[4257704,4260639)`
- **Get Outgoing Connection Settings Names** — `GET /data/api/v1/resources/names/ignition/gateway-network-outgoing` — List all Outgoing Connection Settings resources, including each… — `src:[4260713,4263648)`
- **Rename Outgoing Connection Settings** — `POST /data/api/v1/resources/rename/ignition/gateway-network-outgoing/{name}` — Change the name of a Outgoing Connection Settings resource, and… — `src:[4375914,4378929)`
- **Get Proxy Rules Config** — `GET /data/api/v1/resources/singleton/ignition/gateway-network-proxy-rules` — Retrieve configuration details about the Proxy Rules resource — `src:[4458337,4461999)`
- **Get Queue Settings Config** — `GET /data/api/v1/resources/singleton/ignition/gateway-network-queue-settings` — Retrieve configuration details about the Queue Settings resource — `src:[4462083,4464667)`
- **Get General Gateway Network Settings Config** — `GET /data/api/v1/resources/singleton/ignition/gateway-network-settings` — Retrieve configuration details about the General Gateway Network… — `src:[4464745,4475349)`
- **Describe Incoming Connection Settings Resource Type** — `GET /data/api/v1/resources/type/ignition/gateway-network-incoming` — Provide information about the Incoming Connection Settings… — `src:[4610134,4611557)`
- **Describe Outgoing Connection Settings Resource Type** — `GET /data/api/v1/resources/type/ignition/gateway-network-outgoing` — Provide information about the Outgoing Connection Settings… — `src:[4611630,4613053)`
- **Describe Proxy Rules Resource Type** — `GET /data/api/v1/resources/type/ignition/gateway-network-proxy-rules` — Provide information about the Proxy Rules resource type,… — `src:[4613129,4615525)`
- **Describe Queue Settings Resource Type** — `GET /data/api/v1/resources/type/ignition/gateway-network-queue-settings` — Provide information about the Queue Settings resource type,… — `src:[4615604,4617464)`
- **Describe General Gateway Network Settings Resource Type** — `GET /data/api/v1/resources/type/ignition/gateway-network-settings` — Provide information about the General Gateway Network Settings… — `src:[4617537,4623425)`

## `config-identity-provider`

- **Delete Identity Provider (multiple)** — `POST /data/api/v1/resources/delete/ignition/identity-provider` — Delete multiple Identity Provider resources by name — `src:[1313510,1316158)`
- **Get Identity Provider Config** — `GET /data/api/v1/resources/find/ignition/identity-provider/{name}` — Retrieve configuration details about a specific Identity Provider… — `src:[1808133,1878557)`
- **Modify Identity Provider** — `PUT /data/api/v1/resources/ignition/identity-provider` — Modify one or more Identity Provider resources — `src:[2497128,2570413)`
- **Create Identity Provider** — `POST /data/api/v1/resources/ignition/identity-provider` — Create a new Identity Provider resource — `src:[2570421,2644545)`
- **Delete Identity Provider** — `DELETE /data/api/v1/resources/ignition/identity-provider/{name}/{signature}` — Delete a Identity Provider resource by name — `src:[2644628,2647395)`
- **List Identity Provider Resources** — `GET /data/api/v1/resources/list/ignition/identity-provider` — List all Identity Provider resources, in verbose format,… — `src:[3859628,3931947)`
- **Get Identity Provider Names** — `GET /data/api/v1/resources/names/ignition/identity-provider` — List all Identity Provider resources, including each resource’s… — `src:[4266655,4269570)`
- **Rename Identity Provider** — `POST /data/api/v1/resources/rename/ignition/identity-provider/{name}` — Change the name of a Identity Provider resource, and update all… — `src:[4382034,4385029)`
- **Describe Identity Provider Resource Type** — `GET /data/api/v1/resources/type/ignition/identity-provider` — Provide information about the Identity Provider resource type,… — `src:[4627366,4630911)`

## `config-keyboard-layouts`

- **Delete Keyboard Layouts (multiple)** — `POST /data/api/v1/resources/delete/ignition/keyboard_layout` — Delete multiple Keyboard Layouts resources by name — `src:[1316225,1318870)`
- **Get Keyboard Layouts Config** — `GET /data/api/v1/resources/find/ignition/keyboard_layout/{name}` — Retrieve configuration details about a specific Keyboard Layouts… — `src:[1878628,1888359)`
- **Modify Keyboard Layouts** — `PUT /data/api/v1/resources/ignition/keyboard_layout` — Modify one or more Keyboard Layouts resources — `src:[2647454,2659791)`
- **Create Keyboard Layouts** — `POST /data/api/v1/resources/ignition/keyboard_layout` — Create a new Keyboard Layouts resource — `src:[2659799,2672975)`
- **Delete Keyboard Layouts** — `DELETE /data/api/v1/resources/ignition/keyboard_layout/{name}/{signature}` — Delete a Keyboard Layouts resource by name — `src:[2673056,2675820)`
- **List Keyboard Layouts Resources** — `GET /data/api/v1/resources/list/ignition/keyboard_layout` — List all Keyboard Layouts resources, in verbose format, including… — `src:[3932011,3943637)`
- **Get Keyboard Layouts Names** — `GET /data/api/v1/resources/names/ignition/keyboard_layout` — List all Keyboard Layouts resources, including each resource’s… — `src:[4269635,4272547)`
- **Rename Keyboard Layouts** — `POST /data/api/v1/resources/rename/ignition/keyboard_layout/{name}` — Change the name of a Keyboard Layouts resource, and update all… — `src:[4385103,4388095)`
- **Describe Keyboard Layouts Resource Type** — `GET /data/api/v1/resources/type/ignition/keyboard_layout` — Provide information about the Keyboard Layouts resource type,… — `src:[4630975,4632375)`

## `config-local-system-properties`

- **Modify Local System Properties** — `PUT /data/api/v1/resources/ignition/local-system-properties` — Modify one or more Local System Properties resources — `src:[2675887,2680766)`
- **Create Local System Properties** — `POST /data/api/v1/resources/ignition/local-system-properties` — Create the Local System Properties resource — `src:[2680774,2686477)`
- **Delete Local System Properties** — `DELETE /data/api/v1/resources/ignition/local-system-properties/{signature}` — Delete the Local System Properties resource. — `src:[2686559,2688870)`
- **Get Local System Properties Config** — `GET /data/api/v1/resources/singleton/ignition/local-system-properties` — Retrieve configuration details about the Local System Properties… — `src:[4479104,4481562)`
- **Describe Local System Properties Resource Type** — `GET /data/api/v1/resources/type/ignition/local-system-properties` — Provide information about the Local System Properties resource… — `src:[4632447,4634257)`

## `config-management`

- **Deployment Modes** — `GET /data/api/v1/mode` — Returns a list of the available deployment modes — `src:[126075,129114)`
- **Create Deployment Mode** — `POST /data/api/v1/mode` — Creates a new deployment mode resource collection. — `src:[129122,130458)`
- **Update Deployment Mode** — `PUT /data/api/v1/mode/{name}` — Updates or renames a deployment mode. — `src:[130494,132098)`
- **Delete Deployment Mode** — `DELETE /data/api/v1/mode/{name}` — Deletes a deployment mode — `src:[132108,132865)`
- **Copy a Resource** — `POST /data/api/v1/resources/copy` — Copies a resource from one collection to another, or to a… — `src:[1161243,1165968)`
- **Move a Resource** — `POST /data/api/v1/resources/move` — Moves a resource to a different collection. — `src:[4181624,4186052)`
- **Configuration Scan Lock Info** — `GET /data/api/v1/scan-lock/config` — Returns information about the currently held scan-lock, if any. — `src:[4754686,4755336)`
- **Acquire Configuration Scan Lock** — `POST /data/api/v1/scan-lock/config` — Prevents changes from being applied to the configuration system… — `src:[4755344,4757012)`
- **Project Scan Lock Info** — `GET /data/api/v1/scan-lock/projects` — Returns information about the currently held scan-lock, if any. — `src:[4757055,4757699)`
- **Acquire Project Scan Lock** — `POST /data/api/v1/scan-lock/projects` — Prevents changes from being applied to the project system for a… — `src:[4757707,4759365)`
- **Configuration Scan Status** — `GET /data/api/v1/scan/config` — Returns the status of the configuration filesystem scan. — `src:[4759401,4759914)`
- **Request Configuration Scan** — `POST /data/api/v1/scan/config` — Prompts the system to scan the filesystem for configuration changes. — `src:[4759922,4760507)`
- **Project Scan Status** — `GET /data/api/v1/scan/projects` — Returns the status of the project filesystem scan. — `src:[4760545,4761046)`
- **Request Project Scan** — `POST /data/api/v1/scan/projects` — Prompts the system to scan the filesystem for project changes. — `src:[4761054,4761627)`

## `config-metrics-dashboard`

- **Delete Metrics Dashboard (multiple)** — `POST /data/api/v1/resources/delete/ignition/metrics-dashboard` — Delete multiple Metrics Dashboard resources by name — `src:[1318939,1321587)`
- **Get Metrics Dashboard Config** — `GET /data/api/v1/resources/find/ignition/metrics-dashboard/{name}` — Retrieve configuration details about a specific Metrics Dashboard… — `src:[1888432,1892626)`
- **Modify Metrics Dashboard** — `PUT /data/api/v1/resources/ignition/metrics-dashboard` — Modify one or more Metrics Dashboard resources — `src:[2688931,2695731)`
- **Create Metrics Dashboard** — `POST /data/api/v1/resources/ignition/metrics-dashboard` — Create a new Metrics Dashboard resource — `src:[2695739,2703378)`
- **Delete Metrics Dashboard** — `DELETE /data/api/v1/resources/ignition/metrics-dashboard/{name}/{signature}` — Delete a Metrics Dashboard resource by name — `src:[2703461,2706228)`
- **List Metrics Dashboard Resources** — `GET /data/api/v1/resources/list/ignition/metrics-dashboard` — List all Metrics Dashboard resources, in verbose format,… — `src:[3943703,3949792)`
- **Get Metrics Dashboard Names** — `GET /data/api/v1/resources/names/ignition/metrics-dashboard` — List all Metrics Dashboard resources, including each resource’s… — `src:[4272614,4275529)`
- **Rename Metrics Dashboard** — `POST /data/api/v1/resources/rename/ignition/metrics-dashboard/{name}` — Change the name of a Metrics Dashboard resource, and update all… — `src:[4388171,4391166)`
- **Describe Metrics Dashboard Resource Type** — `GET /data/api/v1/resources/type/ignition/metrics-dashboard` — Provide information about the Metrics Dashboard resource type,… — `src:[4634323,4635726)`

## `config-oauth2-client`

- **Delete OAuth2 Client (multiple)** — `POST /data/api/v1/resources/delete/ignition/oauth2-client` — Delete multiple OAuth2 Client resources by name — `src:[1321652,1324288)`
- **Get OAuth2 Client Config** — `GET /data/api/v1/resources/find/ignition/oauth2-client/{name}` — Retrieve configuration details about a specific OAuth2 Client… — `src:[1892695,1905063)`
- **Modify OAuth2 Client** — `PUT /data/api/v1/resources/ignition/oauth2-client` — Modify one or more OAuth2 Client resources — `src:[2706285,2721514)`
- **Create OAuth2 Client** — `POST /data/api/v1/resources/ignition/oauth2-client` — Create a new OAuth2 Client resource — `src:[2721522,2737590)`
- **Delete OAuth2 Client** — `DELETE /data/api/v1/resources/ignition/oauth2-client/{name}/{signature}` — Delete a OAuth2 Client resource by name — `src:[2737669,2740424)`
- **List OAuth2 Client Resources** — `GET /data/api/v1/resources/list/ignition/oauth2-client` — List all OAuth2 Client resources, in verbose format, including… — `src:[3949854,3964117)`
- **Get OAuth2 Client Names** — `GET /data/api/v1/resources/names/ignition/oauth2-client` — List all OAuth2 Client resources, including each resource’s name… — `src:[4275592,4278495)`
- **Rename OAuth2 Client** — `POST /data/api/v1/resources/rename/ignition/oauth2-client/{name}` — Change the name of a OAuth2 Client resource, and update all… — `src:[4391238,4394221)`
- **Describe OAuth2 Client Resource Type** — `GET /data/api/v1/resources/type/ignition/oauth2-client` — Provide information about the OAuth2 Client resource type,… — `src:[4635788,4637179)`

## `config-secret-provider`

- **Delete Secret Provider (multiple)** — `POST /data/api/v1/resources/delete/ignition/secret-provider` — Delete multiple Secret Provider resources by name — `src:[1332415,1335057)`
- **Get Secret Provider Config** — `GET /data/api/v1/resources/find/ignition/secret-provider/{name}` — Retrieve configuration details about a specific Secret Provider… — `src:[1945876,1954878)`
- **Modify Secret Provider** — `PUT /data/api/v1/resources/ignition/secret-provider` — Modify one or more Secret Provider resources — `src:[2859066,2870674)`
- **Create Secret Provider** — `POST /data/api/v1/resources/ignition/secret-provider` — Create a new Secret Provider resource — `src:[2870682,2883129)`
- **Delete Secret Provider** — `DELETE /data/api/v1/resources/ignition/secret-provider/{name}/{signature}` — Delete a Secret Provider resource by name — `src:[2883210,2885971)`
- **List Secret Provider Resources** — `GET /data/api/v1/resources/list/ignition/secret-provider` — List all Secret Provider resources, in verbose format, including… — `src:[4010587,4021484)`
- **Get Secret Provider Names** — `GET /data/api/v1/resources/names/ignition/secret-provider` — List all Secret Provider resources, including each resource’s… — `src:[4287415,4290324)`
- **Rename Secret Provider** — `POST /data/api/v1/resources/rename/ignition/secret-provider/{name}` — Change the name of a Secret Provider resource, and update all… — `src:[4403417,4406406)`
- **Describe Secret Provider Resource Type** — `GET /data/api/v1/resources/type/ignition/secret-provider` — Provide information about the Secret Provider resource type,… — `src:[4648392,4651931)`

## `config-security-levels`

- **Modify Security Levels** — `PUT /data/api/v1/resources/ignition/security-levels` — Modify one or more Security Levels resources — `src:[2886030,2890565)`
- **Create Security Levels** — `POST /data/api/v1/resources/ignition/security-levels` — Create the Security Levels resource — `src:[2890573,2895932)`
- **Delete Security Levels** — `DELETE /data/api/v1/resources/ignition/security-levels/{signature}` — Delete the Security Levels resource. — `src:[2896006,2898293)`
- **Get Security Levels Config** — `GET /data/api/v1/resources/singleton/ignition/security-levels` — Retrieve configuration details about the Security Levels resource — `src:[4484599,4486713)`
- **Describe Security Levels Resource Type** — `GET /data/api/v1/resources/type/ignition/security-levels` — Provide information about the Security Levels resource type,… — `src:[4651995,4653621)`

## `config-security-properties`

- **Modify Security Properties** — `PUT /data/api/v1/resources/ignition/security-properties` — Modify one or more Security Properties resources — `src:[2898356,2915712)`
- **Create Security Properties** — `POST /data/api/v1/resources/ignition/security-properties` — Create the Security Properties resource — `src:[2915720,2933900)`
- **Delete Security Properties** — `DELETE /data/api/v1/resources/ignition/security-properties/{signature}` — Delete the Security Properties resource. — `src:[2933978,2936277)`
- **Get Security Properties Config** — `GET /data/api/v1/resources/singleton/ignition/security-properties` — Retrieve configuration details about the Security Properties… — `src:[4486786,4501466)`
- **Describe Security Properties Resource Type** — `GET /data/api/v1/resources/type/ignition/security-properties` — Provide information about the Security Properties resource type,… — `src:[4653689,4661604)`

## `config-security-zone`

- **Delete Security Zones (multiple)** — `POST /data/api/v1/resources/delete/ignition/security-zone` — Delete multiple Security Zones resources by name — `src:[1335122,1337760)`
- **Get Security Zones Config** — `GET /data/api/v1/resources/find/ignition/security-zone/{name}` — Retrieve configuration details about a specific Security Zones… — `src:[1954947,1961863)`
- **Modify Security Zones** — `PUT /data/api/v1/resources/ignition/security-zone` — Modify one or more Security Zones resources — `src:[2936334,2945856)`
- **Create Security Zones** — `POST /data/api/v1/resources/ignition/security-zone` — Create a new Security Zones resource — `src:[2945864,2956225)`
- **Delete Security Zones** — `DELETE /data/api/v1/resources/ignition/security-zone/{name}/{signature}` — Delete a Security Zones resource by name — `src:[2956304,2959061)`
- **List Security Zones Resources** — `GET /data/api/v1/resources/list/ignition/security-zone` — List all Security Zones resources, in verbose format, including… — `src:[4021546,4030357)`
- **Get Security Zones Names** — `GET /data/api/v1/resources/names/ignition/security-zone` — List all Security Zones resources, including each resource’s name… — `src:[4290387,4293292)`
- **Rename Security Zones** — `POST /data/api/v1/resources/rename/ignition/security-zone/{name}` — Change the name of a Security Zones resource, and update all… — `src:[4406478,4409463)`
- **Describe Security Zones Resource Type** — `GET /data/api/v1/resources/type/ignition/security-zone` — Provide information about the Security Zones resource type,… — `src:[4661666,4663059)`

## `config-store-forward`

- **Delete Store and Forward Engine (multiple)** — `POST /data/api/v1/resources/delete/ignition/store-and-forward-engine` — Delete multiple Store and Forward Engine resources by name — `src:[1340549,1343207)`
- **Get Store and Forward Engine Config** — `GET /data/api/v1/resources/find/ignition/store-and-forward-engine/{name}` — Retrieve configuration details about a specific Store and Forward… — `src:[1964362,1975202)`
- **Modify Store and Forward Engine** — `PUT /data/api/v1/resources/ignition/store-and-forward-engine` — Modify one or more Store and Forward Engine resources — `src:[2972787,2986233)`
- **Create Store and Forward Engine** — `POST /data/api/v1/resources/ignition/store-and-forward-engine` — Create a new Store and Forward Engine resource — `src:[2986241,3000526)`
- **Delete Store and Forward Engine** — `DELETE /data/api/v1/resources/ignition/store-and-forward-engine/{name}/{signature}` — Delete a Store and Forward Engine resource by name — `src:[3000616,3003393)`
- **List Store and Forward Engine Resources** — `GET /data/api/v1/resources/list/ignition/store-and-forward-engine` — List all Store and Forward Engine resources, in verbose format,… — `src:[4034737,4047472)`
- **Get Store and Forward Engine Names** — `GET /data/api/v1/resources/names/ignition/store-and-forward-engine` — List all Store and Forward Engine resources, including each… — `src:[4296344,4299269)`
- **Rename Store and Forward Engine** — `POST /data/api/v1/resources/rename/ignition/store-and-forward-engine/{name}` — Change the name of a Store and Forward Engine resource, and… — `src:[4412613,4415618)`
- **Describe Store and Forward Engine Resource Type** — `GET /data/api/v1/resources/type/ignition/store-and-forward-engine` — Provide information about the Store and Forward Engine resource… — `src:[4666739,4668582)`

## `config-system-properties`

- **Get Homepage Notes** — `GET /data/api/v1/homepage-notes` — Returns the homepage notes text from Gateway Properties. — `src:[90185,90666)`
- **Modify System Properties** — `PUT /data/api/v1/resources/ignition/system-properties` — Modify one or more System Properties resources — `src:[3003454,3028306)`
- **Create System Properties** — `POST /data/api/v1/resources/ignition/system-properties` — Create the System Properties resource — `src:[3028314,3053990)`
- **Delete System Properties** — `DELETE /data/api/v1/resources/ignition/system-properties/{signature}` — Delete the System Properties resource. — `src:[3054066,3056359)`
- **Get System Properties Config** — `GET /data/api/v1/resources/singleton/ignition/system-properties` — Retrieve configuration details about the System Properties resource — `src:[4501537,4523713)`
- **Describe System Properties Resource Type** — `GET /data/api/v1/resources/type/ignition/system-properties` — Provide information about the System Properties resource type,… — `src:[4668648,4680308)`

## `config-tag-provider`

- **Delete Tag Providers (multiple)** — `POST /data/api/v1/resources/delete/ignition/tag-provider` — Delete multiple Tag Providers resources by name — `src:[1343271,1345906)`
- **Get Tag Providers Config** — `GET /data/api/v1/resources/find/ignition/tag-provider/{name}` — Retrieve configuration details about a specific Tag Providers… — `src:[1975270,1987315)`
- **Modify Tag Providers** — `PUT /data/api/v1/resources/ignition/tag-provider` — Modify one or more Tag Providers resources — `src:[3056415,3070061)`
- **Create Tag Providers** — `POST /data/api/v1/resources/ignition/tag-provider` — Create a new Tag Providers resource — `src:[3070069,3084554)`
- **Delete Tag Providers** — `DELETE /data/api/v1/resources/ignition/tag-provider/{name}/{signature}` — Delete a Tag Providers resource by name — `src:[3084632,3087386)`
- **List Tag Providers Resources** — `GET /data/api/v1/resources/list/ignition/tag-provider` — List all Tag Providers resources, in verbose format, including… — `src:[4047533,4061473)`
- **Get Tag Providers Names** — `GET /data/api/v1/resources/names/ignition/tag-provider` — List all Tag Providers resources, including each resource’s name… — `src:[4299331,4302233)`
- **Rename Tag Providers** — `POST /data/api/v1/resources/rename/ignition/tag-provider/{name}` — Change the name of a Tag Providers resource, and update all… — `src:[4415689,4418671)`
- **Describe Tag Providers Resource Type** — `GET /data/api/v1/resources/type/ignition/tag-provider` — Provide information about the Tag Providers resource type,… — `src:[4680369,4688962)`

## `config-translations`

- **Update Multiple Translations Data Files** — `PUT /data/api/v1/resources/datafile/ignition/translations` — Create or overwrite multiple data files in the singleton resource. — `src:[1230837,1233538)`
- **Get Translations Data File** — `GET /data/api/v1/resources/datafile/ignition/translations/{filename}` — Retrieve a data file from the singleton resource — `src:[1233614,1234285)`
- **Update Translations Data File** — `PUT /data/api/v1/resources/datafile/ignition/translations/{filename}` — Update (overwrite) a data file in the singleton resource — `src:[1234292,1237323)`
- **Delete Translations Data File** — `DELETE /data/api/v1/resources/datafile/ignition/translations/{filename}` — Deletes a data file in the singleton resource — `src:[1237333,1240060)`
- **Modify Translations** — `PUT /data/api/v1/resources/ignition/translations` — Modify one or more Translations resources — `src:[3087442,3093812)`
- **Create Translations** — `POST /data/api/v1/resources/ignition/translations` — Create the Translations resource — `src:[3093820,3101014)`
- **Delete Translations** — `DELETE /data/api/v1/resources/ignition/translations/{signature}` — Delete the Translations resource. — `src:[3101085,3103363)`
- **Get Translations Config** — `GET /data/api/v1/resources/singleton/ignition/translations` — Retrieve configuration details about the Translations resource — `src:[4523779,4527728)`
- **Describe Translations Resource Type** — `GET /data/api/v1/resources/type/ignition/translations` — Provide information about the Translations resource type,… — `src:[4689023,4691497)`

## `config-user-source`

- **Delete User Sources (multiple)** — `POST /data/api/v1/resources/delete/ignition/user-source` — Delete multiple User Sources resources by name — `src:[1345969,1348601)`
- **Get User Sources Config** — `GET /data/api/v1/resources/find/ignition/user-source/{name}` — Retrieve configuration details about a specific User Sources… — `src:[1987382,2105538)`
- **Modify User Sources** — `PUT /data/api/v1/resources/ignition/user-source` — Modify one or more User Sources resources — `src:[3103418,3224435)`
- **Create User Sources** — `POST /data/api/v1/resources/ignition/user-source` — Create a new User Sources resource — `src:[3224443,3346299)`
- **Delete User Sources** — `DELETE /data/api/v1/resources/ignition/user-source/{name}/{signature}` — Delete a User Sources resource by name — `src:[3346376,3349127)`
- **List User Sources Resources** — `GET /data/api/v1/resources/list/ignition/user-source` — List all User Sources resources, in verbose format, including… — `src:[4061533,4181584)`
- **Get User Sources Names** — `GET /data/api/v1/resources/names/ignition/user-source` — List all User Sources resources, including each resource’s name… — `src:[4302294,4305193)`
- **Rename User Sources** — `POST /data/api/v1/resources/rename/ignition/user-source/{name}` — Change the name of a User Sources resource, and update all… — `src:[4418741,4421720)`
- **Describe User Sources Resource Type** — `GET /data/api/v1/resources/type/ignition/user-source` — Provide information about the User Sources resource type,… — `src:[4691557,4753397)`

## `data-syncs`

- **Get Syncable Items** — `GET /data/api/v1/sync/items` — Get all syncable items from on the gateway. — `src:[4894534,4897336)`
- **Data Syncs Reset** — `POST /data/api/v1/sync/reset` — Reset the Data Syncs of the provided keys. — `src:[4897372,4898090)`

## `designer-sessions`

- **Designer Session Details** — `GET /data/api/v1/designer/{id}` — Get details about a specific designer session. — `src:[32647,33869)`
- **Prune Designer Session** — `DELETE /data/api/v1/designer/{id}` — Prune a dormant designer session. — `src:[33879,34543)`
- **Designer Sessions** — `GET /data/api/v1/designers` — Get a list of active designer sessions. — `src:[34577,38095)`

## `encryption`

- **Encrypt arbitrary bytes** — `POST /data/api/v1/encryption/encrypt` — Encrypt an arbitrary payload into a flat JSON Web Encryption… — `src:[44276,46222)`

## `entity`

- **Browse Entities** — `GET /data/api/v1/entity/browse` — This endpoint returns a list of matching registered entities,… — `src:[46260,50757)`
- **Set Entity Enabled** — `POST /data/api/v1/entity/enabled` — Allows for altering the enabled state of an entity — `src:[50797,55103)`
- **Entities by Section** — `GET /data/api/v1/entity/section/{section}` — Returns Entities relevant to a given UI section — `src:[55152,59839)`

## `executors`

- **Private** — `GET /data/api/v1/executors/private` — Details on the executors running on private execution pools on… — `src:[59881,63218)`
- **Shared** — `GET /data/api/v1/executors/shared` — Details on the executors running on the shared execution pool. — `src:[63259,66408)`

## `gateway-backups`

- **Get Gateway Backup** — `GET /data/api/v1/backup` — Generates a \*.gwbk file that can be downloaded. — `src:[29263,30126)`
- **Restore Gateway Backup** — `POST /data/api/v1/backup` — Processes and restores a \*.gwbk file on this gateway. — `src:[30134,32609)`

## `gateway-info`

- **Gateway Info** — `GET /data/api/v1/gateway-info` — Information about this gateway. — `src:[66445,68001)`

## `gateway-network`

- **Reset Incoming Connection** — `PUT /data/api/v1/gateway-network-incoming/reset/{resourceName}` — Resets the incoming gateway network connection from a specified… — `src:[68071,68648)`
- **Toggle Approval** — `PUT /data/api/v1/gateway-network-incoming/toggleApproval/{remoteId}/{approve}` — Toggles the approval status of an incoming connection. — `src:[68733,69592)`
- **Reset Outgoing Connection** — `PUT /data/api/v1/gateway-network-outgoing/reset/{resourceName}` — Resets the outgoing gateway network connection to a specified… — `src:[69662,70237)`
- **Cancel Task Queue** — `DELETE /data/api/v1/gateway-network/cancelQueueTasks/{serverId}/{queueId}` — Cancels any tasks pending in the queue. — `src:[70318,71073)`
- **Connection Details** — `GET /data/api/v1/gateway-network/connectionDetails/{internalId}/{remoteGateway}` — Detailed information about the connection between this Ignition… — `src:[71160,73915)`
- **Connections** — `GET /data/api/v1/gateway-network/connections` — Information about all currently active connections between this… — `src:[73967,76972)`
- **Gateway Network Crawler** — `GET /data/api/v1/gateway-network/crawler` — Runs the Gateway Network crawler to gather information on… — `src:[77020,78247)`
- **Get Remote Server Names** — `GET /data/api/v1/gateway-network/diagnostics/getRemoteServerIds` — Gets the names of all remote servers. — `src:[78318,78564)`
- **Diagnostic Ping** — `POST /data/api/v1/gateway-network/diagnostics/testConnectionToServer/{serverId}` — Pings a remote server to test connectivity. — `src:[78651,79145)`
- **Gateway Detail** — `GET /data/api/v1/gateway-network/gatewayDetail/{serverId}` — Gateway network details about a specific Ignition server. — `src:[79210,81015)`
- **Gateways** — `GET /data/api/v1/gateway-network/gateways` — Information about all gateways on the Gateway Network that this… — `src:[81064,84539)`
- **Gateway Network Live Diagram** — `GET /data/api/v1/gateway-network/livediagram` — Information describing the relationships between all Ignition… — `src:[84591,85561)`
- **Pause Task Queue** — `PUT /data/api/v1/gateway-network/pauseQueue/{serverId}/{queueId}` — Pauses any pending tasks on the given queue, which will cause it… — `src:[85633,86317)`
- **Remote Tag Providers** — `GET /data/api/v1/gateway-network/remote-tag-providers/{serverId}` — A list of remote tag providers names for the specified remote… — `src:[86389,89450)`
- **Resume Task Queue** — `PUT /data/api/v1/gateway-network/resumeQueue/{serverId}/{queueId}` — Resumes a paused task queue. — `src:[89523,90146)`

## `gateway-scripts`

- **Gateway Script Diagnostics** — `GET /data/api/v1/scripts/diagnostics/{type}` — Diagnostic information for all scripts in enabled projects on the… — `src:[4889969,4893280)`

## `launcher`

- **Download Launcher** — `GET /data/api/v1/launcher/download/{type}/{os}/{arch}` — Downloads a launcher for the given type, OS, and architecture — `src:[90727,91773)`
- **Retrieve Launcher information** — `GET /data/api/v1/launcher/info/{type}/{os}/{arch}` — Returns launcher information for the given type, OS, and… — `src:[91830,93499)`
- **List all launchers** — `GET /data/api/v1/launcher/list` — Returns a list of all launcher types — `src:[93537,96711)`

## `license-activation`

- **Activate License** — `PUT /data/api/v1/activation/activate/{key}` — Activate an Ignition license using the given license key. — `src:[9140,11400)`
- **Check Gateway Online Status** — `GET /data/api/v1/activation/is-online` — Returns a boolean representing the online state of the gateway. — `src:[11445,11813)`
- **Submit Offline Activation** — `POST /data/api/v1/activation/offline/activate` — Submit an offline activation response, which can be retrieved by… — `src:[11866,12777)`
- **Generate Offline Activation Request** — `GET /data/api/v1/activation/offline/activate-request/{key}` — Generate an offline activation request for the given license key. — `src:[12843,13429)`
- **Unactivate License (Offline)** — `POST /data/api/v1/activation/offline/unactivate/{key}` — Unactivate an Ignition license in offline mode. — `src:[13490,14501)`
- **Re-Activate License** — `PUT /data/api/v1/activation/reactivate/{key}` — Re-activate an Ignition license using the given license key. — `src:[14553,16824)`
- **Unactivate License** — `POST /data/api/v1/activation/unactivate/{key}` — Unactivate an Ignition license. — `src:[16877,18174)`
- **Leased Activation** — `POST /data/api/v1/leased-activation/activate` — Submit a leased activation request — `src:[96763,97732)`
- **Test Leased License Service** — `GET /data/api/v1/leased-activation/test-leased-license-server` — Test the availability of the Inductive Automation Leased License… — `src:[97801,98828)`
- **Leased Unactivation** — `POST /data/api/v1/leased-activation/unactivate/{key}` — Cancel a leased activation. — `src:[98888,99713)`

## `license-status`

- **Licensing Information** — `GET /data/api/v1/licenses` — Details about all existing licenses on this Ignition Gateway. — `src:[99746,102712)`
- **Trial Information** — `GET /data/api/v1/trial` — Information on this Ignition Gateway's Trial Period. — `src:[4904129,4905138)`

## `logging`

- **Logs** — `GET /data/api/v1/logs` — Returns logs from the Ignition Gateway. — `src:[103935,108414)`
- **Download Logs** — `GET /data/api/v1/logs/download` — Download a copy of the system log from the internal database. — `src:[108452,108645)`
- **Reset Logger Levels** — `POST /data/api/v1/logs/levelreset` — Resets all of the logger levels to their default level. — `src:[108686,108955)`
- **Loggers** — `GET /data/api/v1/logs/loggers` — Returns a list of all the loggers, their current levels, and the… — `src:[108992,111845)`
- **Set Logger Level** — `POST /data/api/v1/logs/loggers/{loggerName}` — Set level for a specific logger. — `src:[111896,112519)`
- **Monitoring Session Logs** — `POST /data/api/v1/logs/monitoring-session` — Creates a logging session that allows setting of levels and… — `src:[112568,116554)`
- **Modify Logger Context Properties (Bulk)** — `POST /data/api/v1/logs/properties` — Bulk operation for setting or removing LoggingFilter properties. — `src:[116595,117339)`
- **Applied Logger Context Properties** — `GET /data/api/v1/logs/properties/applied` — A JSON Object containing the all currently applied logging… — `src:[117387,120323)`
- **Registered Logger Context Properties** — `GET /data/api/v1/logs/properties/registered` — A JSON Object containing the all currently registered logging… — `src:[120374,123209)`
- **Add Logger Context Property** — `POST /data/api/v1/logs/properties/{key}` — Adds the specified property to the current logging context. — `src:[123256,124154)`
- **Remove Logger Context Property** — `DELETE /data/api/v1/logs/properties/{key}` — Removes the specified property from the current logging context. — `src:[124164,125146)`

## `managed-tag-provider`

- **Managed Tag Provider** — `PUT /data/api/v1/managed-tag-provider` — This endpoint modifies the Tag Reference Store property of a… — `src:[125191,126046)`

## `modules`

- **View Certificate Information** — `GET /data/api/v1/modules/certificate` — Get details about the certificate for a given module. — `src:[132909,134038)`
- **Accept Certificate** — `POST /data/api/v1/modules/certificate` — Accept a module's certificate. — `src:[134046,134837)`
- **View EULA** — `GET /data/api/v1/modules/eula` — Retrieves the given module's EULA as HTML. — `src:[134874,135471)`
- **Accept EULA** — `POST /data/api/v1/modules/eula` — Accept a module's EULA — `src:[135479,136240)`
- **All Healthy Modules** — `GET /data/api/v1/modules/healthy` — A list of all "healthy" (non-quarantined) modules on this… — `src:[136280,141247)`
- **Install Module** — `POST /data/api/v1/modules/install` — Completes installation of a previously uploaded module — `src:[141288,142293)`
- **Quarantined Modules** — `GET /data/api/v1/modules/quarantined` — Information about all quarantined modules on this Ignition Gateway. — `src:[142337,145659)`
- **Toggle Module State** — `PUT /data/api/v1/modules/toggle-state` — Enable and disable Ignition Modules. — `src:[145704,147091)`
- **Uninstall Module[s]** — `DELETE /data/api/v1/modules/uninstall` — Configures module[s] to be uninstalled on the next Gateway restart. — `src:[147136,148015)`
- **Upload Module** — `POST /data/api/v1/modules/upload` — Upload an Ignition Module to prepare for installation. — `src:[148055,149368)`

## `opc-connection`

- **Delete OPC Connections (multiple)** — `POST /data/api/v1/resources/delete/ignition/opc-connection` — Delete multiple OPC Connections resources by name — `src:[1324354,1326988)`
- **Get OPC Connections Config** — `GET /data/api/v1/resources/find/ignition/opc-connection/{name}` — Retrieve configuration details about a specific OPC Connections… — `src:[1905133,1935229)`
- **Modify OPC Connections** — `PUT /data/api/v1/resources/ignition/opc-connection` — Modify one or more OPC Connections resources — `src:[2740482,2771899)`
- **Create OPC Connections** — `POST /data/api/v1/resources/ignition/opc-connection` — Create a new OPC Connections resource — `src:[2771907,2804163)`
- **Delete OPC Connections** — `DELETE /data/api/v1/resources/ignition/opc-connection/{name}/{signature}` — Delete a OPC Connections resource by name — `src:[2804243,2806996)`
- **List OPC Connections Resources** — `GET /data/api/v1/resources/list/ignition/opc-connection` — List all OPC Connections resources, in verbose format, including… — `src:[3964180,3996171)`
- **Get OPC Connections Names** — `GET /data/api/v1/resources/names/ignition/opc-connection` — List all OPC Connections resources, including each resource’s… — `src:[4278559,4281460)`
- **Rename OPC Connections** — `POST /data/api/v1/resources/rename/ignition/opc-connection/{name}` — Change the name of a OPC Connections resource, and update all… — `src:[4394294,4397275)`
- **Describe OPC Connections Resource Type** — `GET /data/api/v1/resources/type/ignition/opc-connection` — Provide information about the OPC Connections resource type,… — `src:[4637242,4640773)`

## `overview`

- **Get All Available Locales** — `GET /data/api/v1/locales` — Returns codes and display names for all languages available for… — `src:[102744,103906)`
- **Gateway Overview** — `GET /data/api/v1/overview` — General status information about this Ignition Gateway. — `src:[149401,153383)`
- **Connections Overview** — `GET /data/api/v1/overview/connections` — A list of various connections (DB, OPC, etc.) on this Ignition… — `src:[153428,156619)`
- **Gateway Network** — `GET /data/api/v1/overview/gan` — Information about the Gateway Network. — `src:[156656,157314)`
- **Gateway Name** — `GET /data/api/v1/overview/name` — This Ignition Gateway's name. — `src:[157352,157777)`
- **Problems Overview** — `GET /data/api/v1/overview/problems` — A list of objects representing problems on the gateway that are… — `src:[157819,160790)`

## `projects`

- **Create Project** — `POST /data/api/v1/projects` — Create a new project on this Ignition Gateway. — `src:[160824,166745)`
- **Copy Project** — `POST /data/api/v1/projects/copy` — Create a copy of a project on this Ignition Gateway. — `src:[166784,171681)`
- **Export Project** — `GET /data/api/v1/projects/export/{name}` — Exports the given project as a zip archive. — `src:[171728,172271)`
- **Project Details** — `GET /data/api/v1/projects/find/{name}` — Get information about a specific Ignition Project. — `src:[172316,173971)`
- **Import Project** — `POST /data/api/v1/projects/import/{name}` — Import a project into this Ignition Gateway. — `src:[174019,179108)`
- **List All Projects** — `GET /data/api/v1/projects/list` — List all runnable project on this Ignition Gateway. — `src:[179146,182830)`
- **List All Project Names** — `GET /data/api/v1/projects/names` — List names of all projects on this Ignition Gateway. — `src:[182869,185572)`
- **List All Valid Parent Projects** — `GET /data/api/v1/projects/parents` — List names of all valid parent projects on this Ignition Gateway. — `src:[185613,188337)`
- **List Valid Parent Project Names For Project** — `GET /data/api/v1/projects/parents/{name}` — List names of valid parent projects for a given project on this… — `src:[188385,191362)`
- **Rename Project** — `POST /data/api/v1/projects/rename/{name}` — Rename a project on this Ignition Gateway. — `src:[191410,195646)`
- **Modify Project** — `PUT /data/api/v1/projects/{name}` — Modify an Ignition Project. — `src:[195686,201944)`
- **Delete an Ignition Project** — `DELETE /data/api/v1/projects/{name}` — Delete a project from this Ignition Gateway. — `src:[201954,206037)`

## `quickstart`

- **Modify Quick Start Configuration** — `PUT /data/api/v1/resources/ignition/quickstart` — Modify one or more Quick Start Configuration resources — `src:[2807050,2812375)`
- **Create Quick Start Configuration** — `POST /data/api/v1/resources/ignition/quickstart` — Create the Quick Start Configuration resource — `src:[2812383,2818532)`
- **Delete Quick Start Configuration** — `DELETE /data/api/v1/resources/ignition/quickstart/{signature}` — Delete the Quick Start Configuration resource. — `src:[2818601,2820896)`
- **Get Quick Start Configuration Config** — `GET /data/api/v1/resources/singleton/ignition/quickstart` — Retrieve configuration details about the Quick Start… — `src:[4481626,4484530)`
- **Describe Quick Start Configuration Resource Type** — `GET /data/api/v1/resources/type/ignition/quickstart` — Provide information about the Quick Start Configuration resource… — `src:[4640832,4642792)`

## `redundancy`

- **Status** — `GET /data/api/v1/redundancy` — Information on the current status of the redundancy system. — `src:[206072,207082)`
- **Get Redundancy Config** — `GET /data/api/v1/redundancy/config` — The configuration of the redundancy system. — `src:[207124,211836)`
- **Update Redundancy Config** — `PUT /data/api/v1/redundancy/config` — Updates the configuration of the redundancy system. — `src:[211843,216673)`
- **Log Events** — `GET /data/api/v1/redundancy/events` — Returns log events from the redundancy system. — `src:[216715,219851)`
- **Force Failover** — `POST /data/api/v1/redundancy/gwaction/failover` — Forces redundant configuration to failover to the backup node. — `src:[219905,220256)`
- **Re-Sync Configuration** — `POST /data/api/v1/redundancy/gwaction/resync` — Makes a request to force the re-synchronization of redundant nodes. — `src:[220308,220667)`
- **Redundancy Provider Metrics** — `GET /data/api/v1/redundancy/providers` — Information on all the Redundancy Providers this Ignition Gateway… — `src:[220712,224103)`

## `restart-tasks`

- **Get all required restart tasks** — `GET /data/api/v1/restart-tasks/pending` — Get a list of all tasks that are currently pending a gateway… — `src:[4753443,4753986)`
- **Restart the Gateway** — `POST /data/api/v1/restart-tasks/restart` — Restarts the gateway. — `src:[4754033,4754645)`

## `running-scripts`

- **Running Scripts** — `GET /data/api/v1/scripts` — All of the currently executing scripts on the gateway. — `src:[4886528,4889428)`
- **Cancel Script** — `DELETE /data/api/v1/scripts/cancel-script/{id}` — Cancels the execution of the specified script. — `src:[4889482,4889918)`

## `secret-providers`

- **List Provider Secrets** — `GET /data/api/v1/secret-providers/{provider-name}/secrets` — Discover the list of secrets managed by a provider. — `src:[4893345,4894499)`

## `service-connectors`

- **Update Multiple Service Connectors Data Files** — `PUT /data/api/v1/resources/datafile/ignition/service-connector/{name}` — Create or overwrite multiple data files for a named Service… — `src:[1219995,1223077)`
- **Get Service Connectors Data File** — `GET /data/api/v1/resources/datafile/ignition/service-connector/{name}/{filename}` — Retrieve a data file from a specific Service Connectors resource — `src:[1223165,1224235)`
- **Update Service Connectors Data File** — `PUT /data/api/v1/resources/datafile/ignition/service-connector/{name}/{filename}` — Update (overwrite) a data file for a named Service Connectors… — `src:[1224242,1227654)`
- **Delete Service Connectors Data File** — `DELETE /data/api/v1/resources/datafile/ignition/service-connector/{name}/{filename}` — Delete a data file for a named Service Connectors resource — `src:[1227664,1230772)`
- **Delete Service Connectors (multiple)** — `POST /data/api/v1/resources/delete/ignition/service-connector` — Delete multiple Service Connectors resources by name — `src:[1337829,1340473)`
- **Get Service Connectors Config** — `GET /data/api/v1/resources/find/ignition/service-connector/{name}` — Retrieve configuration details about a specific Service… — `src:[1961936,1964282)`
- **Modify Service Connectors** — `PUT /data/api/v1/resources/ignition/service-connector` — Modify one or more Service Connectors resources — `src:[2959122,2964074)`
- **Create Service Connectors** — `POST /data/api/v1/resources/ignition/service-connector` — Create a new Service Connectors resource — `src:[2964082,2969873)`
- **Delete Service Connectors** — `DELETE /data/api/v1/resources/ignition/service-connector/{name}/{signature}` — Delete a Service Connectors resource by name — `src:[2969956,2972719)`
- **List Service Connectors Resources** — `GET /data/api/v1/resources/list/ignition/service-connector` — List all Service Connectors resources, in verbose format,… — `src:[4030423,4034664)`
- **Get Service Connectors Names** — `GET /data/api/v1/resources/names/ignition/service-connector` — List all Service Connectors resources, including each resource’s… — `src:[4293359,4296270)`
- **Rename Service Connectors** — `POST /data/api/v1/resources/rename/ignition/service-connector/{name}` — Change the name of a Service Connectors resource, and update all… — `src:[4409539,4412530)`
- **Describe Service Connectors Resource Type** — `GET /data/api/v1/resources/type/ignition/service-connector` — Provide information about the Service Connectors resource type,… — `src:[4663125,4666666)`

## `system-performance`

- **Historic Performance Data** — `GET /data/api/v1/systemPerformance/charts` — Returns a list of historical gateway CPU and memory usage. — `src:[4898139,4898971)`
- **Current Performance Data** — `GET /data/api/v1/systemPerformance/currentGauges` — Returns an object containing the current gateway CPU and memory… — `src:[4899027,4899467)`
- **System Clock Drift Events** — `GET /data/api/v1/systemPerformance/driftEvents` — Returns a list of historical system clock drift events. — `src:[4899521,4900025)`
- **Current System Clock Drift** — `GET /data/api/v1/systemPerformance/driftGauge` — Returns the current drift of the system clock. — `src:[4900078,4900438)`
- **Thread Execution Data** — `GET /data/api/v1/systemPerformance/threads` — Return the current number of various types of threads running on… — `src:[4900488,4900964)`

## `tags`

- **Download Tag Export** — `GET /data/api/v1/tags/export` — A tag export containing tags under the specified path. — `src:[4901000,4902520)`
- **Import Tags** — `POST /data/api/v1/tags/import` — Imports tags into the specified path. — `src:[4902557,4904099)`

## `thread-diagnostics`

- **Download Diagnostics Bundle** — `GET /data/api/v1/diagnostics/bundle/download` — Download the most recently generated diagnostics bundle. — `src:[38147,38360)`
- **Diagnostics Generate Bundle** — `POST /data/api/v1/diagnostics/bundle/generate` — Generate a new diagnostics bundle. — `src:[38413,38745)`
- **Diagnostics Bundle Status** — `GET /data/api/v1/diagnostics/bundle/status` — The current status of the diagnostics bundle. — `src:[38795,39163)`
- **Deadlocked Threads** — `GET /data/api/v1/diagnostics/threads/deadlocks` — A list of thread Ids for threads that are deadlocked. — `src:[39217,39626)`
- **Thread Dump (Formatted)** — `GET /data/api/v1/diagnostics/threads/dump/formatted` — Thread dump in legacy format for backward compatibility. — `src:[39685,40585)`
- **Thread Dump** — `GET /data/api/v1/diagnostics/threads/threaddump` — An object containing information about the various threads… — `src:[40640,44232)`

## `user-management-scim`

- **List Group Resources** — `GET /data/api/v1/scim/{profile-name}/{scim-version}/Groups` — This endpoint returns a list of group resources. — `src:[4761693,4769463)`
- **Create Group Resource** — `POST /data/api/v1/scim/{profile-name}/{scim-version}/Groups` — This endpoint creates a new group resource. — `src:[4769471,4778584)`
- **Get Group Resource** — `GET /data/api/v1/scim/{profile-name}/{scim-version}/Groups/{group-id}` — This endpoint returns a group resource. — `src:[4778661,4786398)`
- **Modify Group Resource** — `PUT /data/api/v1/scim/{profile-name}/{scim-version}/Groups/{group-id}` — This endpoint modifies a group resource. — `src:[4786405,4795721)`
- **Delete Group Resource** — `DELETE /data/api/v1/scim/{profile-name}/{scim-version}/Groups/{group-id}` — This path deletes a group resource. — `src:[4795731,4801388)`
- **List Resource Types** — `GET /data/api/v1/scim/{profile-name}/{scim-version}/ResourceTypes` — This endpoint is used to discover the types of resources… — `src:[4801461,4807366)`
- **Get Resource Type** — `GET /data/api/v1/scim/{profile-name}/{scim-version}/ResourceTypes/{type-id}` — This endpoint is used to retrieve information about the specified… — `src:[4807449,4814162)`
- **List Schemas** — `GET /data/api/v1/scim/{profile-name}/{scim-version}/Schemas` — This endpoint is used to retrieve information about resource… — `src:[4814229,4821445)`
- **Get Schema** — `GET /data/api/v1/scim/{profile-name}/{scim-version}/Schemas/{schema-id}` — This endpoint is used to retrieve information about the specified… — `src:[4821524,4828167)`
- **Get Service Provider Configuration** — `GET /data/api/v1/scim/{profile-name}/{scim-version}/ServiceProviderConfig` — The service provider configuration resource enables a service… — `src:[4828248,4838000)`
- **List User Resources** — `GET /data/api/v1/scim/{profile-name}/{scim-version}/Users` — This path returns a list of user resources. — `src:[4838065,4848971)`
- **Create User Resource** — `POST /data/api/v1/scim/{profile-name}/{scim-version}/Users` — This endpoint creates a new user resource. — `src:[4848979,4859708)`
- **Get User Resource** — `GET /data/api/v1/scim/{profile-name}/{scim-version}/Users/{user-id}` — This endpoint returns a user resource. — `src:[4859783,4869896)`
- **Modify User Resource** — `PUT /data/api/v1/scim/{profile-name}/{scim-version}/Users/{user-id}` — This endpoint modifies a user resource. — `src:[4869903,4880832)`
- **Delete User Resource** — `DELETE /data/api/v1/scim/{profile-name}/{scim-version}/Users/{user-id}` — This endpoint deletes a user resource. — `src:[4880842,4886496)`
