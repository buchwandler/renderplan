from __future__ import annotations

from pathlib import Path

import pytest

from renderplan import (
    CodecError,
    ValidationError,
    load_profile,
    load_profile_for_reidentification,
)


def test_reidentification_loader_ignores_only_stored_profile_id(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "examples/af_heart.renderprofile.toml"
    content = source.read_text(encoding="utf-8")

    # Replace the complete stored digest without relying on its current value.
    lines = content.splitlines()
    id_line = next(index for index, line in enumerate(lines) if line.startswith("profile_id ="))
    lines[id_line] = 'profile_id = "sha256:' + "0" * 64 + '"'
    path = tmp_path / "stale.renderprofile.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_profile(path)

    profile = load_profile_for_reidentification(path)
    assert profile.profile_id is None
    repaired = profile.with_identity()
    assert repaired.profile_id == repaired.computed_id()


def test_reidentification_loader_still_rejects_malformed_toml(tmp_path: Path) -> None:
    path = tmp_path / "invalid.renderprofile.toml"
    path.write_text("not = [valid", encoding="utf-8")

    with pytest.raises(CodecError):
        load_profile_for_reidentification(path)
