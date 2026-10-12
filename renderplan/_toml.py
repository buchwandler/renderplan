from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(value: str) -> str:
    return value if _BARE_KEY.fullmatch(value) else json.dumps(value, ensure_ascii=False)


def _path(parts: tuple[str, ...]) -> str:
    return ".".join(_key(part) for part in parts)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    raise TypeError(f"unsupported TOML scalar {type(value).__name__}")


def _is_aot(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, Mapping) for item in value)


def _emit_mapping(lines: list[str], mapping: Mapping[str, Any], prefix: tuple[str, ...], *, header: bool) -> None:
    scalars: list[tuple[str, Any]] = []
    tables: list[tuple[str, Mapping[str, Any]]] = []
    arrays: list[tuple[str, list[Mapping[str, Any]]]] = []

    for raw_key, value in mapping.items():
        key = str(raw_key)
        if isinstance(value, Mapping):
            tables.append((key, value))
        elif _is_aot(value):
            arrays.append((key, value))  # type: ignore[arg-type]
        else:
            scalars.append((key, value))

    if header:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"[{_path(prefix)}]")

    for key, value in scalars:
        lines.append(f"{_key(key)} = {_value(value)}")

    for key, table in tables:
        _emit_mapping(lines, table, prefix + (key,), header=True)

    for key, entries in arrays:
        path = prefix + (key,)
        for entry in entries:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"[[{_path(path)}]]")
            _emit_mapping(lines, entry, path, header=False)


def dumps(mapping: Mapping[str, Any]) -> str:
    lines: list[str] = []
    _emit_mapping(lines, mapping, (), header=False)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"
