from __future__ import annotations

import argparse
from pathlib import Path

from .codec import load_any
from .model import (
    AudioItem,
    PauseItem,
    PreflightReport,
    RenderManifest,
    RenderPlan,
    RenderProfile,
)
from .version import __version__


def _summary(value: object) -> str:
    if isinstance(value, RenderProfile):
        fallbacks = ",".join(value.planning.capacity_fallback)
        return (
            f"renderprofile {value.profile_id or value.computed_id()} roles={len(value.roles)} "
            f"split={value.planning.split} fallbacks={fallbacks}"
        )
    if isinstance(value, RenderPlan):
        pauses = sum(isinstance(item, PauseItem) for item in value.flow)
        audio = sum(isinstance(item, AudioItem) for item in value.flow)
        return "\n".join(
            (
                f"renderplan {value.plan_id or value.computed_id()}",
                f"  source_plan: {value.source_plan_id}",
                f"  profile:     {value.render_profile_id}",
                f"  speech:      {len(value.speech)}",
                f"  pauses:      {pauses}",
                f"  audio:       {audio}",
            )
        )
    if isinstance(value, PreflightReport):
        too_long = sum(check.status == "too_long" for check in value.checks)
        return "\n".join(
            (
                f"renderplan-preflight {value.report_id or value.computed_id()}",
                f"  plan:        {value.render_plan_id}",
                f"  status:      {value.status}",
                f"  checked:     {len(value.checks)}",
                f"  too_long:    {too_long}",
            )
        )
    if isinstance(value, RenderManifest):
        return "\n".join(
            (
                f"render-manifest {value.manifest_id or value.computed_id()}",
                f"  plan:        {value.render_plan_id}",
                f"  complete:    {str(value.complete).lower()}",
                f"  speech:      {len(value.speech)}",
            )
        )
    raise AssertionError(type(value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="renderplan")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "inspect"):
        command = sub.add_parser(name)
        command.add_argument("file", type=Path)
    args = parser.parse_args(argv)
    value = load_any(args.file)
    print(_summary(value))
    return 0
