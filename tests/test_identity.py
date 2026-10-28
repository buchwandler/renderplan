from dataclasses import replace

from renderplan import (
    PauseItem,
    Producer,
    RenderPlan,
    RenderProfile,
    RoleProfile,
    SourceSpan,
    SpeechControl,
    SpeechItem,
)

SOURCE = "sha256:" + "a" * 64
PROFILE = RenderProfile(
    roles={"n": RoleProfile("en-US", {"renderer": "fake"})},
    default_role="n",
).with_identity()
PROFILE_ID = PROFILE.profile_id or PROFILE.computed_id()


def speech(number: int = 1, locator: str = "f000000.s000") -> SpeechItem:
    return SpeechItem(
        number,
        "Hi.",
        "en-US",
        "n",
        {"renderer": "fake"},
        (SourceSpan(locator, 0, 3),),
    ).with_identity()


def test_producer_version_does_not_change_profile_identity() -> None:
    other = replace(
        PROFILE, producer=Producer("voicerender", "9.9.9"), profile_id=None
    ).with_identity()
    assert other.profile_id == PROFILE.profile_id


def test_speech_request_identity_excludes_number_and_provenance() -> None:
    first = speech()
    renumbered = speech(5)
    different_source = speech(locator="opaque.source.address")
    assert first.segment_id == renumbered.segment_id == different_source.segment_id


def test_say_as_control_changes_speech_request_identity() -> None:
    plain = replace(
        speech(),
        text="A R T",
        source=(SourceSpan("f000000.s000", 0, 5),),
        segment_id=None,
    ).with_identity()
    controlled = replace(
        plain,
        controls=(SpeechControl(0, 5, "say_as", {"interpret_as": "characters"}),),
        segment_id=None,
    ).with_identity()

    assert plain.text == controlled.text == "A R T"
    assert plain.segment_id != controlled.segment_id


def test_pause_is_part_of_plan_identity() -> None:
    item = speech()
    p1 = RenderPlan(SOURCE, PROFILE_ID, (item, PauseItem(100, "sentence"))).with_identity()
    p2 = RenderPlan(SOURCE, PROFILE_ID, (item, PauseItem(200, "sentence"))).with_identity()
    assert p1.plan_id != p2.plan_id


def test_plan_producer_version_does_not_change_plan_identity() -> None:
    value = RenderPlan(
        SOURCE, PROFILE_ID, (speech(),), producer=Producer("utterplan", "7.0.0")
    ).with_identity()
    other = replace(
        value, producer=Producer("utterplan", "7.9.0"), plan_id=None
    ).with_identity()
    assert other.plan_id == value.plan_id
