from __future__ import annotations

from pathlib import Path

import pytest

from renderplan import (
    CapacityConstraint,
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
    SpeechArtifact,
    SpeechItem,
    SourceSpan,
    TokenBundle,
    ValidationError,
    load_manifest,
    load_plan,
    load_preflight,
    load_profile,
    save_manifest,
    save_plan,
    save_preflight,
    save_profile,
)

SOURCE_ID = "sha256:" + "1" * 64
SOURCE_SHA = "sha256:" + "2" * 64
WAV_SHA = "sha256:" + "3" * 64


def profile() -> RenderProfile:
    return RenderProfile(
        default_role="narrator",
        producer=Producer("voicerender", "0.2.0"),
        roles={
            "narrator": RoleProfile(
                language="en-US",
                binding={"renderer": "kokoro", "voice": "af_heart", "target_id": "kokoro-v1"},
                requirements=RoleRequirements(token_fields=("span", "pos", "tag", "morph")),
                planner_capacity=CapacityConstraint("linguistic_tokens", 180, "advisory"),
                renderer_capacity=CapacityConstraint("model_tokens", 510, "hard"),
            )
        },
        planning=PlanningPolicy(split="sentence", capacity_fallback=("clause", "token")),
        pauses=PausePolicy("manual", {"clause": 180, "sentence": 500, "paragraph": 800}),
    ).with_identity()


def plan() -> RenderPlan:
    p = profile()
    tokens = TokenBundle(
        span=((0, 1), (2, 6), (6, 7)),
        pos=("PRON", "VERB", "PUNCT"),
        tag=("PRP", "VBP", "."),
        morph=((0, "Case=Nom|Number=Sing"),),
    )
    return RenderPlan(
        source_plan_id=SOURCE_ID,
        source_plan_sha256=SOURCE_SHA,
        render_profile_id=p.profile_id or p.computed_id(),
        producer=Producer("utterplan", "0.6.0"),
        flow=(
            SpeechItem(
                number=1,
                text="I agree.",
                language="en-US",
                role="narrator",
                binding={"renderer": "kokoro", "voice": "af_heart", "target_id": "kokoro-v1"},
                source=(SourceSpan("f000001.s001", 0, 8),),
                semantic_end="sentence",
                tokens=tokens,
            ),
            PauseItem(duration_ms=500, reason="sentence", after_speech=1),
        ),
    ).with_identity()


def test_profile_roundtrip(tmp_path: Path) -> None:
    value = profile()
    path = tmp_path / "voice.renderprofile.toml"
    save_profile(value, path)
    loaded = load_profile(path)
    assert loaded == value
    assert loaded.profile_id == value.profile_id


def test_renderplan_roundtrip_and_wav_number(tmp_path: Path) -> None:
    value = plan()
    path = tmp_path / "chapter.renderplan.toml"
    save_plan(value, path)
    loaded = load_plan(path)
    assert loaded == value
    assert loaded.speech[0].wav == "000001.wav"


def test_plan_rejects_noncontiguous_numbers() -> None:
    p = profile()
    speech = SpeechItem(
        number=2,
        text="Hello.",
        language="en-US",
        role="narrator",
        binding={"renderer": "kokoro", "voice": "af_heart"},
        source=(SourceSpan("f1.s1", 0, 6),),
        semantic_end="sentence",
    ).with_identity()
    with pytest.raises(ValidationError, match="contiguous"):
        RenderPlan(
            source_plan_id=SOURCE_ID,
            render_profile_id=p.profile_id or p.computed_id(),
            flow=(speech,),
        )


def test_preflight_replan_roundtrip(tmp_path: Path) -> None:
    value = plan()
    speech = value.speech[0]
    report = PreflightReport(
        render_plan_id=value.plan_id or value.computed_id(),
        source_plan_id=value.source_plan_id,
        render_profile_id=value.render_profile_id,
        status="replan",
        producer=Producer("voicerender", "0.2.0"),
        checks=(
            PreflightCheck(
                segment_number=1,
                segment_id=speech.segment_id or speech.computed_id(),
                status="too_long",
                metric="model_tokens",
                measured=532,
                maximum=510,
                source="pykokoro.prepare",
            ),
        ),
        replan=(
            ReplanRequest(
                segment_number=1,
                segment_id=speech.segment_id or speech.computed_id(),
                source=speech.source,
                current_level=speech.planning_level,
                metric="model_tokens",
                measured=532,
                maximum=510,
            ),
        ),
    ).with_identity()
    path = tmp_path / "chapter.preflight.toml"
    save_preflight(report, path)
    assert load_preflight(path) == report


def test_ok_preflight_cannot_have_split_request() -> None:
    value = plan()
    speech = value.speech[0]
    check = PreflightCheck(
        1,
        speech.segment_id or speech.computed_id(),
        "ok",
        "model_tokens",
        100,
        510,
        "pykokoro.prepare",
    )
    request = ReplanRequest(1, speech.segment_id or speech.computed_id(), speech.source, speech.planning_level)
    with pytest.raises(ValidationError, match="ok preflight"):
        PreflightReport(
            render_plan_id=value.plan_id or value.computed_id(),
            source_plan_id=value.source_plan_id,
            render_profile_id=value.render_profile_id,
            status="ok",
            checks=(check,),
            replan=(request,),
        )


def test_manifest_binds_exact_plan_segments(tmp_path: Path) -> None:
    value = plan()
    speech = value.speech[0]
    manifest = RenderManifest(
        render_plan_id=value.plan_id or value.computed_id(),
        complete=True,
        producer=Producer("voicerender", "0.2.0"),
        speech=(
            SpeechArtifact(
                segment_number=1,
                segment_id=speech.segment_id or speech.computed_id(),
                wav="000001.wav",
                sha256=WAV_SHA,
                frames=24000,
                sample_rate=24000,
                channels=1,
            ),
        ),
    ).with_identity()
    manifest.validate_against(value)
    path = tmp_path / "render-manifest.toml"
    save_manifest(manifest, path)
    assert load_manifest(path) == manifest


def test_token_bundle_must_fit_text() -> None:
    with pytest.raises(ValidationError, match="exceeds speech text"):
        SpeechItem(
            number=1,
            text="x",
            language="en-US",
            role="narrator",
            binding={"renderer": "kokoro"},
            source=(SourceSpan("f1.s1", 0, 6),),
            tokens=TokenBundle(span=((0, 2),)),
        )


def test_persisted_wav_name_is_strict(tmp_path: Path) -> None:
    value = plan()
    path = tmp_path / "chapter.renderplan.toml"
    save_plan(value, path)
    text = path.read_text(encoding="utf-8").replace('wav = "000001.wav"', 'wav = "wrong.wav"')
    path.write_text(text, encoding="utf-8")
    from renderplan import CodecError

    with pytest.raises(CodecError, match="speech.wav"):
        load_plan(path)
