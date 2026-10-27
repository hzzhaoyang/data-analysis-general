"""Strict input and reproducible payload identity shared by fixed methods."""
import hashlib
import json
import math
from pathlib import Path


class InputError(ValueError):
    pass


def keys(value, required, optional=()):
    if not isinstance(value, dict):
        raise InputError("expected an object")
    missing = set(required) - value.keys()
    unknown = value.keys() - set(required) - set(optional)
    if missing:
        raise InputError("missing fields: " + ", ".join(sorted(missing)))
    if unknown:
        raise InputError("unknown fields: " + ", ".join(sorted(unknown)))


def nonempty(value, field):
    if not isinstance(value, str) or not value.strip():
        raise InputError(field + " must be a nonempty string")


def positive_int(value, field):
    if type(value) is not int or value <= 0:
        raise InputError(field + " must be a positive integer")


def finite_tree(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise InputError("nonfinite numeric input")
    if isinstance(value, dict):
        for item in value.values():
            finite_tree(item)
    elif isinstance(value, list):
        for item in value:
            finite_tree(item)


def payload_hash(value):
    finite_tree(value)
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False).encode()).hexdigest()


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate JSON key: " + key)
        result[key] = value
    return result


def reject_constant(value):
    raise InputError("nonfinite JSON value: " + value)


def load(path):
    result = json.loads(Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=strict_object, parse_constant=reject_constant)
    finite_tree(result)
    return result
