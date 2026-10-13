from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from renderplan import (
    PausePolicy,
    PlanningPolicy,
    RenderPlanSet,
    RenderPlanSetChapter,
    RenderPlanSetError,
    RenderProfile,
    RoleProfile,
    collection,
    load_any,
    load_renderplan_set,
    renderplan_set_from_dict,
    renderplan_set_to_dict,
    renderplan_set_to_toml,
    save_profile,
    save_renderplan_set,
    validate_renderplan_set_profile,
)

SOURCE_PLAN_ID = "sha256:" + "b" * 64
RENDER_PLAN_ID = "sha256:" + "c" * 64


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
    source: str = "../../chapters/chapter-0005.ssmd.md",
    utterplan: str = "../../utterplan/chapter-0005.utterplan.toml",
    renderplan: str = "chapter-0005.renderplan.toml",
) -> RenderPlanSetChapter:
    return RenderPlanSetChapter(
        id=chapter_id,
        source_number=source_number,
        source=source,
        utterplan=utterplan,
        source_plan_id=SOURCE_PLAN_ID,
        renderplan=renderplan,
        render_plan_id=RENDER_PLAN_ID,
    )


def _set(
    *,
    profile: RenderProfile | None = None,
    chapters: tuple[RenderPlanSetChapter, ...] | None = None,
    variant: str = "narration",
) -> RenderPlanSet:
    selected_profile = profile or _profile()
    profile_id = selected_profile.profile_id or selected_profile.computed_id()
    return RenderPlanSet(
        variant=variant,
        profile_fingerprint=profile_id.removeprefix("sha256:")[:12],
        producer_version="0.5.1",
        manifest="../../manifest.json",
        utterplan_dir="../../utterplan",
        profile_id=profile_id,
        snapshot="renderprofile.toml",
        source_profile="../../narration.renderprofile.toml",
        chapters=chapters if chapters is not None else (_chapter(),),
    )


def _raw(value: RenderPlanSet | None = None) -> dict[str, object]:
    return renderplan_set_to_dict(value or _set())


def test_collection_model_construction_and_canonical_fields() -> None:
    value = _set()
    assert value.format == "renderplan-set"
    assert value.version == 1
    assert value.chapters == (_chapter(),)
    raw = _raw(value)
    assert raw["format"] == "renderplan-set"
    assert raw["profile"] == {
        "id": value.profile_id,
        "snapshot": "renderprofile.toml",
        "source": "../../narration.renderprofile.toml",
    }
    assert "producer_version" in raw
    assert "producer" not in raw and "book" not in raw


def test_collection_toml_roundtrip_and_chapter_order() -> None:
    later = _chapter(
        chapter_id="chapter-0008",
        source_number=8,
        renderplan="chapter-0008.renderplan.toml",
    )
    earlier = _chapter(
        chapter_id="chapter-0002",
        source_number=2,
        renderplan="chapter-0002.renderplan.toml",
    )
    value = _set(chapters=(later, earlier))
    text = renderplan_set_to_toml(value)
    assert text == renderplan_set_to_toml(value)
    assert text.count("[[chapter]]") == 2
    assert text.index('id = "chapter-0008"') < text.index('id = "chapter-0002"')
    assert renderplan_set_from_dict(_raw(value)) == value


def test_collection_atomic_save_load_creates_parent_and_cleans_temporary_files(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "new" / "nested" / "renderplan-set.toml"
    value = _set()
    assert save_renderplan_set(destination, value) == destination
    assert load_renderplan_set(destination) == value
    assert list(destination.parent.glob("*.tmp")) == []
    assert list(destination.parent.glob(".*.tmp")) == []


def test_collection_save_replace_failure_preserves_existing_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "renderplan-set.toml"
    destination.write_text("preserve me", encoding="utf-8")

    def fail_replace(source: str | Path, target: str | Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(collection.os, "replace", fail_replace)
    with pytest.raises(RenderPlanSetError) as error:
        save_renderplan_set(destination, _set())
    assert error.value.code == "index.write_failed"
    assert destination.read_text(encoding="utf-8") == "preserve me"
    assert list(tmp_path.iterdir()) == [destination]


def test_collection_profile_fingerprint_mismatch_has_stable_code() -> None:
    with pytest.raises(RenderPlanSetError) as error:
        replace(_set(), profile_fingerprint="f" * 12)
    assert error.value.code == "profile.identity_mismatch"


@pytest.mark.parametrize(
    "field,value",
    [
        ("profile_id", "sha256:xyz"),
        ("profile_id", "sha256:" + "A" * 64),
        ("profile_id", "sha256:" + "a" * 63),
    ],
)
def test_collection_rejects_malformed_profile_ids(field: str, value: str) -> None:
    with pytest.raises(RenderPlanSetError):
        replace(_set(), **{field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_plan_id", "sha256:not-a-hash"),
        ("render_plan_id", "sha256:" + "0" * 63),
    ],
)
def test_collection_rejects_malformed_chapter_sha256_ids(field: str, value: str) -> None:
    with pytest.raises(RenderPlanSetError):
        replace(_set(), chapters=(replace(_chapter(), **{field: value}),))


def test_collection_rejects_unknown_root_field() -> None:
    raw = _raw()
    raw["unexpected"] = True
    with pytest.raises(RenderPlanSetError) as error:
        renderplan_set_from_dict(raw)
    assert error.value.code == "index.field.unknown"


def test_collection_requires_profile_table() -> None:
    raw = _raw()
    del raw["profile"]
    with pytest.raises(RenderPlanSetError) as error:
        renderplan_set_from_dict(raw)
    assert error.value.code == "index.field.required"


def test_collection_rejects_unknown_profile_field() -> None:
    raw = _raw()
    profile = raw["profile"]
    assert isinstance(profile, dict)
    profile["unexpected"] = True
    with pytest.raises(RenderPlanSetError) as error:
        renderplan_set_from_dict(raw)
    assert error.value.code == "index.field.unknown"


def test_collection_requires_all_profile_fields() -> None:
    raw = _raw()
    profile = raw["profile"]
    assert isinstance(profile, dict)
    del profile["source"]
    with pytest.raises(RenderPlanSetError) as error:
        renderplan_set_from_dict(raw)
    assert error.value.code == "index.field.required"


def test_collection_rejects_unknown_chapter_field() -> None:
    raw = _raw()
    chapters = raw["chapter"]
    assert isinstance(chapters, list)
    chapters[0]["unexpected"] = True
    with pytest.raises(RenderPlanSetError) as error:
        renderplan_set_from_dict(raw)
    assert error.value.code == "index.field.unknown"


def test_collection_rejects_duplicate_chapter_ids() -> None:
    with pytest.raises(RenderPlanSetError) as error:
        _set(chapters=(_chapter(), _chapter(source_number=6)))
    assert error.value.code == "index.chapter.duplicate"


def test_collection_rejects_duplicate_source_numbers() -> None:
    duplicate_number = _chapter(chapter_id="chapter-0006")
    with pytest.raises(RenderPlanSetError) as error:
        _set(chapters=(_chapter(), duplicate_number))
    assert error.value.code == "index.chapter.source_number.duplicate"


def test_collection_rejects_duplicate_renderplan_paths() -> None:
    duplicate_path = _chapter(chapter_id="chapter-0006", source_number=6)
    with pytest.raises(RenderPlanSetError) as error:
        _set(chapters=(_chapter(), duplicate_path))
    assert error.value.code == "index.renderplan_path.duplicate"


@pytest.mark.parametrize("variant", ["", ".", "..", "a/b", "a\\b", "C:variant", "bad\x00id"])
def test_collection_rejects_unsafe_variant(variant: str) -> None:
    with pytest.raises(RenderPlanSetError):
        _set(variant=variant)


@pytest.mark.parametrize(
    "chapter_id", ["", ".", "..", "../escape", "a/b", "a\\b", "C:escape", "x\x00y"]
)
def test_collection_rejects_unsafe_chapter_id(chapter_id: str) -> None:
    with pytest.raises(RenderPlanSetError):
        _set(chapters=(_chapter(chapter_id=chapter_id),))


@pytest.mark.parametrize(
    "renderplan",
    [
        "../x",
        "/tmp/plan.toml",
        "a/../b.toml",
        "a/./b.toml",
        "a\\b.toml",
        "C:/plan.toml",
        "//host/share/plan.toml",
        "a\x00b.toml",
        "a//b.toml",
        "./plan.toml",
    ],
)
def test_collection_rejects_unsafe_renderplan_paths(renderplan: str) -> None:
    with pytest.raises(RenderPlanSetError, match="normalized relative path"):
        _set(chapters=(_chapter(renderplan=renderplan),))


@pytest.mark.parametrize("field", ["source", "utterplan"])
def test_collection_accepts_normalized_upstream_parent_references(field: str) -> None:
    chapter = _chapter(**{field: "../../workspace/nested/file.toml"})
    assert _set(chapters=(chapter,)).chapters == (chapter,)


@pytest.mark.parametrize(
    "reference",
    ["", "/absolute/file", "C:/file", "a\\b", "a/./b", "a//b", "bad\x00file"],
)
def test_collection_rejects_non_normalized_upstream_references(reference: str) -> None:
    for field in ("source", "utterplan"):
        with pytest.raises(RenderPlanSetError):
            _set(chapters=(_chapter(**{field: reference}),))


def test_collection_profile_snapshot_identity_succeeds_without_expected_id(
    tmp_path: Path,
) -> None:
    profile = _profile()
    path = tmp_path / "renderprofile.toml"
    save_profile(profile, path)
    value = _set(profile=profile)
    assert validate_renderplan_set_profile(value, path) == profile


def test_collection_profile_snapshot_identity_mismatch_fails(tmp_path: Path) -> None:
    profile = _profile()
    other_profile = _profile("different-engine")
    path = tmp_path / "renderprofile.toml"
    save_profile(profile, path)
    with pytest.raises(RenderPlanSetError) as error:
        validate_renderplan_set_profile(_set(profile=other_profile), path)
    assert error.value.code == "profile.identity_mismatch"


def test_collection_active_expected_profile_identity_is_checked(tmp_path: Path) -> None:
    profile = _profile()
    profile_id = profile.profile_id or profile.computed_id()
    path = tmp_path / "renderprofile.toml"
    save_profile(profile, path)
    value = _set(profile=profile)
    assert (
        validate_renderplan_set_profile(value, path, expected_profile_id=profile_id) == profile
    )
    with pytest.raises(RenderPlanSetError) as error:
        validate_renderplan_set_profile(value, path, expected_profile_id="sha256:" + "f" * 64)
    assert error.value.code == "profile.identity_mismatch"


def test_collection_loader_rejects_malformed_toml(tmp_path: Path) -> None:
    path = tmp_path / "renderplan-set.toml"
    path.write_text("format = [", encoding="utf-8")
    with pytest.raises(RenderPlanSetError) as error:
        load_renderplan_set(path)
    assert error.value.code == "index.read_failed"


def test_collection_load_any_dispatches_canonical_set(tmp_path: Path) -> None:
    value = _set()
    path = tmp_path / "renderplan-set.toml"
    save_renderplan_set(path, value)
    assert load_any(path) == value


def test_collection_loader_wraps_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "renderplan-set.toml"
    path.write_bytes(b"\xff")
    with pytest.raises(RenderPlanSetError) as error:
        load_renderplan_set(path)
    assert error.value.code == "index.read_failed"
