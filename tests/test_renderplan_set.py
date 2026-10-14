from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from renderplan import (
    PausePolicy,
    PlanningPolicy,
    RenderPlanError,
    RenderPlanSet,
    RenderPlanSetChapter,
    RenderPlanSetError,
    RenderProfile,
    RoleProfile,
    load_renderplan_set,
    renderplan_set_from_dict,
    renderplan_set_to_dict,
    save_profile,
    save_renderplan_set,
    validate_renderplan_set_profile,
)


def _profile(binding: str = "fixture") -> RenderProfile:
    return RenderProfile(
        roles={"narrator": RoleProfile(language="en-US", binding={"engine": binding})},
        default_role="narrator",
        planning=PlanningPolicy(split="sentence"),
        pauses=PausePolicy(mode="manual"),
    ).with_identity()


def _chapter(
    *,
    chapter_id: str = "chapter-0005",
    source_number: int = 5,
    renderplan: str = "chapter-0005.renderplan.toml",
) -> RenderPlanSetChapter:
    return RenderPlanSetChapter(
        id=chapter_id,
        source_number=source_number,
        source="../../chapters/chapter-0005.ssmd.md",
        utterplan="../../utterplan/chapter-0005.utterplan.toml",
        source_plan_id="sha256:" + "1" * 64,
        renderplan=renderplan,
        render_plan_id="sha256:" + "2" * 64,
    )


def _set(profile: RenderProfile | None = None) -> RenderPlanSet:
    selected = profile or _profile()
    profile_id = selected.profile_id or selected.computed_id()
    return RenderPlanSet(
        variant="narration",
        profile_fingerprint=profile_id.removeprefix("sha256:")[:12],
        producer_version="1.2.3",
        manifest="../../manifest.json",
        utterplan_dir="../../utterplan",
        profile_id=profile_id,
        snapshot="renderprofile.toml",
        source_profile="../../narration.renderprofile.toml",
        chapters=(_chapter(),),
    )


def test_renderplan_set_v1_roundtrip_and_profile_snapshot(tmp_path: Path) -> None:
    value = _set()
    set_path = tmp_path / "variant" / "renderplan-set.toml"
    profile_path = set_path.parent / "renderprofile.toml"
    save_renderplan_set(set_path, value)
    profile = _profile()
    save_profile(profile, profile_path)

    loaded = load_renderplan_set(set_path)
    profile_id = profile.profile_id or profile.computed_id()
    assert loaded == value
    assert loaded.chapters == (_chapter(),)
    assert (
        validate_renderplan_set_profile(loaded, profile_path, expected_profile_id=profile_id)
        == profile
    )
    raw = set_path.read_text(encoding="utf-8")
    assert 'format = "renderplan-set"' in raw
    assert "version = 1" in raw


def test_renderplan_set_rejects_unknown_and_missing_fields() -> None:
    raw = renderplan_set_to_dict(_set())
    raw["unexpected"] = True
    with pytest.raises(RenderPlanSetError, match="unknown field"):
        renderplan_set_from_dict(raw)

    del raw["unexpected"]
    del raw["profile"]
    with pytest.raises(RenderPlanSetError, match="missing required field"):
        renderplan_set_from_dict(raw)


def test_renderplan_set_rejects_profile_fingerprint_and_duplicate_entries() -> None:
    original = _set()
    with pytest.raises(RenderPlanSetError, match="does not match"):
        replace(original, profile_fingerprint="f" * 12)
    with pytest.raises(RenderPlanSetError, match="duplicate chapter id"):
        replace(original, chapters=(original.chapters[0], _chapter(source_number=6)))
    with pytest.raises(RenderPlanSetError, match="duplicate renderplan path"):
        replace(
            original,
            chapters=(
                original.chapters[0],
                _chapter(chapter_id="chapter-0006", source_number=6),
            ),
        )


@pytest.mark.parametrize(
    "unsafe_path", ["../manifest.json", "/tmp/plan.toml", "a/../b.toml", "a\\b.toml"]
)
def test_renderplan_set_rejects_unsafe_renderplan_paths(unsafe_path: str) -> None:
    original = _set()
    with pytest.raises(RenderPlanSetError, match="normalized relative path"):
        replace(original, chapters=(replace(original.chapters[0], renderplan=unsafe_path),))


def test_renderplan_set_requires_canonical_profile_snapshot() -> None:
    with pytest.raises(RenderPlanSetError, match="canonical"):
        replace(_set(), snapshot="../outside.renderprofile.toml")


def test_renderplan_set_error_is_a_renderplan_error() -> None:
    with pytest.raises(RenderPlanError):
        replace(_set(), profile_fingerprint="bad")

    assert issubclass(RenderPlanSetError, ValueError)

def test_renderplan_set_profile_validation_distinguishes_identity_failures(
    tmp_path: Path,
) -> None:
    profile = _profile()
    profile_id = profile.profile_id or profile.computed_id()
    snapshot = tmp_path / "renderprofile.toml"
    save_profile(profile, snapshot)
    value = _set(profile)

    with pytest.raises(RenderPlanSetError, match="does not match the active"):
        validate_renderplan_set_profile(
            value, snapshot, expected_profile_id="sha256:" + "f" * 64
        )
    other_profile = _profile("other")
    other_id = other_profile.profile_id or other_profile.computed_id()
    with pytest.raises(RenderPlanSetError, match="snapshot identity"):
        validate_renderplan_set_profile(
            _set(other_profile), snapshot, expected_profile_id=other_id
        )
    with pytest.raises(RenderPlanSetError) as missing:
        validate_renderplan_set_profile(
            value, tmp_path / "missing.toml", expected_profile_id=profile_id
        )
    assert missing.value.code == "profile.snapshot_missing"


def test_renderplan_set_loader_fails_closed_on_malformed_toml(tmp_path: Path) -> None:
    path = tmp_path / "renderplan-set.toml"
    path.write_text("format = [", encoding="utf-8")
    with pytest.raises(RenderPlanSetError, match="cannot read renderplan set") as error:
        load_renderplan_set(path)
    assert error.value.code == "index.read_failed"
