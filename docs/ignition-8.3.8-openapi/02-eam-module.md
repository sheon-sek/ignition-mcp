# EAM Module

> Source SHA-256: `7ab205eff392dc38a547980fe94803c97f7c075102e7b983efc63f76b827b744`
> Endpoints: `114`

Read the matching endpoint line, then fetch only its `src:[start,end)` byte range from `./ignition-8.3-openapi.min.json`.

## `agent-group`

- **Modify Agent Groups** — `PUT /data/api/v1/resources/com.inductiveautomation.eam/agent-group` — Modify one or more Agent Groups resources — `src:[345337,349668)`
- **Create Agent Groups** — `POST /data/api/v1/resources/com.inductiveautomation.eam/agent-group` — Create a new Agent Groups resource — `src:[349676,354846)`
- **Delete Agent Groups** — `DELETE /data/api/v1/resources/com.inductiveautomation.eam/agent-group/{name}/{signature}` — Delete a Agent Groups resource by name — `src:[354942,357686)`
- **Delete Agent Groups (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.eam/agent-group` — Delete multiple Agent Groups resources by name — `src:[1242924,1245549)`
- **Get Agent Groups Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.eam/agent-group/{name}` — Retrieve configuration details about a specific Agent Groups… — `src:[1405413,1407138)`
- **List Agent Groups Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.eam/agent-group` — List all Agent Groups resources, in verbose format, including… — `src:[3407820,3411440)`
- **Get Agent Groups Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.eam/agent-group` — List all Agent Groups resources, including each resource’s name… — `src:[4189179,4192071)`
- **Rename Agent Groups** — `POST /data/api/v1/resources/rename/com.inductiveautomation.eam/agent-group/{name}` — Change the name of a Agent Groups resource, and update all… — `src:[4308418,4311390)`
- **Describe Agent Groups Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.eam/agent-group` — Provide information about the Agent Groups resource type,… — `src:[4531483,4532863)`

## `agent-management`

- **Modify Agent Management** — `PUT /data/api/v1/resources/com.inductiveautomation.eam/agent-management` — Modify one or more Agent Management resources — `src:[357765,362898)`
- **Create Agent Management** — `POST /data/api/v1/resources/com.inductiveautomation.eam/agent-management` — Create a new Agent Management resource — `src:[362906,368878)`
- **Delete Agent Management** — `DELETE /data/api/v1/resources/com.inductiveautomation.eam/agent-management/{name}/{signature}` — Delete a Agent Management resource by name — `src:[368979,371736)`
- **Delete Agent Management (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.eam/agent-management` — Delete multiple Agent Management resources by name — `src:[1245636,1248274)`
- **Get Agent Management Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.eam/agent-management/{name}` — Retrieve configuration details about a specific Agent Management… — `src:[1407229,1409501)`
- **List Agent Management Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.eam/agent-management` — List all Agent Management resources, in verbose format, including… — `src:[3411524,3415691)`
- **Get Agent Management Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.eam/agent-management` — List all Agent Management resources, including each resource’s… — `src:[4192156,4195061)`
- **Rename Agent Management** — `POST /data/api/v1/resources/rename/com.inductiveautomation.eam/agent-management/{name}` — Change the name of a Agent Management resource, and update all… — `src:[4311484,4314469)`
- **Describe Agent Management Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.eam/agent-management` — Provide information about the Agent Management resource type,… — `src:[4532947,4534340)`
- **Agent Approval Status** — `GET /data/eam/api/v1/agent-approval-status/{controllerId}/{localServerId}` — Check the approval status of a given agent. — `src:[4909255,4910238)`
- **EAM Agent Details Overview** — `GET /data/eam/api/v1/agent-details/{serverid}` — Gets the details of a specific EAM agent. — `src:[4910291,4911460)`
- **EAM Agent License Keys** — `GET /data/eam/api/v1/agent-licenses/{serverid}` — Gets the license keys of a specific EAM agent. — `src:[4911514,4912061)`
- **Retrieve agent modules** — `GET /data/eam/api/v1/agent-management/modules` — Retrieve module information from an agent. — `src:[4912114,4913230)`
- **Retrieve project resources** — `GET /data/eam/api/v1/agent-management/project-resources` — Retrieve project resource names from an agent or from the local… — `src:[4913293,4914887)`
- **Retrieve projects names** — `GET /data/eam/api/v1/agent-management/projects` — Retrieve project names from an agent or from the local controller. — `src:[4914941,4916285)`
- **Retrieve agent info for upgrade** — `GET /data/eam/api/v1/agent-management/upgrade-info` — Returns system data about an agent, used to prep a remote upgrade — `src:[4916343,4917474)`
- **EAM Agents Status** — `GET /data/eam/api/v1/agents` — Displays a list of EAM agents and their status. — `src:[4917509,4920889)`
- **EAM Agents by Agent Group** — `GET /data/eam/api/v1/agents-by-group` — Gets the connected EAM agents, organized by group. — `src:[4920933,4923993)`
- **Approve agent** — `POST /data/eam/api/v1/approve-agent/{serverid}` — Approves an EAM agent, which will allow interaction with the… — `src:[4924047,4924715)`
- **Quarantined agents** — `GET /data/eam/api/v1/quarantined-agents` — Returns a list of encoded ServerIds of EAM agents waiting for… — `src:[4945469,4945942)`
- **Delete quarantined agent** — `DELETE /data/eam/api/v1/quarantined-agents/{serverid}` — Deletes a pending EAM agent from the agent quarantine. — `src:[4946003,4946642)`
- **Upgrade Agent** — `POST /data/eam/api/v1/upgrade-agent/{groupName}/{serverid}` — Upgrade the specified EAM agent — `src:[4948313,4949226)`

## `eam-tasks`

- **Modify EAM Agent Task** — `PUT /data/api/v1/resources/com.inductiveautomation.eam/eam-tasks` — Modify one or more EAM Agent Task resources — `src:[371808,401012)`
- **Create EAM Agent Task** — `POST /data/api/v1/resources/com.inductiveautomation.eam/eam-tasks` — Create a new EAM Agent Task resource — `src:[401020,431063)`
- **Delete EAM Agent Task** — `DELETE /data/api/v1/resources/com.inductiveautomation.eam/eam-tasks/{name}/{signature}` — Delete a EAM Agent Task resource by name — `src:[431157,433903)`
- **Delete EAM Agent Task (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.eam/eam-tasks` — Delete multiple EAM Agent Task resources by name — `src:[1248354,1250981)`
- **Get EAM Agent Task Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.eam/eam-tasks/{name}` — Retrieve configuration details about a specific EAM Agent Task… — `src:[1409585,1436764)`
- **List EAM Agent Task Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.eam/eam-tasks` — List all EAM Agent Task resources, in verbose format, including… — `src:[3415768,3444842)`
- **Get EAM Agent Task Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.eam/eam-tasks` — List all EAM Agent Task resources, including each resource’s name… — `src:[4195139,4198033)`
- **Rename EAM Agent Task** — `POST /data/api/v1/resources/rename/com.inductiveautomation.eam/eam-tasks/{name}` — Change the name of a EAM Agent Task resource, and update all… — `src:[4314556,4317530)`
- **Describe EAM Agent Task Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.eam/eam-tasks` — Provide information about the EAM Agent Task resource type,… — `src:[4534417,4537941)`
- **Cancel task** — `POST /data/eam/api/v1/eam-tasks/cancel/{name}` — Cancels upcoming execution of a gateway task. — `src:[4924768,4925317)`
- **Clear retry task data** — `DELETE /data/eam/api/v1/eam-tasks/clear-retry/{name}` — Removes retry data for a task, allowing the task to be forced to… — `src:[4925377,4925982)`
- **Force task execution** — `POST /data/eam/api/v1/eam-tasks/force/{owner}/{name}` — Force a task to execute immediately. — `src:[4926838,4927797)`
- **Task History** — `GET /data/eam/api/v1/eam-tasks/history` — Returns a list of task execution results — `src:[4927843,4931043)`
- **Resume task** — `POST /data/eam/api/v1/eam-tasks/resume/{name}` — Resumes a suspended gateway task, allowing it to execute at the… — `src:[4934742,4935290)`
- **Get retry tasks** — `GET /data/eam/api/v1/eam-tasks/retry` — Returns a list of tasks that have errored and can be retried — `src:[4935334,4936284)`
- **Running or Scheduled Tasks** — `GET /data/eam/api/v1/eam-tasks/scheduled/{running}` — Returns a list of running or scheduled tasks — `src:[4936342,4939992)`
- **Suspend task** — `POST /data/eam/api/v1/eam-tasks/suspend/{name}` — Suspends a scheduled gateway task, preventing it from executing… — `src:[4944864,4945422)`

## `event-thresholds`

- **Modify Event Thresholds** — `PUT /data/api/v1/resources/com.inductiveautomation.eam/event-thresholds` — Modify one or more Event Thresholds resources — `src:[433982,447033)`
- **Create Event Thresholds** — `POST /data/api/v1/resources/com.inductiveautomation.eam/event-thresholds` — Create the Event Thresholds resource — `src:[447041,460916)`
- **Delete Event Thresholds** — `DELETE /data/api/v1/resources/com.inductiveautomation.eam/event-thresholds/{signature}` — Delete the Event Thresholds resource. — `src:[461010,463293)`
- **Get Event Thresholds Config** — `GET /data/api/v1/resources/singleton/com.inductiveautomation.eam/event-thresholds` — Retrieve configuration details about the Event Thresholds resource — `src:[4421809,4432439)`
- **Describe Event Thresholds Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.eam/event-thresholds` — Provide information about the Event Thresholds resource type,… — `src:[4538025,4543907)`

## `license-management`

- **Modify Hardware License** — `PUT /data/api/v1/resources/com.inductiveautomation.eam/hw-license-management` — Modify one or more Hardware License resources — `src:[463377,468632)`
- **Create Hardware License** — `POST /data/api/v1/resources/com.inductiveautomation.eam/hw-license-management` — Create a new Hardware License resource — `src:[468640,474734)`
- **Delete Hardware License** — `DELETE /data/api/v1/resources/com.inductiveautomation.eam/hw-license-management/{name}/{signature}` — Delete a Hardware License resource by name — `src:[474840,477599)`
- **Modify Leased License** — `PUT /data/api/v1/resources/com.inductiveautomation.eam/leased-license-management` — Modify one or more Leased License resources — `src:[477687,482934)`
- **Create Leased License** — `POST /data/api/v1/resources/com.inductiveautomation.eam/leased-license-management` — Create a new Leased License resource — `src:[482942,489028)`
- **Delete Leased License** — `DELETE /data/api/v1/resources/com.inductiveautomation.eam/leased-license-management/{name}/{signature}` — Delete a Leased License resource by name — `src:[489138,491893)`
- **Delete Hardware License (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.eam/hw-license-management` — Delete multiple Hardware License resources by name — `src:[1251073,1253713)`
- **Delete Leased License (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.eam/leased-license-management` — Delete multiple Leased License resources by name — `src:[1253809,1256445)`
- **Get Hardware License Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.eam/hw-license-management/{name}` — Retrieve configuration details about a specific Hardware License… — `src:[1436860,1439254)`
- **Get Leased License Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.eam/leased-license-management/{name}` — Retrieve configuration details about a specific Leased License… — `src:[1439354,1442564)`
- **List Hardware License Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.eam/hw-license-management` — List all Hardware License resources, in verbose format, including… — `src:[3444931,3449220)`
- **List Leased License Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.eam/leased-license-management` — List all Leased License resources, in verbose format, including… — `src:[3449313,3454418)`
- **Get Hardware License Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.eam/hw-license-management` — List all Hardware License resources, including each resource’s… — `src:[4198123,4201030)`
- **Get Leased License Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.eam/leased-license-management` — List all Leased License resources, including each resource’s name… — `src:[4201124,4204027)`
- **Rename Hardware License** — `POST /data/api/v1/resources/rename/com.inductiveautomation.eam/hw-license-management/{name}` — Change the name of a Hardware License resource, and update all… — `src:[4317629,4320616)`
- **Rename Leased License** — `POST /data/api/v1/resources/rename/com.inductiveautomation.eam/leased-license-management/{name}` — Change the name of a Leased License resource, and update all… — `src:[4320719,4323702)`
- **Describe Hardware License Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.eam/hw-license-management` — Provide information about the Hardware License resource type,… — `src:[4543996,4545391)`
- **Describe Leased License Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.eam/leased-license-management` — Provide information about the Leased License resource type,… — `src:[4545484,4546875)`

## `module-certificate`

- **Modify Module Certificate** — `PUT /data/api/v1/resources/com.inductiveautomation.eam/module-certificates` — Modify one or more Module Certificate resources — `src:[491975,496937)`
- **Create Module Certificate** — `POST /data/api/v1/resources/com.inductiveautomation.eam/module-certificates` — Create a new Module Certificate resource — `src:[496945,502746)`
- **Delete Module Certificate** — `DELETE /data/api/v1/resources/com.inductiveautomation.eam/module-certificates/{name}/{signature}` — Delete a Module Certificate resource by name — `src:[502850,505613)`
- **Delete Module Certificate (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.eam/module-certificates` — Delete multiple Module Certificate resources by name — `src:[1256535,1259179)`
- **Get Module Certificate Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.eam/module-certificates/{name}` — Retrieve configuration details about a specific Module… — `src:[1442658,1445014)`
- **List Module Certificate Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.eam/module-certificates` — List all Module Certificate resources, in verbose format,… — `src:[3454505,3458756)`
- **Get Module Certificate Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.eam/module-certificates` — List all Module Certificate resources, including each resource’s… — `src:[4204115,4207026)`
- **Rename Module Certificate** — `POST /data/api/v1/resources/rename/com.inductiveautomation.eam/module-certificates/{name}` — Change the name of a Module Certificate resource, and update all… — `src:[4323799,4326790)`
- **Describe Module Certificate Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.eam/module-certificates` — Provide information about the Module Certificate resource type,… — `src:[4546962,4548361)`

## `module-eula`

- **Modify Module EULA** — `PUT /data/api/v1/resources/com.inductiveautomation.eam/module-eulas` — Modify one or more Module EULA resources — `src:[505688,510457)`
- **Create Module EULA** — `POST /data/api/v1/resources/com.inductiveautomation.eam/module-eulas` — Create a new Module EULA resource — `src:[510465,516073)`
- **Delete Module EULA** — `DELETE /data/api/v1/resources/com.inductiveautomation.eam/module-eulas/{name}/{signature}` — Delete a Module EULA resource by name — `src:[516170,518912)`
- **Delete Module EULA (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.eam/module-eulas` — Delete multiple Module EULA resources by name — `src:[1259262,1261885)`
- **Get Module EULA Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.eam/module-eulas/{name}` — Retrieve configuration details about a specific Module EULA resource — `src:[1445101,1447264)`
- **List Module EULA Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.eam/module-eulas` — List all Module EULA resources, in verbose format, including… — `src:[3458836,3462894)`
- **Get Module EULA Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.eam/module-eulas` — List all Module EULA resources, including each resource’s name… — `src:[4207107,4209997)`
- **Rename Module EULA** — `POST /data/api/v1/resources/rename/com.inductiveautomation.eam/module-eulas/{name}` — Change the name of a Module EULA resource, and update all… — `src:[4326880,4329850)`
- **Describe Module EULA Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.eam/module-eulas` — Provide information about the Module EULA resource type,… — `src:[4548441,4549819)`

## `module-settings`

- **Modify Module Settings** — `PUT /data/api/v1/resources/com.inductiveautomation.eam/module-settings` — Modify one or more Module Settings resources — `src:[518990,529135)`
- **Create Module Settings** — `POST /data/api/v1/resources/com.inductiveautomation.eam/module-settings` — Create the Module Settings resource — `src:[529143,540112)`
- **Delete Module Settings** — `DELETE /data/api/v1/resources/com.inductiveautomation.eam/module-settings/{signature}` — Delete the Module Settings resource. — `src:[540205,542485)`
- **Get Module Settings Config** — `GET /data/api/v1/resources/singleton/com.inductiveautomation.eam/module-settings` — Retrieve configuration details about the Module Settings resource — `src:[4432527,4439996)`
- **Describe Module Settings Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.eam/module-settings` — Provide information about the Module Settings resource type,… — `src:[4549902,4554202)`

## `remote-upgrade`

- **Modify Remote Upgrade** — `PUT /data/api/v1/resources/com.inductiveautomation.eam/remote-upgrade` — Modify one or more Remote Upgrade resources — `src:[542562,549828)`
- **Create Remote Upgrade** — `POST /data/api/v1/resources/com.inductiveautomation.eam/remote-upgrade` — Create a new Remote Upgrade resource — `src:[549836,557941)`
- **Delete Remote Upgrade** — `DELETE /data/api/v1/resources/com.inductiveautomation.eam/remote-upgrade/{name}/{signature}` — Delete a Remote Upgrade resource by name — `src:[558040,560791)`
- **Delete Remote Upgrade (multiple)** — `POST /data/api/v1/resources/delete/com.inductiveautomation.eam/remote-upgrade` — Delete multiple Remote Upgrade resources by name — `src:[1261970,1264602)`
- **Get Remote Upgrade Config** — `GET /data/api/v1/resources/find/com.inductiveautomation.eam/remote-upgrade/{name}` — Retrieve configuration details about a specific Remote Upgrade… — `src:[1447353,1452013)`
- **List Remote Upgrade Resources** — `GET /data/api/v1/resources/list/com.inductiveautomation.eam/remote-upgrade` — List all Remote Upgrade resources, in verbose format, including… — `src:[3462976,3469531)`
- **Get Remote Upgrade Names** — `GET /data/api/v1/resources/names/com.inductiveautomation.eam/remote-upgrade` — List all Remote Upgrade resources, including each resource’s name… — `src:[4210080,4212979)`
- **Rename Remote Upgrade** — `POST /data/api/v1/resources/rename/com.inductiveautomation.eam/remote-upgrade/{name}` — Change the name of a Remote Upgrade resource, and update all… — `src:[4329942,4332921)`
- **Describe Remote Upgrade Resource Type** — `GET /data/api/v1/resources/type/com.inductiveautomation.eam/remote-upgrade` — Provide information about the Remote Upgrade resource type,… — `src:[4554284,4555671)`

## `storage`

- **Delete temp folder** — `DELETE /data/eam/api/v1/eam-tasks/delete-temp-folder` — Deletes the folder containing the previously uploaded files for… — `src:[4926042,4926778)`
- **Retrieve module certificate** — `GET /data/eam/api/v1/eam-tasks/module-cert` — Retrieves module certificate information from an uploaded .modl file — `src:[4931093,4932075)`
- **Accept module certificate** — `POST /data/eam/api/v1/eam-tasks/module-cert` — Adds a resource to the controller indicating that the user has… — `src:[4932083,4932865)`
- **Retrieve module EULA** — `GET /data/eam/api/v1/eam-tasks/module-eula` — Retrieves a module EULA from an uploaded .modl file. — `src:[4932915,4933667)`
- **Accept module EULA** — `POST /data/eam/api/v1/eam-tasks/module-eula` — Adds a resource to the controller indicating that the user has… — `src:[4933675,4934689)`
- **Upload .gwbk file** — `POST /data/eam/api/v1/eam-tasks/store-gwbk` — Uploads a .gwbk for temporary storage on the controller. — `src:[4940042,4940719)`
- **Upload .modl file** — `POST /data/eam/api/v1/eam-tasks/store-module` — Uploads a .modl file for temporary storage on the controller. — `src:[4940771,4942381)`
- **Store module file from archive** — `POST /data/eam/api/v1/eam-tasks/store-module-from-archive` — Adds an archived module file to temporary storage on the controller. — `src:[4942446,4943983)`
- **Upload upgrade zip** — `POST /data/eam/api/v1/eam-tasks/store-upgrade-zip` — Upload an Ignition upgrade.zip for temporary storage on the… — `src:[4944040,4944810)`
- **Retrieve archived backups** — `GET /data/eam/api/v1/storage/archived-backups` — Retrieves a list of gateway backups for the specified agents. — `src:[4946695,4947608)`
- **Retrieve archived modules** — `GET /data/eam/api/v1/storage/archived-modules` — Retrieves a list of modules archived on the EAM controller. — `src:[4947661,4948247)`
