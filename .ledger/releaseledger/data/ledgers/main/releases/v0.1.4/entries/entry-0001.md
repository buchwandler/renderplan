---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.1.4
kind: added
summary: Added portable character interpretation semantics to speech controls
status: accepted
audience: null
scopes: []
source_refs:
  - tl:task-0004
paths:
  - renderplan/model.py
  - renderplan/contracts.py
  - README.md
issues: []
prs: []
sources: []
contributors: []
breaking: false
internal: false
order: 1
---

RenderPlan formalizes say_as parameter validation while preserving exact request text and leaving engine-specific realization to renderer preflight.
