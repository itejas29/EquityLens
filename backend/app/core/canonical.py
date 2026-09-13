"""Canonical serialization, content hashes, and first-difference diagnostics.

Two uses, one definition: the strategy verdict is content-hashed with it, and
the determinism gate compares two backtest runs with it. They must agree on what
"the same output" means, so they share this module rather than each rolling
their own json.dumps.

What canonical means here, and why each rule exists:

- Keys sorted, no whitespace: dict insertion order is incidental.
- Floats serialized as-is, never rounded. A determinism check that rounds can
  pass while two runs disagree in the 12th digit, and a difference that small is
  still a real sign that something unordered fed the arithmetic. If a difference
  turns out to be legitimate noise, that is a finding to investigate and then
  document, not a reason to round it away.
- Non-finite floats become the strings "NaN" / "Infinity" / "-Infinity". JSON
  has no spelling for them, and NaN != NaN would otherwise make two identical
  runs compare unequal.
- Decimal -> its exact string. Money is Decimal in this codebase, and
  float(Decimal) would reintroduce the binary error that made money Decimal.
- date / datetime -> ISO 8601.
- set / frozenset -> sorted list. A set has no order to preserve; sorting it is
  the only way to serialize it canonically.
- tuple -> list. Tuples and lists carry the same order.
"""

import dataclasses
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def to_canonical(value: Any) -> Any:
    """Recursively convert `value` into plain JSON-safe types, canonically."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_canonical(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): to_canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_canonical(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((to_canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, Decimal):
        return str(value)
    # datetime before date: datetime is a subclass of date.
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    # numpy scalars and similar: anything exposing .item() is a boxed primitive.
    if hasattr(value, "item") and callable(value.item):
        return to_canonical(value.item())
    raise TypeError(f"cannot canonicalize {type(value).__name__}: {value!r}")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        to_canonical(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,  # to_canonical already encoded them; a raw NaN here is a bug
    ).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True)
class Difference:
    """The first place two canonical structures disagree.

    `artifact` is the top-level key (e.g. "trade_log"), `record` the index of
    the first differing element inside it when it is a list, and `field` the key
    that differed inside that record. `path` is the full location for anything
    nested deeper than that.
    """

    path: str
    artifact: str | None
    record: int | None
    field: str | None
    run_one: Any
    run_two: Any

    def describe(self) -> str:
        return (
            f"first difference at {self.path}\n"
            f"  artifact: {self.artifact}\n"
            f"  record:   {self.record}\n"
            f"  field:    {self.field}\n"
            f"  run one:  {self.run_one!r}\n"
            f"  run two:  {self.run_two!r}"
        )


_MISSING = object()


def first_difference(one: Any, two: Any) -> Difference | None:
    """Walk two structures in canonical order; return where they first differ."""
    return _walk(to_canonical(one), to_canonical(two), [])


def _walk(a: Any, b: Any, path: list) -> Difference | None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            av, bv = a.get(key, _MISSING), b.get(key, _MISSING)
            if av is _MISSING or bv is _MISSING:
                return _make(path + [key], av, bv)
            found = _walk(av, bv, path + [key])
            if found:
                return found
        return None
    if isinstance(a, list) and isinstance(b, list):
        for i in range(max(len(a), len(b))):
            if i >= len(a) or i >= len(b):
                return _make(path + [i], a[i] if i < len(a) else _MISSING, b[i] if i < len(b) else _MISSING)
            found = _walk(a[i], b[i], path + [i])
            if found:
                return found
        return None
    # Compare types too: 1 and 1.0 are == in Python but serialize differently,
    # and a run that emits one where the other emits the other is not identical.
    if type(a) is not type(b) or a != b:
        return _make(path, a, b)
    return None


def _make(path: list, a: Any, b: Any) -> Difference:
    rendered = "".join(f"[{p}]" if isinstance(p, int) else (f".{p}" if i else str(p)) for i, p in enumerate(path))
    artifact = path[0] if path and isinstance(path[0], str) else None
    record = path[1] if len(path) > 1 and isinstance(path[1], int) else None
    field = next((p for p in path[2:] if isinstance(p, str)), None) if record is not None else (
        path[1] if len(path) > 1 and isinstance(path[1], str) else None
    )
    missing = lambda v: "<absent>" if v is _MISSING else v  # noqa: E731
    return Difference(rendered or "<root>", artifact, record, field, missing(a), missing(b))
