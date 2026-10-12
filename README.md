# renderplan MVP

`renderplan` is a deliberately small Python contract package shared by **UtterPlan**, **VoiceRender**, and **AudioCompose**. It contains no SSMD compiler, NLP, TTS engine, G2P, waveform, or DSP implementation.

The intended dependency graph is:

```text
utterplan ──────┐
                │
voicerender ────┼──> renderplan
                │
audiocompose ───┘
```

The applications do not depend on each other.

## Contracts

The MVP defines four TOML documents.

1. `renderprofile.toml` — produced/seeded by VoiceRender and then user-owned. It contains opaque renderer bindings, portable requirements, portable/advisory planning limits, the requested split policy, and concrete pause policy.
2. `renderplan.toml` — produced by UtterPlan. It is an ordered execution flow containing numbered speech requests, exact `NNNNNN.wav` names, pauses, external audio items, token metadata, source provenance, and renderer binding data copied from the profile.
3. `renderplan-preflight.toml` — produced by VoiceRender before synthesis. It binds to one RenderPlan and is either `status = "ok"`, `status = "replan"`, or `status = "reject"`.
4. `render-manifest.toml` — produced by VoiceRender during/after synthesis. It binds rendered WAV metadata to exact RenderPlan speech segment IDs.

## Preflight loop

The important contract is that VoiceRender may **validate and reject** a RenderPlan but may not silently change its segmentation.

```text
UtterPlan + RenderProfile
        |
        v
RenderPlan pass 1
        |
        v
VoiceRender preflight
        |
        +-- OK ----------------------> render exact numbered segments
        |
        `-- REPLAN (segment 37) ----> UtterPlan splits that planned segment at
                                      the next configured fallback boundary
                                      and writes a new RenderPlan
```

A backend-specific measurement remains useful to humans and VoiceRender:

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
source = ["f000123.s004", "f000123.s005"]
current_level = "sentence"
action = "split"
reason = "capacity"
metric = "model_tokens"
measured = 532
maximum = 510
```

UtterPlan does **not** need to understand `model_tokens`. It uses the stable source locators, `current_level`, and generic `action = "split"`. Its original semantic graph plus the RenderProfile fallback sequence, for example `sentence -> clause -> token`, is sufficient to rebuild the failed request. The preflight document also binds the original `source_plan_id` and `render_profile_id`, so stale feedback can be rejected without importing VoiceRender.

If no safe fallback remains, UtterPlan fails instead of inventing a cut.

## Why this avoids false portability

A renderer such as Kokoro may only know the true request size after its own frontend has produced model tokens. The MVP does not pretend that UtterPlan can duplicate that frontend.

A RenderProfile may still contain portable planner limits such as `linguistic_tokens`, but they are advisory unless the renderer can truthfully mark them `hard`. The authoritative backend check happens in preflight.

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

assert report.render_plan_id == plan.plan_id
if report.status == "replan":
    for request in report.replan:
        print(request.segment_number, request.action)
```

## MVP invariants

- Persisted identities are SHA-256 over canonical semantic payloads.
- Producer package versions are provenance and do not alter plan/profile identity.
- Speech numbers are contiguous starting at 1.
- Speech `N` always maps to `NNNNNN.wav`.
- A pause explicitly follows the immediately preceding speech number.
- VoiceRender preflight never changes the plan.
- VoiceRender render never introduces an unplanned child speech segment.
- AudioCompose consumes RenderPlan flow and RenderManifest artifacts; it does not reconstruct pauses from UtterPlan.
- Engine-specific binding tables are opaque to UtterPlan and AudioCompose.
