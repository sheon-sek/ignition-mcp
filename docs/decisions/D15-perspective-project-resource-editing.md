# D15 — Perspective / Project Resource Editing

**Status:** DECIDED

## Canonical ownership
Project-local Perspective authoring belongs to the External FastMCP / REST plane.

The canonical workflow is:

```text
Ignition Native REST Project Export
→ internal Project ZIP Resource Adapter
→ typed resource modification
→ offline validation
→ Native REST Project Import
```

Do not use WebDev, direct Gateway filesystem writes, or project scan as the normal authoring path.

Runtime MCP does not edit project resources.

## Public v1 surface
```text
Perspective Views
├── perspective_view_list
├── perspective_view_get
├── perspective_view_validate
├── perspective_view_upsert
└── perspective_view_delete

Perspective Page Configuration
├── perspective_page_config_get
└── perspective_page_config_update

Perspective Session Properties
├── perspective_session_props_get
└── perspective_session_props_update
```

The ZIP/resource adapter is an internal service, not a public generic Tool.

## No generic project-resource editor
Do not expose:
- arbitrary ZIP paths;
- `project_resource_set`;
- `project_resource_delete`;
- generic resource type/content mutation.

Each future project-resource domain must receive its own typed schema, safety, validation, ownership, and verification decision.

## No bootstrap mega-tool
Do not expose a `perspective_bootstrap_project` mega-operation in v1.

Project creation, View mutation, Page configuration, and Session Property mutation remain separate semantic operations so failure, permission, rollback, and verification remain diagnosable.

## View validation
`perspective_view_validate` is an offline structural/semantic validator.

It may check:
- JSON validity;
- known resource structure;
- required View/component fields;
- known component/binding/event shapes;
- size/depth budgets;
- dangerous/unsupported constructs where practical.

It must not claim that offline validation guarantees acceptance by every Ignition patch/module version. Native import plus post-import verification remains authoritative.

## Typed resource adapter
Perspective resources are treated as project resources, not arbitrary files.

The adapter must preserve all unrelated/unknown resources by default.

The mutation model is:
```text
export existing Project
→ copy baseline archive
→ patch only exact typed target resource(s)
→ preserve everything else
```

Do not rebuild an entire Project from only the resource types the server understands.

## Project inheritance
Gateway export is treated as the local-project resource boundary.

For inherited resources:
- reads should clearly identify inherited/owner information when reliably resolvable;
- writes must not silently create a child override.

v1 rejects mutation of a non-local inherited resource rather than implicitly overriding it.

A future explicit inherited-resource override requires its own deliberate API.

## Logical paths only
Public APIs accept logical resource identifiers such as:

```text
Pages/Overview
```

They do not accept archive/filesystem paths.

The server owns logical-resource-to-archive mapping. This prevents path traversal and cross-resource arbitrary writes.

## ZIP safety
Project archives are treated as untrusted structured input even when obtained from the Gateway.

Validate against:
- path traversal;
- absolute paths;
- symlinks;
- duplicate entries;
- excessive entry count;
- excessive expanded size;
- compression bombs;
- invalid/ambiguous paths.

Temporary extraction/workspace resources must be transaction-scoped and cleaned on success/failure/cancellation.

## Binary handling
Project ZIP binary data does not pass through the LLM/MCP JSON body.

D17 Artifact handling is used for explicit import/export. Internal Perspective edit flows keep export/modify/import on the server side.

## Custom themes
Perspective Custom Themes that are exposed as Gateway-level Native REST resources remain owned by the REST configuration plane rather than the Project ZIP adapter.

## Deferred
Architecture may later support typed adapters for:
- Perspective Style Classes;
- Named Query authoring;
- project scripts;
- Alarm Pipeline definitions;
- Reports;
- SFCs;
- Vision resources.

These are not public v1 generic resources.

## Project scan
Project scan is not part of the normal Perspective authoring workflow.

D15 depends on Project Export/Import, not filesystem write + scan.

```yaml
decision: D15
status: DECIDED
project_resource_owner: ignition-rest
authoring_transport: project_export_zip_adapter_import
webdev_project_editing: false
gateway_filesystem_project_editing: false
generic_project_resource_mutation_public: false
silent_inherited_override: false
perspective_style_class_crud_v1: false
```
