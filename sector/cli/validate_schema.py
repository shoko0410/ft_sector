"""Validate schema contract for taxonomy separation."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

REQUIRED_TAXONOMY_FIELDS = (
    "native_taxonomy_label",
    "icb_proxy_level1",
    "icb_proxy_level3",
)
FORBIDDEN_MIXED_FIELDS = {
    "taxonomy_label",
    "mixed_taxonomy",
    "mixed_taxonomy_label",
    "icb_or_native_label",
}


def _load_contract(file_path: Path) -> dict[str, object]:
    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"unable to read schema file: {exc}") from exc

    try:
        payload_obj = cast(object, json.loads(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid YAML/JSON content: {exc}") from exc

    if not isinstance(payload_obj, dict):
        raise ValueError("schema root must be an object")
    return cast(dict[str, object], payload_obj)


def _extract_fields(payload: Mapping[str, object]) -> list[str]:
    output_schema_obj = payload.get("output_schema")
    if not isinstance(output_schema_obj, dict):
        raise ValueError("output_schema must be an object")
    output_schema = cast(dict[str, object], output_schema_obj)

    fields_obj = output_schema.get("fields")
    if not isinstance(fields_obj, list):
        raise ValueError("output_schema.fields must be a list of strings")
    fields_list = cast(list[object], fields_obj)
    for item_obj in fields_list:
        if not isinstance(item_obj, str):
            raise ValueError("output_schema.fields must be a list of strings")
    return cast(list[str], fields_list)


def _validate_taxonomy_separation(fields: list[str]) -> None:
    field_set = set(fields)
    mixed_fields = sorted(field_set.intersection(FORBIDDEN_MIXED_FIELDS))
    if mixed_fields:
        joined = ", ".join(mixed_fields)
        raise ValueError(
            f"proxy/native must be separate; found mixed taxonomy field(s): {joined}"
        )

    missing_fields = [name for name in REQUIRED_TAXONOMY_FIELDS if name not in field_set]
    if missing_fields:
        joined = ", ".join(missing_fields)
        raise ValueError(
            f"proxy/native must be separate; missing required field(s): {joined}"
        )


def validate_schema_file(file_path: Path) -> list[str]:
    payload = _load_contract(file_path)
    fields = _extract_fields(payload)
    _validate_taxonomy_separation(fields)
    return fields


def _parse_file_arg(argv: list[str]) -> Path:
    if len(argv) != 2 or argv[0] != "--file":
        raise ValueError("usage: python -m sector.cli.validate_schema --file <path>")
    return Path(argv[1])


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv

    try:
        file_path = _parse_file_arg(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        fields = validate_schema_file(file_path)
    except ValueError as exc:
        print(f"Schema validation failed: {exc}", file=sys.stderr)
        return 1

    print("Schema contract valid")
    for field in REQUIRED_TAXONOMY_FIELDS:
        if field in fields:
            print(field)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
