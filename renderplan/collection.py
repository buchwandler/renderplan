"""Portable RenderPlan-set v1 collection contract."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from . import codec as renderplan_codec
from ._toml import dumps as toml_dumps
from .errors import CodecError, RenderPlanError, ValidationError
from .model import RenderProfile
from .paths import (
    validate_contained_relative_path,
    validate_posix_relative_reference,
    validate_safe_basename,
)

RENDERPLAN_SET_FORMAT = "renderplan-set"
RENDERPLAN_SET_VERSION = 1
_PROFILE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{12}$")


class RenderPlanSetError(RenderPlanError):
    """Invalid, unreadable, or unwritable RenderPlan-set metadata."""

    def __init__(self, message: str, *, code: str = "index.invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RenderPlanSetChapter:
    """One chapter's immutable references and identities in a RenderPlan set."""

    id: str
    source_number: int
    source: str
    utterplan: str
    source_plan_id: str
    renderplan: str
    render_plan_id: str


@dataclass(frozen=True, slots=True)
class RenderPlanSet:
    """Workspace index for one immutable RenderProfile identity."""

    variant: str
    profile_fingerprint: str
    producer_version: str
    manifest: str
    utterplan_dir: str
    profile_id: str
    snapshot: str
    source_profile: str
    chapters: tuple[RenderPlanSetChapter, ...] = ()
    format: str = RENDERPLAN_SET_FORMAT
    version: int = RENDERPLAN_SET_VERSION

    def __post_init__(self) -> None:
        validate_renderplan_set(self)


def validate_renderplan_set(value: RenderPlanSet) -> None:
    """Reject malformed set values before they are read from or written to disk."""
    if value.format != RENDERPLAN_SET_FORMAT:
        raise RenderPlanSetError(f"format must be {RENDERPLAN_SET_FORMAT!r}")
    if type(value.version) is not int or value.version != RENDERPLAN_SET_VERSION:
        raise RenderPlanSetError(f"version must be {RENDERPLAN_SET_VERSION}")

    _validate_safe_basename(value.variant, "variant")
    for field in ("producer_version", "manifest", "utterplan_dir", "source_profile"):
        _require_nonempty(getattr(value, field), field)
    for field in ("manifest", "utterplan_dir", "source_profile"):
        _validate_posix_reference(getattr(value, field), field, allow_parent=True)

    if value.snapshot != "renderprofile.toml":
        raise RenderPlanSetError(
            "profile.snapshot must be the canonical 'renderprofile.toml' file",
            code="index.invalid",
        )
    if not isinstance(value.profile_id, str) or not _PROFILE_ID_RE.fullmatch(value.profile_id):
        raise RenderPlanSetError("profile.id must be a full sha256 identity")
    if not isinstance(value.profile_fingerprint, str) or not _FINGERPRINT_RE.fullmatch(
        value.profile_fingerprint
    ):
        raise RenderPlanSetError(
            "profile_fingerprint must be 12 lowercase hexadecimal characters"
        )
    if value.profile_fingerprint != value.profile_id.removeprefix("sha256:")[:12]:
        raise RenderPlanSetError(
            "profile_fingerprint does not match profile.id", code="profile.identity_mismatch"
        )
    if not isinstance(value.chapters, tuple):
        raise RenderPlanSetError("chapter entries must be a tuple")

    seen_ids: set[str] = set()
    seen_numbers: set[int] = set()
    seen_renderplan_paths: set[str] = set()
    for index, chapter in enumerate(value.chapters):
        label = f"chapter[{index}]"
        if not isinstance(chapter, RenderPlanSetChapter):
            raise RenderPlanSetError(f"{label} must be a RenderPlanSetChapter")
        _validate_safe_basename(chapter.id, f"{label}.id")
        if type(chapter.source_number) is not int or chapter.source_number <= 0:
            raise RenderPlanSetError(f"{label}.source_number must be a positive integer")
        for field in ("source", "utterplan", "renderplan"):
            _require_nonempty(getattr(chapter, field), f"{label}.{field}")
        _validate_posix_reference(chapter.source, f"{label}.source", allow_parent=True)
        _validate_posix_reference(chapter.utterplan, f"{label}.utterplan", allow_parent=True)
        _validate_posix_reference(chapter.renderplan, f"{label}.renderplan", allow_parent=False)
        for field in ("source_plan_id", "render_plan_id"):
            plan_id = getattr(chapter, field)
            if not isinstance(plan_id, str) or not _PROFILE_ID_RE.fullmatch(plan_id):
                raise RenderPlanSetError(f"{label}.{field} must be a full sha256 identity")

        if chapter.id in seen_ids:
            raise RenderPlanSetError(
                f"duplicate chapter id: {chapter.id}", code="index.chapter.duplicate"
            )
        if chapter.source_number in seen_numbers:
            raise RenderPlanSetError(
                f"duplicate chapter source_number: {chapter.source_number}",
                code="index.chapter.source_number.duplicate",
            )
        if chapter.renderplan in seen_renderplan_paths:
            raise RenderPlanSetError(
                f"duplicate renderplan path: {chapter.renderplan}",
                code="index.renderplan_path.duplicate",
            )
        seen_ids.add(chapter.id)
        seen_numbers.add(chapter.source_number)
        seen_renderplan_paths.add(chapter.renderplan)


def renderplan_set_to_dict(value: RenderPlanSet) -> dict[str, Any]:
    """Return the canonical plain-data representation for renderplan-set.toml."""
    validate_renderplan_set(value)
    return {
        "format": RENDERPLAN_SET_FORMAT,
        "version": RENDERPLAN_SET_VERSION,
        "variant": value.variant,
        "profile_fingerprint": value.profile_fingerprint,
        "producer_version": value.producer_version,
        "manifest": value.manifest,
        "utterplan_dir": value.utterplan_dir,
        "profile": {
            "id": value.profile_id,
            "snapshot": value.snapshot,
            "source": value.source_profile,
        },
        "chapter": [
            {
                "id": chapter.id,
                "source_number": chapter.source_number,
                "source": chapter.source,
                "utterplan": chapter.utterplan,
                "source_plan_id": chapter.source_plan_id,
                "renderplan": chapter.renderplan,
                "render_plan_id": chapter.render_plan_id,
            }
            for chapter in value.chapters
        ],
    }


def renderplan_set_to_toml(value: RenderPlanSet) -> str:
    """Serialize a validated set as deterministic UTF-8 TOML text."""
    return toml_dumps(renderplan_set_to_dict(value))


def renderplan_set_from_dict(raw: Mapping[str, Any]) -> RenderPlanSet:
    """Parse plain TOML data and strictly validate every supported set field."""
    root_fields = {
        "format",
        "version",
        "variant",
        "profile_fingerprint",
        "producer_version",
        "manifest",
        "utterplan_dir",
        "profile",
        "chapter",
    }
    _check_keys(raw, root_fields, "renderplan-set")
    if raw.get("format") != RENDERPLAN_SET_FORMAT:
        raise RenderPlanSetError(f"format must be {RENDERPLAN_SET_FORMAT!r}")
    if type(raw.get("version")) is not int or raw["version"] != RENDERPLAN_SET_VERSION:
        raise RenderPlanSetError(f"version must be {RENDERPLAN_SET_VERSION}")

    profile = _table(raw["profile"], "profile")
    _check_keys(profile, {"id", "snapshot", "source"}, "profile")
    chapter_data = raw["chapter"]
    if not isinstance(chapter_data, list):
        raise RenderPlanSetError("chapter must be an array of tables")

    chapters: list[RenderPlanSetChapter] = []
    chapter_fields = {
        "id",
        "source_number",
        "source",
        "utterplan",
        "source_plan_id",
        "renderplan",
        "render_plan_id",
    }
    for index, item in enumerate(chapter_data):
        label = f"chapter[{index}]"
        table = _table(item, label)
        _check_keys(table, chapter_fields, label)
        source_number = table["source_number"]
        if type(source_number) is not int:
            raise RenderPlanSetError(f"{label}.source_number must be an integer")
        chapters.append(
            RenderPlanSetChapter(
                id=_string(table, "id", label),
                source_number=source_number,
                source=_string(table, "source", label),
                utterplan=_string(table, "utterplan", label),
                source_plan_id=_string(table, "source_plan_id", label),
                renderplan=_string(table, "renderplan", label),
                render_plan_id=_string(table, "render_plan_id", label),
            )
        )

    return RenderPlanSet(
        variant=_string(raw, "variant", "renderplan-set"),
        profile_fingerprint=_string(raw, "profile_fingerprint", "renderplan-set"),
        producer_version=_string(raw, "producer_version", "renderplan-set"),
        manifest=_string(raw, "manifest", "renderplan-set"),
        utterplan_dir=_string(raw, "utterplan_dir", "renderplan-set"),
        profile_id=_string(profile, "id", "profile"),
        snapshot=_string(profile, "snapshot", "profile"),
        source_profile=_string(profile, "source", "profile"),
        chapters=tuple(chapters),
    )


def load_renderplan_set(path: str | Path) -> RenderPlanSet:
    """Read a set index; malformed documents fail closed instead of being replaced."""
    source = Path(path)
    try:
        raw = renderplan_codec._read(source)
    except CodecError as exc:
        raise RenderPlanSetError(
            f"cannot read renderplan set {source}: {exc}", code="index.read_failed"
        ) from exc
    return renderplan_set_from_dict(raw)


def save_renderplan_set(path: str | Path, value: RenderPlanSet) -> Path:
    """Atomically write a validated set index via a sibling temporary file."""
    destination = Path(path)
    text = renderplan_set_to_toml(value)
    temporary: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except OSError as exc:
        raise RenderPlanSetError(
            f"cannot write renderplan set {destination}: {exc}", code="index.write_failed"
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def validate_renderplan_set_profile(
    value: RenderPlanSet,
    snapshot_path: str | Path,
    *,
    expected_profile_id: str | None = None,
) -> RenderProfile:
    """Verify a canonical snapshot against the set and optional active profile identity."""
    validate_renderplan_set(value)
    if expected_profile_id is not None:
        if not _PROFILE_ID_RE.fullmatch(expected_profile_id):
            raise RenderPlanSetError(
                "expected_profile_id must be a full sha256 identity",
                code="profile.identity_mismatch",
            )
        if value.profile_id != expected_profile_id:
            raise RenderPlanSetError(
                "existing renderplan set profile_id does not match the active RenderProfile",
                code="profile.identity_mismatch",
            )

    snapshot = Path(snapshot_path)
    if not snapshot.is_file():
        raise RenderPlanSetError(
            f"RenderProfile snapshot is missing: {snapshot}", code="profile.snapshot_missing"
        )
    try:
        profile = renderplan_codec.load_profile(snapshot)
    except (CodecError, OSError, ValueError, TypeError) as exc:
        raise RenderPlanSetError(
            f"cannot load RenderProfile snapshot {snapshot}: {exc}",
            code="profile.snapshot_invalid",
        ) from exc
    snapshot_id = profile.profile_id or profile.computed_id()
    if snapshot_id != value.profile_id or (
        expected_profile_id is not None and snapshot_id != expected_profile_id
    ):
        raise RenderPlanSetError(
            "RenderProfile snapshot identity does not match the set and active profile",
            code="profile.identity_mismatch",
        )
    return profile


def _check_keys(raw: Mapping[str, Any], allowed: set[str], label: str) -> None:
    if any(not isinstance(key, str) for key in raw):
        raise RenderPlanSetError(
            f"{label} contains non-string field names", code="index.field.unknown"
        )
    unknown = set(raw).difference(allowed)
    missing = allowed.difference(raw)
    if unknown:
        fields = ", ".join(sorted(unknown))
        raise RenderPlanSetError(
            f"{label} contains unknown field(s): {fields}", code="index.field.unknown"
        )
    if missing:
        fields = ", ".join(sorted(missing))
        raise RenderPlanSetError(
            f"{label} is missing required field(s): {fields}", code="index.field.required"
        )


def _table(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RenderPlanSetError(f"{label} must be a table")
    return cast(Mapping[str, Any], value)


def _string(raw: Mapping[str, Any], key: str, label: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise RenderPlanSetError(f"{label}.{key} must be a non-empty string")
    return value


def _require_nonempty(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise RenderPlanSetError(f"{label} must be a non-empty string")


def _validate_safe_basename(value: str, label: str) -> None:
    try:
        validate_safe_basename(value, field=label)
    except ValidationError as exc:
        raise RenderPlanSetError(str(exc)) from exc


def _validate_posix_reference(value: str, label: str, *, allow_parent: bool) -> None:
    try:
        if allow_parent:
            validate_posix_relative_reference(value, allow_parent=True, field=label)
        else:
            validate_contained_relative_path(value, field=label)
    except ValidationError as exc:
        raise RenderPlanSetError(str(exc)) from exc


__all__ = [
    "RENDERPLAN_SET_FORMAT",
    "RENDERPLAN_SET_VERSION",
    "RenderPlanSet",
    "RenderPlanSetChapter",
    "RenderPlanSetError",
    "load_renderplan_set",
    "renderplan_set_from_dict",
    "renderplan_set_to_dict",
    "renderplan_set_to_toml",
    "save_renderplan_set",
    "validate_renderplan_set",
    "validate_renderplan_set_profile",
]
