from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, cast

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from ._toml import dumps as toml_dumps

from .errors import CodecError
from .model import (
    MANIFEST_FORMAT,
    PLAN_FORMAT,
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
    SpeechArtifact,
    SpeechItem,
    SourceSpan,
    TokenBundle,
)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CodecError(f"{field} must be a TOML table")
    return cast(Mapping[str, Any], value)


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
    return Producer(name=str(data.get("name", "")), version=str(data.get("version", "")))


def _constraint(value: Any, field: str) -> CapacityConstraint | None:
    if value is None:
        return None
    data = _mapping(value, field)
    return CapacityConstraint(
        metric=str(data.get("metric", "")),
        maximum=_integer(data.get("maximum"), f"{field}.maximum"),
        guarantee=cast(Any, data.get("guarantee", "advisory")),
    )


def _source_spans(value: Any, field: str) -> tuple[SourceSpan, ...]:
    if not isinstance(value, list):
        raise CodecError(f"{field} must be an array of tables")
    result: list[SourceSpan] = []
    for index, raw in enumerate(value):
        item = _mapping(raw, f"{field}[{index}]")
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
    raw_span = data.get("span")
    if not isinstance(raw_span, list):
        raise CodecError("token.span must be an array")
    span: list[tuple[int, int]] = []
    for index, pair in enumerate(raw_span):
        if not isinstance(pair, list) or len(pair) != 2:
            raise CodecError(f"token.span[{index}] must be [start, end]")
        span.append((_integer(pair[0], "token.span start"), _integer(pair[1], "token.span end")))

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


def profile_from_dict(raw: Mapping[str, Any]) -> RenderProfile:
    roles_raw = _mapping(raw.get("roles"), "roles")
    roles: dict[str, RoleProfile] = {}
    for name, role_value in roles_raw.items():
        data = _mapping(role_value, f"roles.{name}")
        req_data = _mapping(data.get("requirements", {}), f"roles.{name}.requirements")
        cap_data = _mapping(data.get("capacity", {}), f"roles.{name}.capacity")
        roles[name] = RoleProfile(
            language=str(data.get("language", "")),
            binding=dict(_mapping(data.get("binding"), f"roles.{name}.binding")),
            requirements=RoleRequirements(
                token_fields=_strings(req_data.get("token_fields", []), "requirements.token_fields"),
                frontend=str(req_data.get("frontend", "renderer")),
                pronunciation=str(req_data.get("pronunciation", "renderer")),
            ),
            planner_capacity=_constraint(cap_data.get("planner"), "capacity.planner"),
            renderer_capacity=_constraint(cap_data.get("renderer"), "capacity.renderer"),
        )
    planning_raw = _mapping(raw.get("planning", {}), "planning")
    pauses_raw = _mapping(raw.get("pauses", {}), "pauses")
    durations = {
        key: _duration_ms(value, f"pauses.{key}")
        for key, value in pauses_raw.items()
        if key != "mode"
    }
    return RenderProfile(
        roles=roles,
        default_role=str(raw.get("default_role", "")),
        planning=PlanningPolicy(
            split=cast(Any, planning_raw.get("split", "sentence")),
            capacity_fallback=cast(
                Any,
                _strings(
                    planning_raw.get("capacity_fallback", ["clause", "token"]),
                    "planning.capacity_fallback",
                ),
            ),
            max_preflight_passes=_integer(
                planning_raw.get("max_preflight_passes", 3), "planning.max_preflight_passes"
            ),
        ),
        pauses=PausePolicy(mode=str(pauses_raw.get("mode", "manual")), durations_ms=durations),
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
    flow_raw = raw.get("flow")
    if not isinstance(flow_raw, list):
        raise CodecError("flow must be an array of tables")
    flow: list[Any] = []
    for index, item_value in enumerate(flow_raw):
        item = _mapping(item_value, f"flow[{index}]")
        kind = item.get("kind")
        if kind == "speech":
            number = _integer(item.get("number"), "speech.number")
            expected_wav = f"{number:06d}.wav"
            if item.get("wav") != expected_wav:
                raise CodecError(f"speech.wav must be {expected_wav!r}")
            flow.append(
                SpeechItem(
                    number=number,
                    text=str(item.get("text", "")),
                    language=str(item.get("language", "")),
                    role=str(item.get("role", "")),
                    binding=dict(_mapping(item.get("binding"), "speech.binding")),
                    source=_source_spans(item.get("source", []), "speech.source"),
                    split_reason=str(item.get("split_reason", "policy")),
                    planning_level=str(item.get("planning_level", "sentence")),
                    semantic_end=str(item.get("semantic_end", "none")),
                    tokens=_token_bundle(item.get("token")),
                    preflight_required=bool(item.get("preflight_required", True)),
                    segment_id=cast(str | None, item.get("segment_id")),
                )
            )
        elif kind == "pause":
            flow.append(
                PauseItem(
                    duration_ms=_duration_ms(item.get("duration"), "pause.duration"),
                    reason=str(item.get("reason", "")),
                    after_speech=_integer(item.get("after_speech"), "pause.after_speech"),
                    pause_id=cast(str | None, item.get("pause_id")),
                )
            )
        elif kind == "audio":
            flow.append(
                AudioItem(
                    src=str(item.get("src", "")),
                    source=_source_spans(item.get("source", []), "audio.source"),
                    options=dict(_mapping(item.get("options", {}), "audio.options")),
                    audio_id=cast(str | None, item.get("audio_id")),
                )
            )
        else:
            raise CodecError(f"flow[{index}].kind is unsupported: {kind!r}")
    return RenderPlan(
        source_plan_id=str(raw.get("source_plan_id", "")),
        source_plan_sha256=cast(str | None, raw.get("source_plan_sha256")),
        render_profile_id=str(raw.get("render_profile_id", "")),
        flow=tuple(flow),
        plan_id=cast(str | None, raw.get("plan_id")),
        producer=_producer(raw.get("producer")),
        format=str(raw.get("format", "")),
        schema_version=_integer(raw.get("schema_version"), "schema_version"),
    )


def plan_to_dict(plan: RenderPlan) -> dict[str, Any]:
    data: dict[str, Any] = {
        "format": plan.format,
        "schema_version": plan.schema_version,
        "plan_id": plan.plan_id or plan.computed_id(),
        "source_plan_id": plan.source_plan_id,
        "render_profile_id": plan.render_profile_id,
    }
    if plan.source_plan_sha256 is not None:
        data["source_plan_sha256"] = plan.source_plan_sha256
    if plan.producer is not None:
        data["producer"] = plan.producer.to_dict()
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
                "binding": dict(item.binding),
            }
            if item.tokens is not None:
                entry["token"] = item.tokens.to_dict()
            flow.append(entry)
        elif isinstance(item, PauseItem):
            flow.append(
                {
                    "kind": "pause",
                    "pause_id": item.pause_id or item.computed_id(),
                    "duration": f"{item.duration_ms}ms",
                    "reason": item.reason,
                    "after_speech": item.after_speech,
                }
            )
        else:
            flow.append(
                {
                    "kind": "audio",
                    "audio_id": item.audio_id or item.computed_id(),
                    "src": item.src,
                    "source": [span.to_dict() for span in item.source],
                    "options": dict(item.options),
                }
            )
    data["flow"] = flow
    return data


def preflight_from_dict(raw: Mapping[str, Any]) -> PreflightReport:
    checks_raw = raw.get("checks", [])
    replan_raw = raw.get("replan", [])
    if not isinstance(checks_raw, list) or not isinstance(replan_raw, list):
        raise CodecError("checks and replan must be arrays of tables")
    checks = tuple(
        PreflightCheck(
            segment_number=_integer(_mapping(item, "check").get("segment_number"), "check.segment_number"),
            segment_id=str(_mapping(item, "check").get("segment_id", "")),
            status=cast(Any, _mapping(item, "check").get("status")),
            metric=str(_mapping(item, "check").get("metric", "")),
            measured=cast(int | None, _mapping(item, "check").get("measured")),
            maximum=cast(int | None, _mapping(item, "check").get("maximum")),
            source=str(_mapping(item, "check").get("source", "")),
            detail=cast(str | None, _mapping(item, "check").get("detail")),
        )
        for item in checks_raw
    )
    replan = tuple(
        ReplanRequest(
            segment_number=_integer(_mapping(item, "replan").get("segment_number"), "replan.segment_number"),
            segment_id=str(_mapping(item, "replan").get("segment_id", "")),
            source=_source_spans(_mapping(item, "replan").get("source", []), "replan.source"),
            current_level=str(_mapping(item, "replan").get("current_level", "")),
            action=str(_mapping(item, "replan").get("action", "split")),
            reason=str(_mapping(item, "replan").get("reason", "capacity")),
            metric=cast(str | None, _mapping(item, "replan").get("metric")),
            measured=cast(int | None, _mapping(item, "replan").get("measured")),
            maximum=cast(int | None, _mapping(item, "replan").get("maximum")),
        )
        for item in replan_raw
    )
    return PreflightReport(
        render_plan_id=str(raw.get("render_plan_id", "")),
        source_plan_id=str(raw.get("source_plan_id", "")),
        render_profile_id=str(raw.get("render_profile_id", "")),
        status=cast(Any, raw.get("status")),
        checks=checks,
        replan=replan,
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
    speech_raw = raw.get("speech", [])
    if not isinstance(speech_raw, list):
        raise CodecError("speech must be an array of tables")
    speech = tuple(
        SpeechArtifact(
            segment_number=_integer(_mapping(item, "speech").get("segment_number"), "speech.segment_number"),
            segment_id=str(_mapping(item, "speech").get("segment_id", "")),
            wav=str(_mapping(item, "speech").get("wav", "")),
            sha256=str(_mapping(item, "speech").get("sha256", "")),
            frames=_integer(_mapping(item, "speech").get("frames"), "speech.frames"),
            sample_rate=_integer(_mapping(item, "speech").get("sample_rate"), "speech.sample_rate"),
            channels=_integer(_mapping(item, "speech").get("channels"), "speech.channels"),
        )
        for item in speech_raw
    )
    return RenderManifest(
        render_plan_id=str(raw.get("render_plan_id", "")),
        speech=speech,
        complete=bool(raw.get("complete", False)),
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
    except (OSError, tomllib.TOMLDecodeError) as exc:
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


def load_any(path: str | Path) -> RenderProfile | RenderPlan | PreflightReport | RenderManifest:
    raw = _read(path)
    format_name = raw.get("format")
    if format_name == PROFILE_FORMAT:
        return profile_from_dict(raw)
    if format_name == PLAN_FORMAT:
        return plan_from_dict(raw)
    if format_name == PREFLIGHT_FORMAT:
        return preflight_from_dict(raw)
    if format_name == MANIFEST_FORMAT:
        return manifest_from_dict(raw)
    raise CodecError(f"unknown renderplan document format: {format_name!r}")
