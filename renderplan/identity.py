from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import ValidationError


def _plain(value: Any) -> Any:
    if hasattr(value, "identity_payload"):
        return _plain(value.identity_payload())
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"Unsupported canonical identity value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _plain(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_id(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def require_sha256_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise ValidationError(f"{field} must be a sha256:<64 hex> identity")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise ValidationError(f"{field} must contain lowercase/uppercase hexadecimal") from exc
    return value


def require_nonempty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string")
    return value


def require_toml_value(value: Any, field: str = "value") -> Any:
    """Validate the small JSON/TOML-safe subset used for opaque binding/options data."""
    if isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError(f"{field} must not contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError(f"{field} keys must be strings")
            require_nonempty(key, f"{field} key")
            require_toml_value(item, f"{field}.{key}")
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            require_toml_value(item, f"{field}[{index}]")
        return value
    raise ValidationError(f"{field} contains unsupported TOML value {type(value).__name__}")
