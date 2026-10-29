from __future__ import annotations

import importlib
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

try:
    tomllib: Any = importlib.import_module("tomllib")
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    tomllib = importlib.import_module("tomli")

from ._toml import dumps as toml_dumps
from .errors import CodecError, ValidationError
from .identity import require_toml_value
from .model import (
    LEGACY_PLAN_SCHEMA_VERSION,
    MANIFEST_FORMAT,
    PLAN_FORMAT,
    PLAN_SCHEMA_VERSION,
    PREFLIGHT_FORMAT,
    PROFILE_FORMAT,
    AudioItem,
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
    SourceSpan,
    SpeechArtifact,
    SpeechControl,
    SpeechItem,
    TokenBundle,
)

if TYPE_CHECKING:
    from .collection import RenderPlanSet


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CodecError(f"{field} must be a TOML table")
    return cast(Mapping[str, Any], value)


def _check_keys(data: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = set(data).difference(allowed)
    if unknown:
        raise CodecError(f"{field} contains unknown field(s): {', '.join(sorted(unknown))}")


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise CodecError(f"{field} must be a boolean")
    return value


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CodecError(f"{field} must be an integer")
    return value


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CodecError(f"{field} must be an array of strings")
    return tuple(value)


def _duration_ms(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise CodecError(f"{field} must be '<integer>ms' or integer ms")
    if isinstance(value, int):
        if value < 0:
            raise CodecError(f"{field} must be non-negative")
        return value
    if isinstance(value, str) and value.endswith("ms"):
        raw = value[:-2]
        if raw.isdigit():
            return int(raw)
    raise CodecError(f"{field} must be '<integer>ms' or integer ms")


def _producer(value: Any) -> Producer | None:
    if value is None:
        return None
    data = _mapping(value, "producer")
    _check_keys(data, {"name", "version"}, "producer")
    return Producer(name=str(data.get("name", "")), version=str(data.get("version", "")))


def _constraint(value: Any, field: str) -> CapacityConstraint | None:
    if value is None:
        return None
    data = _mapping(value, field)
    _check_keys(data, {"metric", "maximum", "guarantee"}, field)
    return CapacityConstraint(
        metric=str(data.get("metric", "")),
        maximum=_integer(data.get("maximum"), f"{field}.maximum"),
        guarantee=data.get("guarantee", "advisory"),
    )


def _source_spans(value: Any, field: str) -> tuple[SourceSpan, ...]:
    if not isinstance(value, list):
        raise CodecError(f"{field} must be an array of tables")
    result: list[SourceSpan] = []
    for index, raw in enumerate(value):
        item = _mapping(raw, f"{field}[{index}]")
        _check_keys(item, {"locator", "start", "end"}, f"{field}[{index}]")
        result.append(
            SourceSpan(
                locator=str(item.get("locator", "")),
                start=_integer(item.get("start"), f"{field}[{index}].start"),
                end=_integer(item.get("end"), f"{field}[{index}].end"),
            )
        )
    return tuple(result)


def _token_bundle(value: Any) -> TokenBundle | None:
    if value is None:
        return None
    data = _mapping(value, "token")
    _check_keys(data, {"span", "pos", "tag", "lemma", "morph"}, "token")
    raw_span = data.get("span")
    if not isinstance(raw_span, list):
        raise CodecError("token.span must be an array")
    span: list[tuple[int, int]] = []
    for index, pair in enumerate(raw_span):
        if not isinstance(pair, list) or len(pair) != 2:
            raise CodecError(f"token.span[{index}] must be [start, end]")
        span.append(
            (_integer(pair[0], "token.span start"), _integer(pair[1], "token.span end"))
        )

    def sparse(name: str) -> tuple[tuple[int, str], ...]:
        raw = data.get(name, [])
        if not isinstance(raw, list):
            raise CodecError(f"token.{name} must be an array")
        result: list[tuple[int, str]] = []
        for pair in raw:
            if not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[1], str):
                raise CodecError(f"token.{name} entries must be [index, string]")
            result.append((_integer(pair[0], f"token.{name} index"), pair[1]))
        return tuple(result)

    return TokenBundle(
        span=tuple(span),
        pos=_strings(data.get("pos", []), "token.pos"),
        tag=_strings(data.get("tag", []), "token.tag"),
        lemma=sparse("lemma"),
        morph=sparse("morph"),
    )


def _speech_controls(value: Any) -> tuple[SpeechControl, ...]:
    if not isinstance(value, list):
        raise CodecError("speech.control must be an array of tables")
    result: list[SpeechControl] = []
    for index, raw in enumerate(value):
        field = f"speech.control[{index}]"
        data = _mapping(raw, field)
        _check_keys(data, {"start", "end", "name", "params"}, field)
        params = _mapping(data.get("params", {}), f"{field}.params")
        result.append(
            SpeechControl(
                start=_integer(data.get("start"), f"{field}.start"),
                end=_integer(data.get("end"), f"{field}.end"),
                name=str(data.get("name", "")),
                params=dict(params),
            )
        )
    return tuple(result)


def profile_from_dict(raw: Mapping[str, Any]) -> RenderProfile:
    _check_keys(
        raw,
        {
            "format",
            "schema_version",
            "profile_id",
            "producer",
            "default_role",
            "planning",
            "pauses",
            "roles",
        },
        "renderprofile",
    )
    roles_raw = _mapping(raw.get("roles"), "roles")
    roles: dict[str, RoleProfile] = {}
    for name, role_value in roles_raw.items():
        role_field = f"roles.{name}"
        data = _mapping(role_value, role_field)
        _check_keys(data, {"language", "binding", "requirements", "capacity"}, role_field)
        req_data = _mapping(data.get("requirements", {}), f"{role_field}.requirements")
        _check_keys(
            req_data,
            {"token_fields", "frontend", "pronunciation"},
            f"{role_field}.requirements",
        )
        cap_data = _mapping(data.get("capacity", {}), f"{role_field}.capacity")
        _check_keys(cap_data, {"planner", "renderer"}, f"{role_field}.capacity")
        roles[name] = RoleProfile(
            language=str(data.get("language", "")),
            binding=dict(_mapping(data.get("binding"), f"{role_field}.binding")),
            requirements=RoleRequirements(
                token_fields=_strings(
                    req_data.get("token_fields", []), f"{role_field}.requirements.token_fields"
                ),
                frontend=str(req_data.get("frontend", "renderer")),
                pronunciation=str(req_data.get("pronunciation", "renderer")),
            ),
            planner_capacity=_constraint(
                cap_data.get("planner"), f"{role_field}.capacity.planner"
            ),
            renderer_capacity=_constraint(
                cap_data.get("renderer"), f"{role_field}.capacity.renderer"
            ),
        )
    planning_raw = _mapping(raw.get("planning", {}), "planning")
    _check_keys(planning_raw, {"split", "capacity_fallback"}, "planning")
    pauses_raw = _mapping(raw.get("pauses", {}), "pauses")
    pause_fields = {
        "mode",
        "x_weak",
        "weak",
        "medium",
        "strong",
        "x_strong",
        "clause",
        "sentence",
        "paragraph",
        "voice_change",
    }
    _check_keys(pauses_raw, pause_fields, "pauses")
    durations = {
        key: _duration_ms(value, f"pauses.{key}")
        for key, value in pauses_raw.items()
        if key != "mode"
    }
    return RenderProfile(
        roles=roles,
        default_role=str(raw.get("default_role", "")),
        planning=PlanningPolicy(
            split=planning_raw.get("split", "sentence"),
            capacity_fallback=cast(
                Any,
                _strings(
                    planning_raw.get("capacity_fallback", ["clause", "token"]),
                    "planning.capacity_fallback",
                ),
            ),
        ),
        pauses=PausePolicy(mode=pauses_raw.get("mode", "manual"), durations_ms=durations),
        profile_id=cast(str | None, raw.get("profile_id")),
        producer=_producer(raw.get("producer")),
        format=str(raw.get("format", "")),
        schema_version=_integer(raw.get("schema_version"), "schema_version"),
    )


def profile_to_dict(profile: RenderProfile) -> dict[str, Any]:
    data: dict[str, Any] = {
        "format": profile.format,
        "schema_version": profile.schema_version,
        "profile_id": profile.profile_id or profile.computed_id(),
        "default_role": profile.default_role,
    }
    if profile.producer is not None:
        data["producer"] = profile.producer.to_dict()
    data["planning"] = profile.planning.to_dict()
    data["pauses"] = profile.pauses.to_dict()
    data["roles"] = {name: role.to_dict() for name, role in sorted(profile.roles.items())}
    return data


def plan_from_dict(raw: Mapping[str, Any]) -> RenderPlan:
    version = _integer(raw.get("schema_version"), "schema_version")
    if version == LEGACY_PLAN_SCHEMA_VERSION:
        return _plan_v1_from_dict(raw)
    if version == PLAN_SCHEMA_VERSION:
        return _plan_v2_from_dict(raw)
    raise CodecError(f"unsupported renderplan schema_version: {version}")


def _plan_v1_from_dict(raw: Mapping[str, Any]) -> RenderPlan:
    return _plan_from_dict(raw, LEGACY_PLAN_SCHEMA_VERSION)


def _plan_v2_from_dict(raw: Mapping[str, Any]) -> RenderPlan:
    return _plan_from_dict(raw, PLAN_SCHEMA_VERSION)


def _plan_from_dict(raw: Mapping[str, Any], version: int) -> RenderPlan:
    allowed_fields = {
        "format",
        "schema_version",
        "plan_id",
        "source_plan_id",
        "render_profile_id",
        "producer",
        "flow",
    }
    if version == PLAN_SCHEMA_VERSION:
        allowed_fields.add("bindings")
    _check_keys(raw, allowed_fields, "renderplan")

    bindings: dict[str, dict[str, Any]] = {}
    if version == PLAN_SCHEMA_VERSION:
        bindings_raw = _mapping(raw.get("bindings", {}), "bindings")
        for role, binding_value in bindings_raw.items():
            if not isinstance(role, str) or not role:
                raise CodecError("bindings must use non-empty role names")
            field = f"bindings.{role}"
            binding = _mapping(binding_value, field)
            if not binding:
                raise CodecError(f"{field} must not be empty")
            try:
                require_toml_value(binding, field)
            except ValidationError as exc:
                raise CodecError(str(exc)) from exc
            bindings[role] = dict(binding)

    flow_raw = raw.get("flow")
    if not isinstance(flow_raw, list):
        raise CodecError("flow must be an array of tables")
    flow: list[Any] = []
    for index, item_value in enumerate(flow_raw):
        field = f"flow[{index}]"
        item = _mapping(item_value, field)
        kind = item.get("kind")
        if kind == "speech":
            speech_fields = {
                "kind",
                "number",
                "segment_id",
                "wav",
                "text",
                "language",
                "role",
                "source",
                "split_reason",
                "planning_level",
                "semantic_end",
                "preflight_required",
                "token",
                "control",
            }
            if version == LEGACY_PLAN_SCHEMA_VERSION:
                speech_fields.add("binding")
            _check_keys(item, speech_fields, field)
            number = _integer(item.get("number"), f"{field}.number")
            expected_wav = f"{number:06d}.wav"
            if item.get("wav") != expected_wav:
                raise CodecError(f"speech.wav must be {expected_wav!r}")
            role = str(item.get("role", ""))
            if version == PLAN_SCHEMA_VERSION:
                role_binding = bindings.get(role)
                if role_binding is None:
                    raise CodecError(f"{field}.role has no matching plan binding: {role!r}")
            else:
                role_binding = dict(_mapping(item.get("binding"), f"{field}.binding"))
            flow.append(
                SpeechItem(
                    number=number,
                    text=str(item.get("text", "")),
                    language=str(item.get("language", "")),
                    role=role,
                    binding=dict(role_binding),
                    source=_source_spans(item.get("source", []), f"{field}.source"),
                    split_reason=str(item.get("split_reason", "policy")),
                    planning_level=str(item.get("planning_level", "sentence")),
                    semantic_end=str(item.get("semantic_end", "none")),
                    tokens=_token_bundle(item.get("token")),
                    controls=_speech_controls(item.get("control", [])),
                    preflight_required=_boolean(
                        item.get("preflight_required", True), f"{field}.preflight_required"
                    ),
                    segment_id=cast(str | None, item.get("segment_id")),
                )
            )
        elif kind == "pause":
            _check_keys(item, {"kind", "pause_id", "duration", "reason", "source"}, field)
            flow.append(
                PauseItem(
                    duration_ms=_duration_ms(item.get("duration"), f"{field}.duration"),
                    reason=str(item.get("reason", "")),
                    source=_source_spans(item.get("source", []), f"{field}.source"),
                    pause_id=cast(str | None, item.get("pause_id")),
                )
            )
        elif kind == "audio":
            _check_keys(item, {"kind", "audio_id", "src", "source", "options"}, field)
            flow.append(
                AudioItem(
                    src=str(item.get("src", "")),
                    source=_source_spans(item.get("source", []), f"{field}.source"),
                    options=dict(_mapping(item.get("options", {}), f"{field}.options")),
                    audio_id=cast(str | None, item.get("audio_id")),
                )
            )
        else:
            raise CodecError(f"{field}.kind is unsupported: {kind!r}")

    if version == PLAN_SCHEMA_VERSION:
        used_roles = {item.role for item in flow if isinstance(item, SpeechItem)}
        unused_roles = sorted(set(bindings).difference(used_roles))
        if unused_roles:
            raise CodecError(f"bindings contains unused role(s): {', '.join(unused_roles)}")

    return RenderPlan(
        source_plan_id=str(raw.get("source_plan_id", "")),
        render_profile_id=str(raw.get("render_profile_id", "")),
        flow=tuple(flow),
        plan_id=cast(str | None, raw.get("plan_id")),
        producer=_producer(raw.get("producer")),
        format=str(raw.get("format", "")),
        schema_version=version,
    )


def _bindings_by_role(plan: RenderPlan) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for speech in plan.speech:
        binding = dict(speech.binding)
        previous = result.get(speech.role)
        if previous is None:
            result[speech.role] = binding
        elif previous != binding:
            raise CodecError(
                f"role {speech.role!r} has multiple bindings and cannot be encoded as RenderPlan v2"
            )
    return dict(sorted(result.items()))


def plan_to_dict(plan: RenderPlan) -> dict[str, Any]:
    if plan.schema_version == LEGACY_PLAN_SCHEMA_VERSION:
        return _plan_v1_to_dict(plan)
    if plan.schema_version == PLAN_SCHEMA_VERSION:
        return _plan_v2_to_dict(plan)
    raise CodecError(f"unsupported renderplan schema_version: {plan.schema_version}")


def _plan_v1_to_dict(plan: RenderPlan) -> dict[str, Any]:
    return _plan_to_dict(plan, LEGACY_PLAN_SCHEMA_VERSION)


def _plan_v2_to_dict(plan: RenderPlan) -> dict[str, Any]:
    return _plan_to_dict(plan, PLAN_SCHEMA_VERSION)


def _plan_to_dict(plan: RenderPlan, version: int) -> dict[str, Any]:
    data: dict[str, Any] = {
        "format": plan.format,
        "schema_version": plan.schema_version,
        "plan_id": plan.plan_id or plan.computed_id(),
        "source_plan_id": plan.source_plan_id,
        "render_profile_id": plan.render_profile_id,
    }
    if plan.producer is not None:
        data["producer"] = plan.producer.to_dict()
    if version == PLAN_SCHEMA_VERSION:
        bindings = _bindings_by_role(plan)
        if bindings:
            data["bindings"] = bindings

    flow: list[dict[str, Any]] = []
    for item in plan.flow:
        if isinstance(item, SpeechItem):
            entry: dict[str, Any] = {
                "kind": "speech",
                "number": item.number,
                "segment_id": item.segment_id or item.computed_id(),
                "wav": item.wav,
                "text": item.text,
                "language": item.language,
                "role": item.role,
                "source": [span.to_dict() for span in item.source],
                "split_reason": item.split_reason,
                "planning_level": item.planning_level,
                "semantic_end": item.semantic_end,
                "preflight_required": item.preflight_required,
            }
            if version == LEGACY_PLAN_SCHEMA_VERSION:
                entry["binding"] = dict(item.binding)
            if item.tokens is not None:
                entry["token"] = item.tokens.to_dict()
            if item.controls:
                entry["control"] = [control.to_dict() for control in item.controls]
            flow.append(entry)
        elif isinstance(item, PauseItem):
            pause_entry: dict[str, Any] = {
                "kind": "pause",
                "pause_id": item.pause_id or item.computed_id(),
                "duration": f"{item.duration_ms}ms",
                "reason": item.reason,
            }
            if item.source:
                pause_entry["source"] = [span.to_dict() for span in item.source]
            flow.append(pause_entry)
        else:
            audio_entry: dict[str, Any] = {
                "kind": "audio",
                "audio_id": item.audio_id or item.computed_id(),
                "src": item.src,
                "source": [span.to_dict() for span in item.source],
            }
            if item.options:
                audio_entry["options"] = dict(item.options)
            flow.append(audio_entry)
    data["flow"] = flow
    return data


def preflight_from_dict(raw: Mapping[str, Any]) -> PreflightReport:
    _check_keys(
        raw,
        {
            "format",
            "schema_version",
            "report_id",
            "render_plan_id",
            "source_plan_id",
            "render_profile_id",
            "status",
            "checks",
            "replan",
            "producer",
        },
        "preflight",
    )
    checks_raw = raw.get("checks", [])
    replan_raw = raw.get("replan", [])
    if not isinstance(checks_raw, list) or not isinstance(replan_raw, list):
        raise CodecError("checks and replan must be arrays of tables")
    checks_list: list[PreflightCheck] = []
    for index, raw_check in enumerate(checks_raw):
        field = f"checks[{index}]"
        data = _mapping(raw_check, field)
        _check_keys(
            data,
            {
                "segment_number",
                "segment_id",
                "status",
                "metric",
                "measured",
                "maximum",
                "source",
                "detail",
            },
            field,
        )
        checks_list.append(
            PreflightCheck(
                segment_number=_integer(data.get("segment_number"), f"{field}.segment_number"),
                segment_id=str(data.get("segment_id", "")),
                status=cast(Any, data.get("status")),
                metric=str(data.get("metric", "")),
                measured=cast(int | None, data.get("measured")),
                maximum=cast(int | None, data.get("maximum")),
                source=str(data.get("source", "")),
                detail=cast(str | None, data.get("detail")),
            )
        )
    replan_list: list[ReplanRequest] = []
    for index, raw_request in enumerate(replan_raw):
        field = f"replan[{index}]"
        data = _mapping(raw_request, field)
        _check_keys(
            data,
            {
                "segment_number",
                "segment_id",
                "source",
                "current_level",
                "action",
                "reason",
                "metric",
                "measured",
                "maximum",
            },
            field,
        )
        replan_list.append(
            ReplanRequest(
                segment_number=_integer(data.get("segment_number"), f"{field}.segment_number"),
                segment_id=str(data.get("segment_id", "")),
                source=_source_spans(data.get("source", []), f"{field}.source"),
                current_level=str(data.get("current_level", "")),
                action=str(data.get("action", "split")),
                reason=str(data.get("reason", "capacity")),
                metric=cast(str | None, data.get("metric")),
                measured=cast(int | None, data.get("measured")),
                maximum=cast(int | None, data.get("maximum")),
            )
        )
    return PreflightReport(
        render_plan_id=str(raw.get("render_plan_id", "")),
        source_plan_id=str(raw.get("source_plan_id", "")),
        render_profile_id=str(raw.get("render_profile_id", "")),
        status=cast(Any, raw.get("status")),
        checks=tuple(checks_list),
        replan=tuple(replan_list),
        producer=_producer(raw.get("producer")),
        report_id=cast(str | None, raw.get("report_id")),
        format=str(raw.get("format", "")),
        schema_version=_integer(raw.get("schema_version"), "schema_version"),
    )


def preflight_to_dict(report: PreflightReport) -> dict[str, Any]:
    data: dict[str, Any] = {
        "format": report.format,
        "schema_version": report.schema_version,
        "report_id": report.report_id or report.computed_id(),
        "render_plan_id": report.render_plan_id,
        "source_plan_id": report.source_plan_id,
        "render_profile_id": report.render_profile_id,
        "status": report.status,
        "checks": [item.to_dict() for item in report.checks],
        "replan": [item.to_dict() for item in report.replan],
    }
    if report.producer is not None:
        data["producer"] = report.producer.to_dict()
    return data


def manifest_from_dict(raw: Mapping[str, Any]) -> RenderManifest:
    _check_keys(
        raw,
        {
            "format",
            "schema_version",
            "manifest_id",
            "render_plan_id",
            "complete",
            "producer",
            "speech",
        },
        "render-manifest",
    )
    speech_raw = raw.get("speech", [])
    if not isinstance(speech_raw, list):
        raise CodecError("speech must be an array of tables")
    speech_list: list[SpeechArtifact] = []
    for index, raw_artifact in enumerate(speech_raw):
        field = f"speech[{index}]"
        data = _mapping(raw_artifact, field)
        _check_keys(
            data,
            {
                "segment_number",
                "segment_id",
                "wav",
                "sha256",
                "frames",
                "sample_rate",
                "channels",
            },
            field,
        )
        speech_list.append(
            SpeechArtifact(
                segment_number=_integer(data.get("segment_number"), f"{field}.segment_number"),
                segment_id=str(data.get("segment_id", "")),
                wav=str(data.get("wav", "")),
                sha256=str(data.get("sha256", "")),
                frames=_integer(data.get("frames"), f"{field}.frames"),
                sample_rate=_integer(data.get("sample_rate"), f"{field}.sample_rate"),
                channels=_integer(data.get("channels"), f"{field}.channels"),
            )
        )
    return RenderManifest(
        render_plan_id=str(raw.get("render_plan_id", "")),
        speech=tuple(speech_list),
        complete=_boolean(raw.get("complete", False), "complete"),
        producer=_producer(raw.get("producer")),
        manifest_id=cast(str | None, raw.get("manifest_id")),
        format=str(raw.get("format", "")),
        schema_version=_integer(raw.get("schema_version"), "schema_version"),
    )


def manifest_to_dict(manifest: RenderManifest) -> dict[str, Any]:
    data: dict[str, Any] = {
        "format": manifest.format,
        "schema_version": manifest.schema_version,
        "manifest_id": manifest.manifest_id or manifest.computed_id(),
        "render_plan_id": manifest.render_plan_id,
        "complete": manifest.complete,
        "speech": [item.to_dict() for item in manifest.speech],
    }
    if manifest.producer is not None:
        data["producer"] = manifest.producer.to_dict()
    return data


def _read(path: str | Path) -> Mapping[str, Any]:
    location = Path(path)
    try:
        with location.open("rb") as stream:
            return _mapping(tomllib.load(stream), "document")
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise CodecError(f"cannot read {location}: {exc}") from exc


def _write(path: str | Path, data: Mapping[str, Any]) -> Path:
    location = Path(path)
    try:
        text = toml_dumps(dict(data))
        location.parent.mkdir(parents=True, exist_ok=True)
        temporary = location.with_name(location.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(location)
    except OSError as exc:
        raise CodecError(f"cannot write {location}: {exc}") from exc
    return location


def load_profile(path: str | Path) -> RenderProfile:
    return profile_from_dict(_read(path))


def load_profile_for_reidentification(path: str | Path) -> RenderProfile:
    """Load and validate a profile while ignoring only its stored identity."""
    raw = dict(_read(path))
    raw["profile_id"] = None
    return profile_from_dict(raw)


def save_profile(profile: RenderProfile, path: str | Path) -> Path:
    return _write(path, profile_to_dict(profile.with_identity()))


def load_plan(path: str | Path) -> RenderPlan:
    return plan_from_dict(_read(path))


def save_plan(plan: RenderPlan, path: str | Path) -> Path:
    return _write(path, plan_to_dict(plan.with_identity()))


def load_preflight(path: str | Path) -> PreflightReport:
    return preflight_from_dict(_read(path))


def save_preflight(report: PreflightReport, path: str | Path) -> Path:
    return _write(path, preflight_to_dict(report.with_identity()))


def load_manifest(path: str | Path) -> RenderManifest:
    return manifest_from_dict(_read(path))


def save_manifest(manifest: RenderManifest, path: str | Path) -> Path:
    return _write(path, manifest_to_dict(manifest.with_identity()))


def load_any(
    path: str | Path,
) -> RenderProfile | RenderPlan | PreflightReport | RenderManifest | RenderPlanSet:
    raw = _read(path)
    from .collection import RENDERPLAN_SET_FORMAT, renderplan_set_from_dict

    format_name = raw.get("format")
    if format_name == PROFILE_FORMAT:
        return profile_from_dict(raw)
    if format_name == PLAN_FORMAT:
        return plan_from_dict(raw)
    if format_name == PREFLIGHT_FORMAT:
        return preflight_from_dict(raw)
    if format_name == MANIFEST_FORMAT:
        return manifest_from_dict(raw)
    if format_name == RENDERPLAN_SET_FORMAT:
        return renderplan_set_from_dict(raw)
    raise CodecError(f"unknown renderplan document format: {format_name!r}")
