"""Portable RenderPlan-set v1 index contract.

The set is workspace metadata produced by UtterPlan and consumed by downstream
RenderPlan tools. Its existing on-disk format name is retained for compatibility.
"""

from __future__ import annotations

import importlib
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, cast

from . import codec as renderplan_codec
from ._toml import dumps as toml_dumps
from .errors import RenderPlanError
from .model import RenderProfile

try:
    tomllib: Any = importlib.import_module("tomllib")
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    tomllib = importlib.import_module("tomli")

RENDERPLAN_SET_FORMAT = "utterplan-renderplan-set"
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
    """One successfully written chapter projection in a RenderPlan set."""

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

    def __post_init__(self) -> None:
        validate_renderplan_set(self)


def validate_renderplan_set(value: RenderPlanSet) -> None:
    """Reject malformed set values before they are read from or written to disk."""
    for field in ("variant", "producer_version", "manifest", "utterplan_dir", "snapshot", "source_profile"):
        _require_nonempty(getattr(value, field), field)
    if value.snapshot != "renderprofile.toml":
        raise RenderPlanSetError("profile.snapshot must be the canonical 'renderprofile.toml' file")
    if not _PROFILE_ID_RE.fullmatch(value.profile_id):
        raise RenderPlanSetError("profile.profile_id must be a full sha256 identity")
    if not _FINGERPRINT_RE.fullmatch(value.profile_fingerprint):
        raise RenderPlanSetError("profile_fingerprint must be 12 lowercase hexadecimal characters")
    if value.profile_fingerprint != value.profile_id.removeprefix("sha256:")[:12]:
        raise RenderPlanSetError("profile_fingerprint does not match profile.profile_id")
    if not isinstance(value.chapters, tuple):
        raise RenderPlanSetError("chapter entries must be a tuple")

    seen_ids: set[str] = set()
    seen_numbers: set[int] = set()
    seen_renderplan_paths: set[str] = set()
    for index, chapter in enumerate(value.chapters):
        label = f"chapter[{index}]"
        if not isinstance(chapter, RenderPlanSetChapter):
            raise RenderPlanSetError(f"{label} must be a RenderPlanSetChapter")
        _require_nonempty(chapter.id, f"{label}.id")
        if type(chapter.source_number) is not int or chapter.source_number <= 0:
            raise RenderPlanSetError(f"{label}.source_number must be a positive integer")
        for key in ("source", "utterplan", "renderplan"):
            _require_nonempty(getattr(chapter, key), f"{label}.{key}")
        renderplan_path = PurePosixPath(chapter.renderplan)
        if (
            "\x00" in chapter.renderplan
            or "\\" in chapter.renderplan
            or renderplan_path.is_absolute()
            or PureWindowsPath(chapter.renderplan).drive
            or not renderplan_path.parts
            or any(part in {".", ".."} for part in renderplan_path.parts)
            or renderplan_path.as_posix() != chapter.renderplan
        ):
            raise RenderPlanSetError(
                f"{label}.renderplan must be a normalized relative path inside the variant"
            )
        for key in ("source_plan_id", "render_plan_id"):
            plan_id = getattr(chapter, key)
            if not isinstance(plan_id, str) or not _PROFILE_ID_RE.fullmatch(plan_id):
                raise RenderPlanSetError(f"{label}.{key} must be a full sha256 identity")
        if chapter.id in seen_ids:
            raise RenderPlanSetError(f"duplicate chapter id: {chapter.id}")
        if chapter.source_number in seen_numbers:
            raise RenderPlanSetError(f"duplicate chapter source_number: {chapter.source_number}")
        if chapter.renderplan in seen_renderplan_paths:
            raise RenderPlanSetError(f"duplicate renderplan path: {chapter.renderplan}")
        seen_ids.add(chapter.id)
        seen_numbers.add(chapter.source_number)
        seen_renderplan_paths.add(chapter.renderplan)


def renderplan_set_to_dict(value: RenderPlanSet) -> dict[str, Any]:
    """Return the stable plain-data representation for renderplan-set.toml."""
    validate_renderplan_set(value)
    return {
        "format": RENDERPLAN_SET_FORMAT,
        "version": RENDERPLAN_SET_VERSION,
        "variant": value.variant,
        "profile_fingerprint": value.profile_fingerprint,
        "producer": {"name": "utterplan", "version": value.producer_version},
        "book": {"manifest": value.manifest, "utterplan_dir": value.utterplan_dir},
        "profile": {
            "profile_id": value.profile_id,
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
    """Serialize a validated set as TOML workspace metadata."""
    return toml_dumps(renderplan_set_to_dict(value))


def renderplan_set_from_dict(raw: Mapping[str, Any]) -> RenderPlanSet:
    """Parse plain TOML data and strictly validate every supported set field."""
    _check_keys(
        raw,
        {"format", "version", "variant", "profile_fingerprint", "producer", "book", "profile", "chapter"},
        "renderplan-set",
    )
    if raw.get("format") != RENDERPLAN_SET_FORMAT:
        raise RenderPlanSetError(f"format must be {RENDERPLAN_SET_FORMAT!r}")
    if type(raw.get("version")) is not int or raw["version"] != RENDERPLAN_SET_VERSION:
        raise RenderPlanSetError(f"version must be {RENDERPLAN_SET_VERSION}")

    producer = _table(raw.get("producer"), "producer")
    _check_keys(producer, {"name", "version"}, "producer")
    if producer.get("name") != "utterplan":
        raise RenderPlanSetError("producer.name must be 'utterplan'")
    book = _table(raw.get("book"), "book")
    _check_keys(book, {"manifest", "utterplan_dir"}, "book")
    profile = _table(raw.get("profile"), "profile")
    _check_keys(profile, {"profile_id", "snapshot", "source"}, "profile")

    chapter_data = raw.get("chapter")
    if not isinstance(chapter_data, list):
        raise RenderPlanSetError("chapter must be an array of tables")
    chapters: list[RenderPlanSetChapter] = []
    chapter_keys = {
        "id", "source_number", "source", "utterplan", "source_plan_id", "renderplan", "render_plan_id"
    }
    for index, item in enumerate(chapter_data):
        item_table = _table(item, f"chapter[{index}]")
        _check_keys(item_table, chapter_keys, f"chapter[{index}]")
        source_number = item_table.get("source_number")
        if type(source_number) is not int:
            raise RenderPlanSetError(f"chapter[{index}].source_number must be an integer")
        chapters.append(
            RenderPlanSetChapter(
                id=_string(item_table, "id", f"chapter[{index}]"),
                source_number=source_number,
                source=_string(item_table, "source", f"chapter[{index}]"),
                utterplan=_string(item_table, "utterplan", f"chapter[{index}]"),
                source_plan_id=_string(item_table, "source_plan_id", f"chapter[{index}]"),
                renderplan=_string(item_table, "renderplan", f"chapter[{index}]"),
                render_plan_id=_string(item_table, "render_plan_id", f"chapter[{index}]"),
            )
        )
    return RenderPlanSet(
        variant=_string(raw, "variant", "renderplan-set"),
        profile_fingerprint=_string(raw, "profile_fingerprint", "renderplan-set"),
        producer_version=_string(producer, "version", "producer"),
        manifest=_string(book, "manifest", "book"),
        utterplan_dir=_string(book, "utterplan_dir", "book"),
        profile_id=_string(profile, "profile_id", "profile"),
        snapshot=_string(profile, "snapshot", "profile"),
        source_profile=_string(profile, "source", "profile"),
        chapters=tuple(chapters),
    )


def load_renderplan_set(path: str | Path) -> RenderPlanSet:
    """Read a set index; malformed documents fail closed instead of being replaced."""
    source = Path(path)
    try:
        with source.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise RenderPlanSetError(
            f"cannot read renderplan set {source}: {exc}", code="index.read_failed"
        ) from exc
    if not isinstance(raw, Mapping):
        raise RenderPlanSetError("renderplan-set root must be a table")
    return renderplan_set_from_dict(cast(Mapping[str, Any], raw))


def save_renderplan_set(path: str | Path, value: RenderPlanSet) -> Path:
    """Atomically write a validated set index."""
    destination = Path(path)
    text = renderplan_set_to_toml(value)
    temporary: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
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
    expected_profile_id: str,
) -> RenderProfile:
    """Verify the set and canonical snapshot against the active RenderProfile identity."""
    validate_renderplan_set(value)
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
    except (OSError, ValueError, TypeError) as exc:
        raise RenderPlanSetError(
            f"cannot load RenderProfile snapshot {snapshot}: {exc}",
            code="profile.snapshot_invalid",
        ) from exc
    snapshot_id = profile.profile_id or profile.computed_id()
    if snapshot_id != value.profile_id or snapshot_id != expected_profile_id:
        raise RenderPlanSetError(
            "RenderProfile snapshot identity does not match the set and active profile",
            code="profile.snapshot_mismatch",
        )
    return profile


def _check_keys(raw: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(raw) - allowed
    missing = allowed - set(raw)
    if unknown:
        raise RenderPlanSetError(f"{label} contains unknown field(s): {', '.join(sorted(unknown))}")
    if missing:
        raise RenderPlanSetError(
            f"{label} is missing required field(s): {', '.join(sorted(missing))}"
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
