from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal, Mapping

from .errors import ValidationError
from .identity import require_nonempty, require_sha256_id, require_toml_value, sha256_id

PROFILE_FORMAT = "renderprofile"
PLAN_FORMAT = "renderplan"
PREFLIGHT_FORMAT = "renderplan-preflight"
MANIFEST_FORMAT = "render-manifest"
SCHEMA_VERSION = 1

SplitLevel = Literal["paragraph", "sentence", "clause"]
FallbackLevel = Literal["clause", "token"]
Guarantee = Literal["hard", "advisory"]
PreflightStatus = Literal["ok", "replan", "reject"]
CheckStatus = Literal["ok", "too_long", "error"]

_ALLOWED_TOKEN_FIELDS = frozenset({"span", "pos", "tag", "lemma", "morph"})
_ALLOWED_SPLITS = frozenset({"paragraph", "sentence", "clause"})
_ALLOWED_FALLBACKS = frozenset({"clause", "token"})
_ALLOWED_SEMANTIC_ENDS = frozenset({"none", "clause", "sentence", "paragraph", "voice_change"})
_ALLOWED_SPLIT_REASONS = frozenset({"policy", "capacity", "voice_change", "directive_change", "media"})
_ALLOWED_PLANNING_LEVELS = frozenset({"paragraph", "sentence", "clause", "token", "hard"})


def _tuple_strings(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    seen: set[str] = set()
    for item in values:
        require_nonempty(item, field_name)
        if item in seen:
            raise ValidationError(f"{field_name} contains duplicate {item!r}")
        seen.add(item)
    return values


@dataclass(frozen=True, slots=True)
class Producer:
    name: str
    version: str

    def __post_init__(self) -> None:
        require_nonempty(self.name, "producer.name")
        require_nonempty(self.version, "producer.version")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version}


@dataclass(frozen=True, slots=True)
class CapacityConstraint:
    metric: str
    maximum: int
    guarantee: Guarantee = "advisory"

    def __post_init__(self) -> None:
        require_nonempty(self.metric, "capacity.metric")
        if isinstance(self.maximum, bool) or not isinstance(self.maximum, int) or self.maximum <= 0:
            raise ValidationError("capacity.maximum must be a positive integer")
        if self.guarantee not in {"hard", "advisory"}:
            raise ValidationError("capacity.guarantee must be 'hard' or 'advisory'")

    def to_dict(self) -> dict[str, Any]:
        return {"metric": self.metric, "maximum": self.maximum, "guarantee": self.guarantee}


@dataclass(frozen=True, slots=True)
class RoleRequirements:
    token_fields: tuple[str, ...] = ()
    frontend: str = "renderer"
    pronunciation: str = "renderer"

    def __post_init__(self) -> None:
        _tuple_strings(self.token_fields, "requirements.token_fields")
        unknown = set(self.token_fields).difference(_ALLOWED_TOKEN_FIELDS)
        if unknown:
            raise ValidationError(f"unknown token fields: {', '.join(sorted(unknown))}")
        require_nonempty(self.frontend, "requirements.frontend")
        require_nonempty(self.pronunciation, "requirements.pronunciation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_fields": list(self.token_fields),
            "frontend": self.frontend,
            "pronunciation": self.pronunciation,
        }


@dataclass(frozen=True, slots=True)
class RoleProfile:
    language: str
    binding: Mapping[str, Any]
    requirements: RoleRequirements = field(default_factory=RoleRequirements)
    planner_capacity: CapacityConstraint | None = None
    renderer_capacity: CapacityConstraint | None = None

    def __post_init__(self) -> None:
        require_nonempty(self.language, "role.language")
        if not self.binding:
            raise ValidationError("role.binding must not be empty")
        require_toml_value(self.binding, "role.binding")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "language": self.language,
            "binding": dict(self.binding),
            "requirements": self.requirements.to_dict(),
        }
        capacity: dict[str, Any] = {}
        if self.planner_capacity is not None:
            capacity["planner"] = self.planner_capacity.to_dict()
        if self.renderer_capacity is not None:
            capacity["renderer"] = self.renderer_capacity.to_dict()
        if capacity:
            data["capacity"] = capacity
        return data


@dataclass(frozen=True, slots=True)
class PlanningPolicy:
    split: SplitLevel = "sentence"
    capacity_fallback: tuple[FallbackLevel, ...] = ("clause", "token")
    max_preflight_passes: int = 3

    def __post_init__(self) -> None:
        if self.split not in _ALLOWED_SPLITS:
            raise ValidationError(f"planning.split must be one of {sorted(_ALLOWED_SPLITS)}")
        _tuple_strings(self.capacity_fallback, "planning.capacity_fallback")
        unknown = set(self.capacity_fallback).difference(_ALLOWED_FALLBACKS)
        if unknown:
            raise ValidationError(f"unknown capacity fallback: {', '.join(sorted(unknown))}")
        if (
            isinstance(self.max_preflight_passes, bool)
            or not isinstance(self.max_preflight_passes, int)
            or self.max_preflight_passes < 0
        ):
            raise ValidationError("planning.max_preflight_passes must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "capacity_fallback": list(self.capacity_fallback),
            "max_preflight_passes": self.max_preflight_passes,
        }


@dataclass(frozen=True, slots=True)
class PausePolicy:
    mode: str = "manual"
    durations_ms: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in {"manual", "none", "native"}:
            raise ValidationError("pauses.mode must be manual, none, or native")
        for name, duration in self.durations_ms.items():
            require_nonempty(name, "pause name")
            if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
                raise ValidationError(f"pause {name!r} duration must be a non-negative integer ms")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"mode": self.mode}
        for key, value in sorted(self.durations_ms.items()):
            data[key] = f"{value}ms"
        return data


@dataclass(frozen=True, slots=True)
class RenderProfile:
    roles: Mapping[str, RoleProfile]
    default_role: str
    planning: PlanningPolicy = field(default_factory=PlanningPolicy)
    pauses: PausePolicy = field(default_factory=PausePolicy)
    profile_id: str | None = None
    producer: Producer | None = None
    format: str = PROFILE_FORMAT
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.format != PROFILE_FORMAT or self.schema_version != SCHEMA_VERSION:
            raise ValidationError("unsupported render profile format/schema")
        if not self.roles:
            raise ValidationError("render profile requires at least one role")
        for role in self.roles:
            require_nonempty(role, "role name")
        require_nonempty(self.default_role, "default_role")
        if self.default_role not in self.roles:
            raise ValidationError("default_role must name one of roles")
        if self.profile_id is not None:
            require_sha256_id(self.profile_id, "profile_id")
            if self.profile_id != self.computed_id():
                raise ValidationError("profile_id does not match profile content")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "schema_version": self.schema_version,
            "roles": {name: role.to_dict() for name, role in sorted(self.roles.items())},
            "default_role": self.default_role,
            "planning": self.planning.to_dict(),
            "pauses": self.pauses.to_dict(),
        }

    def computed_id(self) -> str:
        return sha256_id(self.identity_payload())

    def with_identity(self) -> RenderProfile:
        return replace(self, profile_id=self.computed_id())


@dataclass(frozen=True, slots=True)
class SourceSpan:
    locator: str
    start: int
    end: int

    def __post_init__(self) -> None:
        require_nonempty(self.locator, "source.locator")
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (self.start, self.end)):
            raise ValidationError("source start/end must be integers")
        if self.start < 0 or self.end <= self.start:
            raise ValidationError("source span must satisfy 0 <= start < end")

    def to_dict(self) -> dict[str, Any]:
        return {"locator": self.locator, "start": self.start, "end": self.end}


@dataclass(frozen=True, slots=True)
class TokenBundle:
    span: tuple[tuple[int, int], ...]
    pos: tuple[str, ...] = ()
    tag: tuple[str, ...] = ()
    lemma: tuple[tuple[int, str], ...] = ()
    morph: tuple[tuple[int, str], ...] = ()

    def __post_init__(self) -> None:
        last_start = -1
        for index, (start, end) in enumerate(self.span):
            if any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end)):
                raise ValidationError(f"token.span[{index}] must contain integer offsets")
            if start < 0 or end <= start or start < last_start:
                raise ValidationError(f"token.span[{index}] is invalid or out of order")
            last_start = start
        for field_name, values in (("pos", self.pos), ("tag", self.tag)):
            if values and len(values) != len(self.span):
                raise ValidationError(f"token.{field_name} length must match token.span")
        for field_name, values in (("lemma", self.lemma), ("morph", self.morph)):
            seen: set[int] = set()
            for token_index, value in values:
                if token_index < 0 or token_index >= len(self.span) or token_index in seen:
                    raise ValidationError(f"token.{field_name} has invalid token index {token_index}")
                require_nonempty(value, f"token.{field_name} value")
                seen.add(token_index)

    def validate_text(self, text: str) -> None:
        for index, (_, end) in enumerate(self.span):
            if end > len(text):
                raise ValidationError(f"token.span[{index}] exceeds speech text length")

    def fields_present(self) -> frozenset[str]:
        present = {"span"}
        if self.pos:
            present.add("pos")
        if self.tag:
            present.add("tag")
        if self.lemma:
            present.add("lemma")
        if self.morph:
            present.add("morph")
        return frozenset(present)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"span": [list(pair) for pair in self.span]}
        if self.pos:
            data["pos"] = list(self.pos)
        if self.tag:
            data["tag"] = list(self.tag)
        if self.lemma:
            data["lemma"] = [[index, value] for index, value in self.lemma]
        if self.morph:
            data["morph"] = [[index, value] for index, value in self.morph]
        return data


@dataclass(frozen=True, slots=True)
class SpeechItem:
    number: int
    text: str
    language: str
    role: str
    binding: Mapping[str, Any]
    source: tuple[SourceSpan, ...]
    split_reason: str = "policy"
    planning_level: str = "sentence"
    semantic_end: str = "none"
    tokens: TokenBundle | None = None
    preflight_required: bool = True
    segment_id: str | None = None
    kind: str = "speech"

    def __post_init__(self) -> None:
        if isinstance(self.number, bool) or not isinstance(self.number, int) or self.number <= 0:
            raise ValidationError("speech.number must be a positive integer")
        require_nonempty(self.text, "speech.text")
        require_nonempty(self.language, "speech.language")
        require_nonempty(self.role, "speech.role")
        if not self.binding:
            raise ValidationError("speech.binding must not be empty")
        require_toml_value(self.binding, "speech.binding")
        if not self.source:
            raise ValidationError("speech.source must not be empty")
        if self.split_reason not in _ALLOWED_SPLIT_REASONS:
            raise ValidationError(f"unsupported speech.split_reason {self.split_reason!r}")
        if self.planning_level not in _ALLOWED_PLANNING_LEVELS:
            raise ValidationError(f"unsupported speech.planning_level {self.planning_level!r}")
        if self.semantic_end not in _ALLOWED_SEMANTIC_ENDS:
            raise ValidationError(f"unsupported speech.semantic_end {self.semantic_end!r}")
        if self.tokens is not None:
            self.tokens.validate_text(self.text)
        if self.segment_id is not None:
            require_sha256_id(self.segment_id, "speech.segment_id")
            if self.segment_id != self.computed_id():
                raise ValidationError(f"speech {self.number} segment_id does not match content")

    @property
    def wav(self) -> str:
        return f"{self.number:06d}.wav"

    def identity_payload(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": self.kind,
            "number": self.number,
            "text": self.text,
            "language": self.language,
            "role": self.role,
            "binding": dict(self.binding),
            "source": [item.to_dict() for item in self.source],
            "split_reason": self.split_reason,
            "planning_level": self.planning_level,
            "semantic_end": self.semantic_end,
            "preflight_required": self.preflight_required,
        }
        if self.tokens is not None:
            data["token"] = self.tokens.to_dict()
        return data

    def computed_id(self) -> str:
        return sha256_id(self.identity_payload())

    def with_identity(self) -> SpeechItem:
        return replace(self, segment_id=self.computed_id())


@dataclass(frozen=True, slots=True)
class PauseItem:
    duration_ms: int
    reason: str
    after_speech: int
    pause_id: str | None = None
    kind: str = "pause"

    def __post_init__(self) -> None:
        if isinstance(self.duration_ms, bool) or not isinstance(self.duration_ms, int) or self.duration_ms < 0:
            raise ValidationError("pause.duration_ms must be a non-negative integer")
        require_nonempty(self.reason, "pause.reason")
        if isinstance(self.after_speech, bool) or not isinstance(self.after_speech, int) or self.after_speech <= 0:
            raise ValidationError("pause.after_speech must be a positive integer")
        if self.pause_id is not None:
            require_sha256_id(self.pause_id, "pause.pause_id")
            if self.pause_id != self.computed_id():
                raise ValidationError("pause_id does not match pause content")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "duration_ms": self.duration_ms,
            "reason": self.reason,
            "after_speech": self.after_speech,
        }

    def computed_id(self) -> str:
        return sha256_id(self.identity_payload())

    def with_identity(self) -> PauseItem:
        return replace(self, pause_id=self.computed_id())


@dataclass(frozen=True, slots=True)
class AudioItem:
    src: str
    source: tuple[SourceSpan, ...]
    options: Mapping[str, Any] = field(default_factory=dict)
    audio_id: str | None = None
    kind: str = "audio"

    def __post_init__(self) -> None:
        require_nonempty(self.src, "audio.src")
        if not self.source:
            raise ValidationError("audio.source must not be empty")
        require_toml_value(self.options, "audio.options")
        if self.audio_id is not None:
            require_sha256_id(self.audio_id, "audio.audio_id")
            if self.audio_id != self.computed_id():
                raise ValidationError("audio_id does not match audio content")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "src": self.src,
            "source": [item.to_dict() for item in self.source],
            "options": dict(self.options),
        }

    def computed_id(self) -> str:
        return sha256_id(self.identity_payload())

    def with_identity(self) -> AudioItem:
        return replace(self, audio_id=self.computed_id())


FlowItem = SpeechItem | PauseItem | AudioItem


@dataclass(frozen=True, slots=True)
class RenderPlan:
    source_plan_id: str
    render_profile_id: str
    flow: tuple[FlowItem, ...]
    source_plan_sha256: str | None = None
    plan_id: str | None = None
    producer: Producer | None = None
    format: str = PLAN_FORMAT
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.format != PLAN_FORMAT or self.schema_version != SCHEMA_VERSION:
            raise ValidationError("unsupported render plan format/schema")
        require_sha256_id(self.source_plan_id, "source_plan_id")
        require_sha256_id(self.render_profile_id, "render_profile_id")
        if self.source_plan_sha256 is not None:
            require_sha256_id(self.source_plan_sha256, "source_plan_sha256")
        if not self.flow:
            raise ValidationError("render plan flow must not be empty")
        expected_number = 1
        previous_speech: int | None = None
        for index, item in enumerate(self.flow):
            if isinstance(item, SpeechItem):
                if item.number != expected_number:
                    raise ValidationError(
                        f"speech numbers must be contiguous from 1; flow[{index}] is {item.number}, expected {expected_number}"
                    )
                if item.segment_id is None and self.plan_id is not None:
                    raise ValidationError(f"speech {item.number} must have segment_id")
                expected_number += 1
                previous_speech = item.number
            elif isinstance(item, PauseItem):
                if item.pause_id is None and self.plan_id is not None:
                    raise ValidationError(f"flow[{index}] pause must have pause_id")
                if previous_speech is None or item.after_speech != previous_speech:
                    raise ValidationError(
                        f"flow[{index}] pause.after_speech must name the immediately preceding speech"
                    )
            elif isinstance(item, AudioItem):
                if item.audio_id is None and self.plan_id is not None:
                    raise ValidationError(f"flow[{index}] audio must have audio_id")
        if self.plan_id is not None:
            require_sha256_id(self.plan_id, "plan_id")
            if self.plan_id != self.computed_id():
                raise ValidationError("plan_id does not match render plan content")

    def identity_payload(self) -> dict[str, Any]:
        flow: list[dict[str, Any]] = []
        for item in self.flow:
            payload = item.identity_payload()
            if isinstance(item, SpeechItem):
                payload = {**payload, "segment_id": item.segment_id}
            elif isinstance(item, PauseItem):
                payload = {**payload, "pause_id": item.pause_id}
            else:
                payload = {**payload, "audio_id": item.audio_id}
            flow.append(payload)
        data: dict[str, Any] = {
            "format": self.format,
            "schema_version": self.schema_version,
            "source_plan_id": self.source_plan_id,
            "render_profile_id": self.render_profile_id,
            "flow": flow,
        }
        if self.source_plan_sha256 is not None:
            data["source_plan_sha256"] = self.source_plan_sha256
        return data

    def computed_id(self) -> str:
        return sha256_id(self.identity_payload())

    def with_identity(self) -> RenderPlan:
        identified: list[FlowItem] = []
        for item in self.flow:
            identified.append(item.with_identity())
        provisional = replace(self, flow=tuple(identified), plan_id=None)
        return replace(provisional, plan_id=provisional.computed_id())

    @property
    def speech(self) -> tuple[SpeechItem, ...]:
        return tuple(item for item in self.flow if isinstance(item, SpeechItem))


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    segment_number: int
    segment_id: str
    status: CheckStatus
    metric: str
    measured: int | None
    maximum: int | None
    source: str
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.segment_number <= 0:
            raise ValidationError("preflight check segment_number must be positive")
        require_sha256_id(self.segment_id, "preflight.segment_id")
        if self.status not in {"ok", "too_long", "error"}:
            raise ValidationError("invalid preflight check status")
        require_nonempty(self.metric, "preflight.metric")
        require_nonempty(self.source, "preflight.source")
        for field_name, value in (("measured", self.measured), ("maximum", self.maximum)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValidationError(f"preflight.{field_name} must be a non-negative integer or null")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "segment_number": self.segment_number,
            "segment_id": self.segment_id,
            "status": self.status,
            "metric": self.metric,
            "source": self.source,
        }
        if self.measured is not None:
            data["measured"] = self.measured
        if self.maximum is not None:
            data["maximum"] = self.maximum
        if self.detail is not None:
            data["detail"] = self.detail
        return data


@dataclass(frozen=True, slots=True)
class ReplanRequest:
    segment_number: int
    segment_id: str
    source: tuple[SourceSpan, ...]
    current_level: str
    action: str = "split"
    reason: str = "capacity"
    metric: str | None = None
    measured: int | None = None
    maximum: int | None = None

    def __post_init__(self) -> None:
        if self.segment_number <= 0:
            raise ValidationError("replan segment_number must be positive")
        require_sha256_id(self.segment_id, "replan.segment_id")
        if not self.source:
            raise ValidationError("replan.source must not be empty")
        if self.current_level not in _ALLOWED_PLANNING_LEVELS:
            raise ValidationError("replan.current_level is invalid")
        if self.action != "split":
            raise ValidationError("MVP replan.action must be 'split'")
        require_nonempty(self.reason, "replan.reason")
        if self.metric is not None:
            require_nonempty(self.metric, "replan.metric")
        for field_name, value in (("measured", self.measured), ("maximum", self.maximum)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValidationError(f"replan.{field_name} must be a non-negative integer or null")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "segment_number": self.segment_number,
            "segment_id": self.segment_id,
            "source": [item.to_dict() for item in self.source],
            "current_level": self.current_level,
            "action": self.action,
            "reason": self.reason,
        }
        if self.metric is not None:
            data["metric"] = self.metric
        if self.measured is not None:
            data["measured"] = self.measured
        if self.maximum is not None:
            data["maximum"] = self.maximum
        return data


@dataclass(frozen=True, slots=True)
class PreflightReport:
    render_plan_id: str
    source_plan_id: str
    render_profile_id: str
    status: PreflightStatus
    checks: tuple[PreflightCheck, ...]
    replan: tuple[ReplanRequest, ...] = ()
    producer: Producer | None = None
    report_id: str | None = None
    format: str = PREFLIGHT_FORMAT
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.format != PREFLIGHT_FORMAT or self.schema_version != SCHEMA_VERSION:
            raise ValidationError("unsupported preflight format/schema")
        require_sha256_id(self.render_plan_id, "render_plan_id")
        require_sha256_id(self.source_plan_id, "source_plan_id")
        require_sha256_id(self.render_profile_id, "render_profile_id")
        if self.status not in {"ok", "replan", "reject"}:
            raise ValidationError("preflight.status must be ok, replan, or reject")
        check_keys = {(item.segment_number, item.segment_id) for item in self.checks}
        if len(check_keys) != len(self.checks):
            raise ValidationError("preflight checks contain duplicate segments")
        for request in self.replan:
            if (request.segment_number, request.segment_id) not in check_keys:
                raise ValidationError("replan request must refer to a checked segment")
        if self.status == "ok":
            if self.replan or any(item.status != "ok" for item in self.checks):
                raise ValidationError("ok preflight cannot contain failed checks or replan requests")
        if self.status == "replan" and not self.replan:
            raise ValidationError("replan preflight requires at least one replan request")
        if self.report_id is not None:
            require_sha256_id(self.report_id, "report_id")
            if self.report_id != self.computed_id():
                raise ValidationError("report_id does not match preflight content")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "schema_version": self.schema_version,
            "render_plan_id": self.render_plan_id,
            "source_plan_id": self.source_plan_id,
            "render_profile_id": self.render_profile_id,
            "status": self.status,
            "checks": [item.to_dict() for item in self.checks],
            "replan": [item.to_dict() for item in self.replan],
        }

    def computed_id(self) -> str:
        return sha256_id(self.identity_payload())

    def with_identity(self) -> PreflightReport:
        return replace(self, report_id=self.computed_id())


@dataclass(frozen=True, slots=True)
class SpeechArtifact:
    segment_number: int
    segment_id: str
    wav: str
    sha256: str
    frames: int
    sample_rate: int
    channels: int

    def __post_init__(self) -> None:
        if self.segment_number <= 0:
            raise ValidationError("artifact.segment_number must be positive")
        require_sha256_id(self.segment_id, "artifact.segment_id")
        expected = f"{self.segment_number:06d}.wav"
        if self.wav != expected:
            raise ValidationError(f"artifact.wav must be {expected!r}")
        require_sha256_id(self.sha256, "artifact.sha256")
        for field_name, value in (
            ("frames", self.frames),
            ("sample_rate", self.sample_rate),
            ("channels", self.channels),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValidationError(f"artifact.{field_name} must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_number": self.segment_number,
            "segment_id": self.segment_id,
            "wav": self.wav,
            "sha256": self.sha256,
            "frames": self.frames,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
        }


@dataclass(frozen=True, slots=True)
class RenderManifest:
    render_plan_id: str
    speech: tuple[SpeechArtifact, ...]
    complete: bool
    producer: Producer | None = None
    manifest_id: str | None = None
    format: str = MANIFEST_FORMAT
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.format != MANIFEST_FORMAT or self.schema_version != SCHEMA_VERSION:
            raise ValidationError("unsupported render manifest format/schema")
        require_sha256_id(self.render_plan_id, "render_plan_id")
        expected = 1
        for artifact in self.speech:
            if artifact.segment_number != expected:
                raise ValidationError("manifest speech artifacts must be contiguous from 1")
            expected += 1
        if self.manifest_id is not None:
            require_sha256_id(self.manifest_id, "manifest_id")
            if self.manifest_id != self.computed_id():
                raise ValidationError("manifest_id does not match manifest content")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "schema_version": self.schema_version,
            "render_plan_id": self.render_plan_id,
            "complete": self.complete,
            "speech": [item.to_dict() for item in self.speech],
        }

    def computed_id(self) -> str:
        return sha256_id(self.identity_payload())

    def with_identity(self) -> RenderManifest:
        return replace(self, manifest_id=self.computed_id())

    def validate_against(self, plan: RenderPlan) -> None:
        if plan.plan_id is None or self.render_plan_id != plan.plan_id:
            raise ValidationError("manifest does not belong to this render plan")
        by_number = {item.segment_number: item for item in self.speech}
        for segment in plan.speech:
            artifact = by_number.get(segment.number)
            if artifact is None:
                if self.complete:
                    raise ValidationError(f"complete manifest missing speech {segment.number}")
                continue
            if artifact.segment_id != segment.segment_id:
                raise ValidationError(f"manifest speech {segment.number} segment_id mismatch")
