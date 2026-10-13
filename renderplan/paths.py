"""Pure lexical path validators for shared RenderPlan handoff contracts."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from .errors import ValidationError


def validate_safe_basename(value: str, *, field: str = "path") -> str:
    """Return a safe single POSIX basename or raise ``ValidationError``."""
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field} must be a non-empty safe basename")
    path = PurePosixPath(value)
    if (
        value in {".", ".."}
        or "\x00" in value
        or "/" in value
        or "\\" in value
        or bool(PureWindowsPath(value).drive)
        or path.name != value
        or path.as_posix() != value
    ):
        raise ValidationError(f"{field} must be a safe single basename")
    return value


def validate_posix_relative_reference(
    value: str, *, allow_parent: bool = True, field: str = "path"
) -> str:
    """Validate normalized relative POSIX spelling, optionally allowing leading ``..``."""
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field} must be a non-empty relative POSIX reference")
    path = PurePosixPath(value)
    invalid = (
        "\x00" in value
        or "\\" in value
        or path.is_absolute()
        or bool(PureWindowsPath(value).drive)
        or not path.parts
        or path.as_posix() != value
        or value == "."
    )
    stack: list[str] = []
    for part in path.parts:
        if part == "..":
            if not allow_parent or (stack and stack[-1] != ".."):
                invalid = True
            else:
                stack.append(part)
        else:
            stack.append(part)
    if invalid:
        suffix = (
            "relative POSIX reference"
            if allow_parent
            else "relative path contained inside its base directory (POSIX syntax)"
        )
        raise ValidationError(f"{field} must be a normalized {suffix}")
    return value


def validate_contained_relative_path(value: str, *, field: str = "path") -> str:
    """Validate a normalized relative POSIX path that cannot escape its base directory."""
    return validate_posix_relative_reference(value, allow_parent=False, field=field)


__all__ = [
    "validate_contained_relative_path",
    "validate_posix_relative_reference",
    "validate_safe_basename",
]
