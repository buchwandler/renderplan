# renderplan MVP

`renderplan` is a deliberately small Python contract package shared by **UtterPlan**, **VoiceRender**, and **AudioCompose**. It contains no SSMD compiler, NLP, TTS engine, G2P, waveform, DSP, or orchestration implementation.

The intended dependency graph is:

```text
utterplan ──────┐
voicerender ────┼──> renderplan
audiocompose ──┘
```

The applications do not depend on each other; the contract package is their only shared dependency.

## RenderPlan-set collection contract

RenderPlan owns the immutable `renderplan-set` v1 collection/index contract shared by the
speech pipeline. UtterPlan's `render-book` operation produces the index alongside its
per-chapter RenderPlans; UtterPlan continues to own book-workspace discovery,
segmentation, and its invocation report. The set contract records chapter ordering,
profile identity, and normalized artifact references without performing orchestration.

RenderPlan provides `RenderPlanSet` and `RenderPlanSetChapter`, strict TOML codecs,
profile snapshot identity validation, and `load_any()` dispatch for `renderplan-set`.
Filesystem containment and symlink checks remain the consumers' responsibility.
RenderPlan does not discover SSMDBook workspaces, batch chapter files, import UtterPlan
or VoiceRender, or add execution/runtime orchestration.

## Contracts

The package defines five TOML contract documents:

1. `renderprofile.toml` — seeded by VoiceRender and user-owned. It contains opaque, self-contained renderer bindings, portable requirements, optional capacity descriptions, split/fallback policy, and explicit pause durations. Shared pause modes are `manual` and `none`; orchestration pass limits do not belong in this contract.
2. `renderplan.toml` — produced by UtterPlan. Its ordered flow is the exact serial execution contract: numbered speech requests, exact `NNNNNN.wav` names, generic pauses, audio items, controls, tokens, source provenance, and role bindings copied from the profile.
3. `renderplan-preflight.toml` — produced by VoiceRender for one exact RenderPlan. It has status `ok`, `replan`, or `reject` and refers to speech occurrences by `(segment_number, segment_id)`.
4. `render-manifest.toml` — produced by VoiceRender. It binds WAV metadata to exact `(segment_number, segment_id)` plan occurrences. A complete manifest contains exactly every plan speech occurrence; an incomplete one may contain only occurrences from that plan.
5. `renderplan-set.toml` — produced by UtterPlan in `render-book` workflows and defined by RenderPlan. It records a safe variant, profile identity/snapshot references, upstream workspace references, and ordered chapter references with source-plan and RenderPlan identities.

Consumers can use the shared joins instead of implementing their own parsers:

```python
plan.validate_against_profile(profile)
report.validate_against(plan)
manifest.validate_against(plan)
validate_plan_profile(plan, profile)
validate_preflight_handoff(plan, report, require_ok=True)
validate_manifest_handoff(plan, manifest, require_complete=True)
validate_set_chapter_plan(renderplan_set, chapter, plan)
```

These are portable contract checks only; they do not resolve engines, inspect WAV files, or orchestrate a pipeline.

## UtterPlan v7 source and token mapping

The first producer uses zero-based v7 provenance locators such as `f000000.s000`, `f000000.s001`, and `f000001.s000`: six digits for the flow index and three for the segment index. A locator is an opaque provenance address, meaningful only together with `RenderPlan.source_plan_id`; it is not a globally stable segment ID. `renderplan` does not parse or resolve locators and has no UtterPlan dependency.

`SourceSpan.start` and `.end` are Python string/code-point offsets into the prepared `FlowSegment.text`. For every speech request, UtterPlan must prove:

```text
SpeechItem.text == concatenation, in order, of each referenced FlowSegment.text[start:end]
```

Do not trim request-edge whitespace. These offsets also map source spans and request-local controls/tokens deterministically. Source-to-text equality is proved by UtterPlan against its source document, not by `renderplan`.
The destination start offset of source span `N` inside the request is the sum of the lengths of all preceding source slices. This preserves a direct mapping without trimming or a second offset table.

`TokenBundle.span` is a dense, ordered list of request-local `[start, end]` character ranges; `pos` and `tag`, when present, are dense parallel arrays. `lemma` and `morph` are sparse arrays of `[token_index, value]`. VoiceRender derives a token surface from `speech.text[start:end]`. Profile-required token fields must be complete under those semantics; available optional fields should be preserved, but `lemma` and `morph` should not be declared required unless an adapter genuinely requires them.

`SpeechControl` is request-local and renderer-neutral: it carries `start`, `end`, `name`, and TOML-safe `params`. Initial semantic names include `pronunciation`, `prosody`, `emphasis`, `say_as`, and `extension:<name>`. Controls participate in `segment_id`; renderers decide support, and unsupported controls must fail preflight rather than be ignored. Control ranges may overlap only when the source semantics permit it. Engine-specific voice selection is resolved into the opaque logical role/binding; audio directives become `AudioItem`s.

### Standard `say_as` semantics

`SpeechControl(name="say_as")` requires a non-empty `interpret_as` string and accepts optional non-empty `format` and `detail` strings. The standard value `interpret_as = "characters"` means: speak the covered text as a sequence of separately pronounced characters, preserving the surrounding request as one continuous speech request when the engine can do so. It is portable intent; RenderPlan does not dictate an engine's realization or restrict future `interpret_as` values.

For example, the following control covers exactly `A R T`:

```toml
[[flow.control]]
start = 0
end = 5
name = "say_as"

[flow.control.params]
interpret_as = "characters"
```

The exact-range invariant is `speech.text[0:5] == "A R T"`. The control does not rewrite the canonical request text, prescribe spaces, hyphens, commas, SSML, phonemes, or pauses, and does not split a sentence or add a `PauseItem`. A renderer that does not support the requested semantics must reject the request during preflight rather than silently ignore the control.

Do not store `A- R- T` in RenderPlan merely because one Kokoro frontend prefers that spelling; an engine may choose such a frontend-specific realization internally.

Bindings copied into plans must be self-contained enough to execute without reopening the RenderProfile. This contract does not define renderer-specific path keys or local-asset resolution.
This schema has no generic asset-reference type yet. A VoiceRender profile producer must refuse bindings that depend on profile-local or otherwise uncopied asset state; do not introduce renderer-specific asset-path keys into this shared contract.

## Identity and replan contract

`segment_id` identifies the exact synthesis request: text, language, role, binding, tokens, and controls. It excludes display number and plan provenance such as source spans, split reason, planning level, and semantic end. Duplicate requests may therefore share a `segment_id`; `(segment_number, segment_id)` identifies each occurrence within a plan, report, or manifest.

`plan_id` identifies the complete ordered execution flow, including speech numbers/order, request IDs, provenance and planning semantics, preflight requirements, every pause, and every audio item. Producer package versions do not change semantic profile or plan identity. Exact source-file bytes are not included in RenderPlan identity.

A PreflightReport describes failures only for its concrete RenderPlan; it cannot preserve successful capacity splits from earlier attempts. The replan join is therefore:

```text
original UtterPlan + RenderProfile + previous RenderPlan
                           + PreflightReport for that previous RenderPlan
                              -> new RenderPlan
```

UtterPlan uses the previous plan as the partition baseline and rebuilds only failed source ranges. Each replan request must correspond to a `too_long` check and exactly match that speech occurrence's source coverage and current planning level. VoiceRender preflight never edits the plan. `replan` is only for recoverable capacity failures; missing engines, unknown voices, unsupported controls, invalid token fields, and frontend/API failures are `reject` conditions.

A `PauseItem` is a generic serial event positioned by `flow`, with duration, reason, and optional provenance; it can lead a flow or follow speech, audio, or another operation. No preceding speech number is required. The ordered flow is authoritative for AudioCompose.

## Example preflight occurrence

```toml
[[checks]]
segment_number = 37
segment_id = "sha256:..."
status = "too_long"
metric = "model_tokens"
measured = 532
maximum = 510
source = "pykokoro.prepare"

[[replan]]
segment_number = 37
segment_id = "sha256:..."
current_level = "sentence"
action = "split"
reason = "capacity"
metric = "model_tokens"
measured = 532
maximum = 510

[[replan.source]]
locator = "f000123.s004"
start = 0
end = 42
```

UtterPlan does not need to understand `model_tokens`; the source-plan ID, copied profile ID, exact plan ID, source ranges, and generic split level/action allow stale feedback to be rejected without importing VoiceRender.

## Install for development

This repository intentionally has **no `src/` directory**.

```bash
python -m pip install -e '.[dev]'
pytest
ruff check .
mypy renderplan
```

Versioning is dynamic through `setuptools-scm`; create Git tags such as `v0.1.0`. A source tree without Git metadata uses the configured development fallback version.

## Example API

```python
from renderplan import load_plan, load_preflight

plan = load_plan("chapter.renderplan.toml")
report = load_preflight("chapter.preflight.toml")

report.validate_against(plan)
if report.status == "replan":
    for request in report.replan:
        print(request.segment_number, request.action, request.source)
```

## MVP invariants

- Persisted identities are SHA-256 over canonical semantic payloads; producer versions are provenance, not identity.
- Speech numbers are contiguous starting at 1; speech `N` maps to `NNNNNN.wav`.
- Speech-request identity is stable across renumbering; full plan identity changes when flow order or execution semantics change.
- `RenderPlan.validate_against_profile`, `PreflightReport.validate_against`, and `RenderManifest.validate_against` enforce portable joins and exact occurrence matching.
- VoiceRender preflight never changes the plan, and rendering never introduces unplanned speech segments.
- AudioCompose consumes RenderPlan flow and RenderManifest artifacts; it does not reconstruct pauses from UtterPlan.
- Engine-specific binding tables are opaque to UtterPlan and AudioCompose.
