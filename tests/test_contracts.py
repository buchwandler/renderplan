from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from renderplan import (
    AudioItem,
    CapacityConstraint,
    CodecError,
    PauseItem,
    PausePolicy,
    PlanningPolicy,
    PreflightCheck,
    PreflightReport,
    Producer,
    RenderManifest,
    RenderPlan,
    RenderProfile,
    ReplanRequest,
    RoleProfile,
    RoleRequirements,
    SourceSpan,
    SpeechArtifact,
    SpeechControl,
    SpeechItem,
    TokenBundle,
    ValidationError,
    load_any,
    load_manifest,
    load_plan,
    load_preflight,
    load_profile,
    save_manifest,
    save_plan,
    save_preflight,
    save_profile,
)
from renderplan.cli import main
from renderplan.codec import plan_from_dict, profile_from_dict, profile_to_dict

SOURCE_ID = "sha256:" + "1" * 64
OTHER_SOURCE_ID = "sha256:" + "4" * 64
WAV_SHA = "sha256:" + "3" * 64
BINDING = {"renderer": "kokoro", "voice": "af_heart", "target_id": "kokoro-v1"}


def profile(
    *, required: tuple[str, ...] = (), producer_version: str = "0.2.0"
) -> RenderProfile:
    return RenderProfile(
        default_role="narrator",
        producer=Producer("voicerender", producer_version),
        roles={
            "narrator": RoleProfile(
                language="en-US",
                binding=BINDING,
                requirements=RoleRequirements(token_fields=required),
                planner_capacity=CapacityConstraint("linguistic_tokens", 180, "advisory"),
                renderer_capacity=CapacityConstraint("model_tokens", 510, "hard"),
            )
        },
        planning=PlanningPolicy(split="sentence", capacity_fallback=("clause", "token")),
        pauses=PausePolicy("manual", {"sentence": 500, "clause": 180}),
    ).with_identity()


def speech(
    number: int = 1,
    text: str = "I agree.",
    *,
    source: tuple[SourceSpan, ...] | None = None,
    binding: dict[str, Any] | None = None,
    controls: tuple[SpeechControl, ...] = (),
    tokens: TokenBundle | None = None,
    planning_level: str = "sentence",
    split_reason: str = "policy",
    preflight_required: bool = True,
) -> SpeechItem:
    selected_source = source or (SourceSpan("f000000.s000", 0, len(text)),)
    return SpeechItem(
        number=number,
        text=text,
        language="en-US",
        role="narrator",
        binding=binding or BINDING,
        source=selected_source,
        split_reason=split_reason,
        planning_level=planning_level,
        semantic_end="sentence",
        tokens=tokens,
        controls=controls,
        preflight_required=preflight_required,
    ).with_identity()


def plan(
    *speeches: SpeechItem,
    flow: tuple[SpeechItem | PauseItem | AudioItem, ...] | None = None,
    profile_value: RenderProfile | None = None,
) -> RenderPlan:
    selected_profile = profile_value or profile()
    ordered_flow = flow if flow is not None else tuple(speeches)
    return RenderPlan(
        source_plan_id=SOURCE_ID,
        render_profile_id=selected_profile.profile_id or selected_profile.computed_id(),
        flow=ordered_flow,
        producer=Producer("utterplan", "7.0.0"),
    ).with_identity()


def profile_identity(value: RenderProfile) -> str:
    return value.profile_id or value.computed_id()


def plan_identity(value: RenderPlan) -> str:
    return value.plan_id or value.computed_id()


def check(
    item: SpeechItem, status: Literal["ok", "too_long", "error"] = "ok"
) -> PreflightCheck:
    return PreflightCheck(
        segment_number=item.number,
        segment_id=item.segment_id or item.computed_id(),
        status=status,
        metric="model_tokens",
        measured=532 if status == "too_long" else 4,
        maximum=510,
        source="test.frontend",
    )


def report(
    value: RenderPlan,
    *,
    status: Literal["ok", "replan", "reject"] = "ok",
    checks: tuple[PreflightCheck, ...] | None = None,
    replan: tuple[ReplanRequest, ...] = (),
    source_plan_id: str | None = None,
    render_profile_id: str | None = None,
    render_plan_id: str | None = None,
) -> PreflightReport:
    return PreflightReport(
        render_plan_id=render_plan_id or value.plan_id or value.computed_id(),
        source_plan_id=source_plan_id or value.source_plan_id,
        render_profile_id=render_profile_id or value.render_profile_id,
        status=status,
        checks=checks
        if checks is not None
        else tuple(check(item) for item in value.speech if item.preflight_required),
        replan=replan,
    )


def artifact(
    item: SpeechItem, *, number: int | None = None, segment_id: str | None = None
) -> SpeechArtifact:
    selected_number = item.number if number is None else number
    return SpeechArtifact(
        segment_number=selected_number,
        segment_id=segment_id or item.segment_id or item.computed_id(),
        wav=f"{selected_number:06d}.wav",
        sha256=WAV_SHA,
        frames=24000,
        sample_rate=24000,
        channels=1,
    )


def test_profile_roundtrip(tmp_path: Path) -> None:
    value = profile()
    path = tmp_path / "voice.renderprofile.toml"
    save_profile(value, path)
    loaded = load_profile(path)
    assert loaded == value
    assert loaded.profile_id == value.profile_id


def test_native_pause_mode_is_not_part_of_shared_profile_contract() -> None:
    with pytest.raises(ValidationError, match="manual or none"):
        PausePolicy(cast(Any, "native"))


def test_renderplan_roundtrip_with_controls_and_generic_binding(tmp_path: Path) -> None:
    generic_binding = {
        "renderer": "opaque-adapter",
        "target_id": "target-v1",
        "voice": "voice-a",
        "vendor_options": {"layers": [1, 2], "enabled": True},
    }
    item = speech(
        text="I agree.",
        controls=(SpeechControl(2, 7, "emphasis", {"level": "moderate"}),),
        binding=generic_binding,
    )
    value = plan(item)
    path = tmp_path / "chapter.renderplan.toml"
    save_plan(value, path)
    loaded = load_plan(path)
    assert loaded == value
    assert loaded.speech[0].wav == "000001.wav"
    assert loaded.speech[0].binding == generic_binding
    assert loaded.speech[0].controls[0].params == {"level": "moderate"}


def test_leading_pause_is_legal() -> None:
    item = speech()
    value = plan(flow=(PauseItem(120, "authored"), item))
    assert isinstance(value.flow[0], PauseItem)


def test_pause_after_audio_item_is_legal() -> None:
    item = speech()
    audio = AudioItem("media/door.wav", item.source)
    value = plan(flow=(item, audio, PauseItem(80, "between_operations")))
    assert isinstance(value.flow[-1], PauseItem)
    assert value.flow[-1].reason == "between_operations"


def test_segment_id_does_not_change_when_number_changes() -> None:
    item = speech()
    renumbered = replace(item, number=27, segment_id=None).with_identity()
    assert renumbered.segment_id == item.segment_id


def test_plan_id_changes_when_numbering_or_order_changes() -> None:
    first = speech(1, "First.", source=(SourceSpan("f000000.s000", 0, 6),))
    second = speech(2, "Second.", source=(SourceSpan("f000000.s001", 0, 7),))
    original = plan(first, second)
    reordered = plan(
        replace(second, number=1, segment_id=None).with_identity(),
        replace(first, number=2, segment_id=None).with_identity(),
    )
    assert original.plan_id != reordered.plan_id


def test_segment_id_changes_when_text_changes() -> None:
    item = speech()
    changed = replace(item, text="I agree!", segment_id=None).with_identity()
    assert item.segment_id != changed.segment_id


def test_segment_id_changes_when_binding_changes() -> None:
    item = speech()
    changed = replace(
        item, binding={**BINDING, "voice": "another"}, segment_id=None
    ).with_identity()
    assert item.segment_id != changed.segment_id


def test_segment_id_changes_when_controls_change() -> None:
    original = speech(controls=(SpeechControl(0, 1, "emphasis", {"level": "mild"}),))
    changed = replace(
        original,
        controls=(SpeechControl(0, 1, "emphasis", {"level": "strong"}),),
        segment_id=None,
    ).with_identity()
    assert original.segment_id != changed.segment_id


def test_source_and_provenance_change_plan_id_not_segment_id() -> None:
    original = speech()
    changed = replace(
        original,
        source=(SourceSpan("different.opaque.locator", 4, 12),),
        split_reason="capacity",
        planning_level="clause",
        semantic_end="clause",
        segment_id=None,
    ).with_identity()
    assert changed.segment_id == original.segment_id
    assert plan(original).plan_id != plan(changed).plan_id


def test_validate_plan_against_profile_checks_role_binding_language_and_tokens() -> None:
    required_profile = profile(required=("span", "pos"))
    missing = plan(speech(), profile_value=required_profile)
    with pytest.raises(ValidationError, match="missing required token fields"):
        missing.validate_against_profile(required_profile)

    tokens = TokenBundle(span=((0, 8),), pos=("VERB",))
    valid = plan(speech(tokens=tokens), profile_value=required_profile)
    valid.validate_against_profile(required_profile)

    wrong_binding = plan(speech(binding={"renderer": "other"}), profile_value=required_profile)
    with pytest.raises(ValidationError, match="binding does not match"):
        wrong_binding.validate_against_profile(required_profile)

    wrong_language = replace(speech(), language="fr-FR", segment_id=None).with_identity()
    with pytest.raises(ValidationError, match="language"):
        plan(wrong_language, profile_value=required_profile).validate_against_profile(
            required_profile
        )


def test_required_sparse_token_field_must_be_complete() -> None:
    required_profile = profile(required=("lemma",))
    partial = TokenBundle(span=((0, 2), (3, 8)), lemma=((0, "I"),))
    value = plan(speech(tokens=partial), profile_value=required_profile)
    with pytest.raises(ValidationError, match="incomplete required token fields: lemma"):
        value.validate_against_profile(required_profile)


def test_profile_identity_ignores_producer_version() -> None:
    a = profile(producer_version="0.2.0")
    b = profile(producer_version="0.3.0")
    assert a.profile_id == b.profile_id


def test_plan_identity_ignores_producer_version() -> None:
    value = plan(speech())
    other = replace(
        value, producer=Producer("utterplan", "7.1.0"), plan_id=None
    ).with_identity()
    assert value.plan_id == other.plan_id


def test_preflight_validation_rejects_stale_plan_source_and_profile_ids() -> None:
    value = plan(speech())
    valid = report(value)
    with pytest.raises(ValidationError, match="render_plan_id"):
        replace(valid, render_plan_id=OTHER_SOURCE_ID).validate_against(value)
    with pytest.raises(ValidationError, match="source_plan_id"):
        replace(valid, source_plan_id=OTHER_SOURCE_ID).validate_against(value)
    with pytest.raises(ValidationError, match="render_profile_id"):
        replace(valid, render_profile_id=OTHER_SOURCE_ID).validate_against(value)


def test_preflight_rejects_check_with_wrong_segment_id() -> None:
    value = plan(speech())
    wrong = PreflightCheck(1, OTHER_SOURCE_ID, "ok", "tokens", 1, 10, "test")
    with pytest.raises(ValidationError, match="unknown speech occurrence"):
        report(value, checks=(wrong,)).validate_against(value)


def test_ok_preflight_requires_checks_for_every_required_occurrence() -> None:
    first = speech(1, "First.", source=(SourceSpan("f000000.s000", 0, 6),))
    second = speech(2, "Second.", source=(SourceSpan("f000000.s001", 0, 7),))
    value = plan(first, second)
    incomplete = report(value, checks=(check(first),))
    with pytest.raises(ValidationError, match="missing required speech checks"):
        incomplete.validate_against(value)
    report(value).validate_against(value)


def test_preflight_rejects_extra_checks_for_unrequired_speech() -> None:
    optional = speech(preflight_required=False)
    value = plan(optional)
    with pytest.raises(ValidationError, match="not requiring preflight"):
        report(value, checks=(check(optional),)).validate_against(value)
    report(value, checks=()).validate_against(value)


def replan_request(item: SpeechItem, **changes: object) -> ReplanRequest:
    return ReplanRequest(
        segment_number=item.number,
        segment_id=item.segment_id or item.computed_id(),
        source=cast(tuple[SourceSpan, ...], changes.get("source", item.source)),
        current_level=str(changes.get("current_level", item.planning_level)),
    )


def test_replan_source_must_equal_failed_speech_source() -> None:
    item = speech()
    value = plan(item)
    request = replan_request(item, source=(SourceSpan("f000000.s000", 1, 8),))
    checked = report(
        value, status="replan", checks=(check(item, "too_long"),), replan=(request,)
    )
    with pytest.raises(ValidationError, match="source does not match"):
        checked.validate_against(value)


def test_replan_level_must_match_failed_speech_level() -> None:
    item = speech(planning_level="sentence")
    value = plan(item)
    request = replan_request(item, current_level="clause")
    checked = report(
        value, status="replan", checks=(check(item, "too_long"),), replan=(request,)
    )
    with pytest.raises(ValidationError, match="current_level"):
        checked.validate_against(value)


def test_replan_must_refer_to_too_long_check_and_passes_when_exact() -> None:
    item = speech()
    value = plan(item)
    exact = report(
        value,
        status="replan",
        checks=(check(item, "too_long"),),
        replan=(replan_request(item),),
    )
    exact.validate_against(value)
    not_failed = report(
        value,
        status="replan",
        checks=(check(item, "ok"),),
        replan=(replan_request(item),),
    )
    with pytest.raises(ValidationError, match="too_long"):
        not_failed.validate_against(value)


def test_reject_report_cannot_contain_replan_requests() -> None:
    item = speech()
    with pytest.raises(ValidationError, match="reject preflight"):
        report(
            plan(item),
            status="reject",
            checks=(check(item, "too_long"),),
            replan=(replan_request(item),),
        )


def test_complete_manifest_must_contain_exactly_all_plan_speech_occurrences() -> None:
    first = speech(1, "First.", source=(SourceSpan("f000000.s000", 0, 6),))
    second = speech(2, "Second.", source=(SourceSpan("f000000.s001", 0, 7),))
    value = plan(first, second)
    complete = RenderManifest(plan_identity(value), (artifact(first), artifact(second)), True)
    complete.validate_against(value)
    missing = RenderManifest(plan_identity(value), (artifact(first),), True)
    with pytest.raises(ValidationError, match="missing speech occurrences"):
        missing.validate_against(value)


def test_manifest_rejects_extra_occurrence_even_when_complete() -> None:
    item = speech()
    value = plan(item)
    extra = artifact(item, number=2, segment_id=OTHER_SOURCE_ID)
    manifest = RenderManifest(plan_identity(value), (artifact(item), extra), True)
    with pytest.raises(ValidationError, match="not in plan"):
        manifest.validate_against(value)


def test_incomplete_manifest_accepts_subset_and_rejects_non_plan_occurrence() -> None:
    first = speech(1, "First.", source=(SourceSpan("f000000.s000", 0, 6),))
    second = speech(2, "Second.", source=(SourceSpan("f000000.s001", 0, 7),))
    value = plan(first, second)
    RenderManifest(plan_identity(value), (artifact(first),), False).validate_against(value)
    foreign = artifact(second, segment_id=OTHER_SOURCE_ID)
    manifest = RenderManifest(plan_identity(value), (foreign,), False)
    with pytest.raises(ValidationError, match="not in plan"):
        manifest.validate_against(value)


def test_zero_duration_pause_roundtrips_exactly(tmp_path: Path) -> None:
    item = speech()
    value = plan(flow=(PauseItem(0, "leading"), item, PauseItem(0, "after_speech")))
    path = tmp_path / "zero-pauses.toml"
    save_plan(value, path)
    loaded = load_plan(path)
    assert loaded == value
    assert [event.duration_ms for event in loaded.flow if isinstance(event, PauseItem)] == [
        0,
        0,
    ]


def test_unknown_contract_fields_are_rejected_at_multiple_levels() -> None:
    raw_profile = profile_to_dict(profile())
    raw_profile["unexpected"] = True
    with pytest.raises(CodecError, match="unknown field"):
        profile_from_dict(raw_profile)

    raw_profile = profile_to_dict(profile())
    raw_profile["planning"]["max_preflight_passes"] = 3
    with pytest.raises(CodecError, match="planning contains unknown"):
        profile_from_dict(raw_profile)

    item = speech(controls=(SpeechControl(0, 1, "emphasis"),))
    raw_plan = {
        "format": "renderplan",
        "schema_version": 1,
        "source_plan_id": SOURCE_ID,
        "render_profile_id": profile_identity(profile()),
        "flow": [
            {
                "kind": "speech",
                "number": 1,
                "segment_id": item.segment_id,
                "wav": item.wav,
                "text": item.text,
                "language": item.language,
                "role": item.role,
                "binding": dict(item.binding),
                "source": [span.to_dict() for span in item.source],
                "control": [{"start": 0, "end": 1, "name": "emphasis", "unexpected": 1}],
            }
        ],
    }
    with pytest.raises(CodecError, match="unknown field"):
        plan_from_dict(raw_plan)


def test_unknown_binding_fields_remain_opaque() -> None:
    generic = {**BINDING, "vendor_extension": {"flags": ["a", "b"], "enabled": True}}
    item = speech(binding=generic)
    value = plan(item)
    assert value.speech[0].binding == generic


def test_toml_serialization_order_is_stable_for_equivalent_opaque_mappings(
    tmp_path: Path,
) -> None:
    first_binding = {"renderer": "opaque", "target_id": "target", "voice": "v"}
    second_binding = {"voice": "v", "renderer": "opaque", "target_id": "target"}
    first = plan(speech(binding=first_binding))
    second = plan(speech(binding=second_binding))
    assert first.plan_id == second.plan_id
    first_path = tmp_path / "first.toml"
    second_path = tmp_path / "second.toml"
    save_plan(first, first_path)
    save_plan(second, second_path)
    assert first_path.read_text(encoding="utf-8") == second_path.read_text(encoding="utf-8")


def test_unsupported_toml_values_fail() -> None:
    with pytest.raises(ValidationError, match="unsupported TOML value"):
        SpeechControl(0, 1, "extension:date", {"when": datetime.now(timezone.utc)})

    with pytest.raises(ValidationError, match="keys must be strings"):
        SpeechControl(0, 1, "extension:key", cast(Any, {1: "not TOML-safe"}))


def test_source_locators_are_opaque_and_roundtrip(tmp_path: Path) -> None:
    opaque = SourceSpan("not-a-v7-locator/雪", 0, 8)
    item = speech(text="opaque!", source=(opaque,))
    value = plan(item)
    path = tmp_path / "opaque.renderplan.toml"
    save_plan(value, path)
    assert load_plan(path).speech[0].source == (opaque,)


def test_preflight_and_manifest_codecs_roundtrip_and_validate(tmp_path: Path) -> None:
    item = speech()
    value = plan(item)
    preflight = report(value)
    preflight_path = tmp_path / "preflight.toml"
    save_preflight(preflight, preflight_path)
    loaded_preflight = load_preflight(preflight_path)
    assert loaded_preflight == preflight.with_identity()
    loaded_preflight.validate_against(value)

    manifest = RenderManifest(plan_identity(value), (artifact(item),), True)
    manifest_path = tmp_path / "manifest.toml"
    save_manifest(manifest, manifest_path)
    loaded_manifest = load_manifest(manifest_path)
    assert loaded_manifest == manifest.with_identity()
    loaded_manifest.validate_against(value)


def test_checked_in_examples_load_and_join() -> None:
    root = Path(__file__).parents[1]
    profile_value = load_profile(root / "examples/af_heart.renderprofile.toml")
    plan_value = load_plan(root / "examples/chapter.renderplan.toml")
    ok_report = load_preflight(root / "examples/chapter.preflight.toml")
    replan_report = load_preflight(root / "examples/chapter.replan.preflight.toml")
    manifest_value = load_manifest(root / "examples/chapter.render-manifest.toml")
    assert isinstance(load_any(root / "examples/af_heart.renderprofile.toml"), RenderProfile)
    plan_value.validate_against_profile(profile_value)
    ok_report.validate_against(plan_value)
    replan_report.validate_against(plan_value)
    manifest_value.validate_against(plan_value)


def test_cli_inspect_summaries_are_read_only_and_informative(
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = Path(__file__).parents[1]
    for filename, expected in (
        ("af_heart.renderprofile.toml", "fallbacks=clause,token"),
        ("chapter.renderplan.toml", "  pauses:      1"),
        ("chapter.preflight.toml", "  status:      ok"),
        ("chapter.render-manifest.toml", "  complete:    true"),
    ):
        assert main(["inspect", str(root / "examples" / filename)]) == 0
        assert expected in capsys.readouterr().out
