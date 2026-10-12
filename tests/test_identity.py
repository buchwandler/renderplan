from renderplan import PauseItem, RenderPlan, RenderProfile, RoleProfile, SourceSpan, SpeechItem

SOURCE = "sha256:" + "a" * 64


def test_producer_version_does_not_change_profile_identity() -> None:
    a = RenderProfile(roles={"n": RoleProfile("en-US", {"renderer": "fake"})}, default_role="n").with_identity()
    b = RenderProfile(roles={"n": RoleProfile("en-US", {"renderer": "fake"})}, default_role="n").with_identity()
    assert a.profile_id == b.profile_id


def test_pause_is_part_of_plan_identity() -> None:
    profile = RenderProfile(roles={"n": RoleProfile("en-US", {"renderer": "fake"})}, default_role="n").with_identity()
    speech = SpeechItem(1, "Hi.", "en-US", "n", {"renderer": "fake"}, (SourceSpan("f.s", 0, 3),), semantic_end="sentence")
    p1 = RenderPlan(SOURCE, profile.profile_id, (speech, PauseItem(100, "sentence", 1))).with_identity()  # type: ignore[arg-type]
    p2 = RenderPlan(SOURCE, profile.profile_id, (speech, PauseItem(200, "sentence", 1))).with_identity()  # type: ignore[arg-type]
    assert p1.plan_id != p2.plan_id
