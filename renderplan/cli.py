from __future__ import annotations

import argparse
from pathlib import Path

from .codec import load_any
from .model import PreflightReport, RenderManifest, RenderPlan, RenderProfile
from .version import __version__


def _summary(value: object) -> str:
    if isinstance(value, RenderProfile):
        return f"renderprofile {value.profile_id or value.computed_id()} roles={len(value.roles)}"
    if isinstance(value, RenderPlan):
        return (
            f"renderplan {value.plan_id or value.computed_id()} "
            f"speech={len(value.speech)} flow={len(value.flow)}"
        )
    if isinstance(value, PreflightReport):
        return (
            f"renderplan-preflight {value.report_id or value.computed_id()} "
            f"status={value.status} checks={len(value.checks)} replan={len(value.replan)}"
        )
    if isinstance(value, RenderManifest):
        return (
            f"render-manifest {value.manifest_id or value.computed_id()} "
            f"complete={str(value.complete).lower()} speech={len(value.speech)}"
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
