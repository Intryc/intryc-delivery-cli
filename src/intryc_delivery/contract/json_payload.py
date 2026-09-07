"""Strict customer JSON decoding before semantic validation or persistence."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from typing import Any


MAX_JSON_DEPTH = 100


class InvalidJSONDocument(ValueError):
    """The document is invalid customer content, not a source system failure."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidJSONDocument("JSON object keys must be unique")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise InvalidJSONDocument("JSON numbers must be finite")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise InvalidJSONDocument("JSON numbers must be finite")
    return parsed


def _validate_text(value: str) -> None:
    if "\x00" in value:
        raise InvalidJSONDocument("JSON strings must not contain U+0000")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidJSONDocument(
            "JSON strings must not contain unpaired Unicode surrogates"
        ) from exc


def parse_json_object(content: bytes) -> dict[str, Any]:
    """Decode one bounded, UTF-8 JSON object safe for downstream JSONB storage.

    A document may contain up to 100 nested objects/arrays, counting the root.
    Error messages deliberately exclude customer keys, values, and source text.
    """
    try:
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except InvalidJSONDocument:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise InvalidJSONDocument("Object must contain valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise InvalidJSONDocument("JSON document must contain an object")

    pending: list[tuple[Iterator[Any], int]] = [(iter((payload,)), 1)]
    while pending:
        children, depth = pending[-1]
        try:
            value = next(children)
        except StopIteration:
            pending.pop()
            continue
        if isinstance(value, (dict, list)):
            if depth > MAX_JSON_DEPTH:
                raise InvalidJSONDocument(
                    f"JSON must not exceed {MAX_JSON_DEPTH} nested objects or arrays"
                )
            if isinstance(value, dict):
                for key in value:
                    _validate_text(key)
                pending.append((iter(value.values()), depth + 1))
            else:
                pending.append((iter(value), depth + 1))
        elif isinstance(value, str):
            _validate_text(value)
    return payload
