"""Pure cross-artifact validation helpers for RenderPlan handoffs."""

from __future__ import annotations

from .collection import RenderPlanSet, RenderPlanSetChapter, validate_renderplan_set
from .errors import ValidationError
from .model import PreflightReport, RenderManifest, RenderPlan, RenderProfile


def validate_plan_profile(plan: RenderPlan, profile: RenderProfile) -> None:
    """Validate that a plan is internally compatible with its RenderProfile."""
    plan.validate_against_profile(profile)


def validate_preflight_handoff(
    plan: RenderPlan, report: PreflightReport, *, require_ok: bool = False
) -> None:
    """Validate preflight identity/content against a plan, optionally requiring success."""
    report.validate_against(plan)
    if require_ok and report.status != "ok":
        raise ValidationError(
            f"preflight status must be 'ok' for execution; got {report.status!r}"
        )


def validate_manifest_handoff(
    plan: RenderPlan, manifest: RenderManifest, *, require_complete: bool = False
) -> None:
    """Validate manifest membership against a plan, optionally requiring completeness."""
    manifest.validate_against(plan)
    if require_complete and not manifest.complete:
        raise ValidationError("render manifest must be complete for this handoff")


def validate_set_chapter_plan(
    renderplan_set: RenderPlanSet, chapter: RenderPlanSetChapter, plan: RenderPlan
) -> None:
    """Validate a chapter's plan and profile identities against its containing set."""
    validate_renderplan_set(renderplan_set)
    if chapter not in renderplan_set.chapters:
        raise ValidationError("chapter is not a member of the RenderPlan set")
    actual_plan_id = plan.plan_id or plan.computed_id()
    if chapter.render_plan_id != actual_plan_id:
        raise ValidationError("chapter render_plan_id does not match the RenderPlan")
    if chapter.source_plan_id != plan.source_plan_id:
        raise ValidationError("chapter source_plan_id does not match the RenderPlan")
    if plan.render_profile_id != renderplan_set.profile_id:
        raise ValidationError("RenderPlan render_profile_id does not match the RenderPlan set")


__all__ = [
    "validate_manifest_handoff",
    "validate_plan_profile",
    "validate_preflight_handoff",
    "validate_set_chapter_plan",
]
