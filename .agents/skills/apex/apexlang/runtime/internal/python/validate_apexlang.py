#!/usr/bin/env python3
"""Validate APEXLang DSL contracts and layout rules."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from functools import wraps
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

from validator_common import (
    COMPONENT_ATTRIBUTES_PATH,
    LOG_ROOT,
    ROOT,
    collect_targets,
    display_path,
    issue_to_record,
    line_no,
    load_runtime_component_map,
    write_report,
)

SCHEMA_PATH = COMPONENT_ATTRIBUTES_PATH
INLINE_BLOCK_CHAR_LIMIT = 4000
DEFAULT_REPORT = LOG_ROOT / "apexlang-dsl-report.json"
APP_ROOT_ALLOWED_ENTRIES = {
    ".apex",
    "application.apx",
    "deployments",
    "page-groups.apx",
    "pages",
    "shared-components",
    "supporting-objects",
}
APP_ROOT_FORBIDDEN_TEMPLATE_ARTIFACTS = {
    "README.md",
    "base-app-structure._common.md",
    "base-app-structure._index.md",
    "base-app-structure.registry.json",
}
SMART_FILTER_ALLOWED_RESULTS_REGION_TYPES = {
    "classicReport",
    "interactiveReport",
    "interactiveGrid",
    "cards",
    "contentRow",
    "metricCard",
}
SMART_FILTER_FORBIDDEN_RESULTS_REGION_TYPES = {
    "map",
    "smartFilters",
    "facetedSearch",
}
IMAGE_UPLOAD_LEGACY_SETTINGS = {
    "storageType",
    "displayAs",
    "allowMultipleFiles",
    "maxFileSize",
    "displayDownloadLink",
    "downloadLinkText",
    "purgeFilesAt",
    "dropzoneTitle",
    "dropzoneDescription",
    "maxWidth",
    "maxHeight",
    "allowCropping",
    "aspectRatio",
    "customAspectRatio",
    "captureUsing",
    "previewSize",
}
IMAGE_UPLOAD_LEGACY_SOURCE_PROPERTIES = {
    "mimeTypeColumn",
    "filenameColumn",
    "blobLastUpdatedColumn",
}
PROJECTION_COVERAGE_REGION_TYPES = {
    "classicReport",
    "interactiveReport",
    "interactiveGrid",
    "contentRow",
    "metricCard",
}
LOB_COMPARISON_RULE_ID = "SQL_PLSQL_LOB_COMPARISON_KEY_FORBIDDEN_001"
LOB_COMPARISON_REMEDIATION = (
    "raw LOB expressions can raise ORA-22848: cannot use BLOB type as comparison key; "
    "use scalar keys such as PK/FK, filename, MIME type, charset, last-updated timestamp, "
    "modeled checksum/hash, or dbms_lob.getlength(<lob_expr>) for file size"
)
CONFIG_BUILD_OPTION_BLOCK_META = {
    "allowedProperties": ["buildOption"],
}
LintRunner = Callable[["LintContext"], list[str]]


@dataclass
class LintContext:
    """Shared context for one validator target."""

    path: Path
    text: str
    schema: dict[str, Any]
    validation_context: dict[str, Any] = field(default_factory=dict)
    runtime_component_map: dict[str, Any] | None = None
    cache: dict[str, Any] = field(default_factory=dict)


def load_schema() -> dict:
    """Load the validator schema and attach runtime compiler metadata when available."""
    try:
        data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to parse schema: {SCHEMA_PATH} ({exc})") from exc

    if not isinstance(data, dict) or not isinstance(data.get("components"), dict):
        raise RuntimeError("component-attributes.json must contain a top-level 'components' object")
    runtime_component_map = load_runtime_component_map()
    if runtime_component_map:
        data["_runtimeComponentMap"] = runtime_component_map
        data["_runtimeComponentMapSource"] = "query-valid-props"
    else:
        data["_runtimeComponentMap"] = None
        data["_runtimeComponentMapSource"] = "component-attributes-only"
    return data


def find_component_blocks(text: str, keyword: str) -> list[tuple[int, str, str]]:
    """Find named parenthesized APEXLang component blocks by keyword."""
    results: list[tuple[int, str, str]] = []
    pattern = re.compile(rf"^[ \t]*{re.escape(keyword)}\s+([A-Za-z0-9_$-]+)\s*\(", re.MULTILINE)
    for match in pattern.finditer(text):
        start = match.start()
        name = match.group(1)
        depth = 0
        for idx in range(start, len(text)):
            ch = text[idx]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    results.append((start, name, text[start : idx + 1]))
                    break
    return results


def find_named_brace_blocks(text: str) -> list[tuple[int, str, str]]:
    """Find named brace blocks while respecting quoted strings."""
    results: list[tuple[int, str, str]] = []
    pattern = re.compile(r"(?m)^[ \t]*([A-Za-z][A-Za-z0-9]*)\s*\{")
    for match in pattern.finditer(text):
        start = match.start()
        name = match.group(1)
        depth = 0
        in_string = False
        for idx in range(match.end() - 1, len(text)):
            ch = text[idx]
            if ch == '"' and (idx == 0 or text[idx - 1] != "\\"):
                in_string = not in_string
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    results.append((start, name, text[start : idx + 1]))
                    break
    return results


def extract_item_type(block: str) -> str | None:
    """Extract an item type value from an item block when present."""
    match = re.search(r"(?m)^\s*type\s*:\s*([A-Za-z0-9_/-]+)\s*$", block)
    return match.group(1) if match else None


def region_schema_key(region_type: str) -> str:
    """Map a region type to its component schema key."""
    if region_type == "themeTemplateComponent/contentRow":
        return "contentRow"
    if region_type == "themeTemplateComponent/metricCard":
        return "metricCard"
    return region_type


def extract_top_level_blocks(block: str) -> dict[str, tuple[int, str]]:
    """Return first-level nested brace blocks keyed by block name."""
    body_start = block.find("\n")
    body = block[body_start:] if body_start != -1 else block
    mapping: dict[str, tuple[int, str]] = {}
    for offset, name, sub_block in find_named_brace_blocks(body):
        paren_depth, brace_depth = nesting_depth(body, offset)
        if paren_depth == 0 and brace_depth == 0 and name not in mapping:
            mapping[name] = (offset + body_start, sub_block)
    return mapping


def block_body(block: str) -> tuple[int, str]:
    """Return the inner body text and its offset for a brace block."""
    body_start = block.find("\n")
    if body_start == -1:
        return len(block), ""
    return body_start + 1, block[body_start + 1 :]


def nesting_depth(text: str, idx: int) -> tuple[int, int]:
    """Calculate brace and parenthesis depth at a character offset."""
    paren_depth = 0
    brace_depth = 0
    in_string = False

    for pos, ch in enumerate(text[:idx]):
        if ch == '"' and (pos == 0 or text[pos - 1] != "\\"):
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth = max(0, paren_depth - 1)
        elif ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth = max(0, brace_depth - 1)

    return paren_depth, brace_depth


def find_immediate_component_blocks(block: str, keyword: str) -> list[tuple[int, str, str]]:
    """Find child component blocks that are immediate children of a block."""
    body_offset, body = block_body(block)
    results: list[tuple[int, str, str]] = []
    for start, name, child_block in find_component_blocks(body, keyword):
        paren_depth, brace_depth = nesting_depth(body, start)
        if paren_depth == 0 and brace_depth == 0:
            results.append((body_offset + start, name, child_block))
    return results


def find_immediate_named_brace_blocks(block: str, block_name: str) -> list[tuple[int, str]]:
    """Find named brace blocks that are immediate children of a parenthesized component block."""
    body_offset, body = block_body(block)
    results: list[tuple[int, str]] = []
    for start, name, child_block in find_named_brace_blocks(body):
        if name != block_name:
            continue
        paren_depth, brace_depth = nesting_depth(body, start)
        if paren_depth == 0 and brace_depth == 0:
            results.append((body_offset + start, child_block))
    return results


def parse_int(value: str | None) -> int | None:
    """Parse an integer string and return None for invalid values."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def is_template_base_app_structure_path(path: Path) -> bool:
    """Return whether a path belongs to the template-family base-app-structure tree."""
    parts = path.resolve().parts
    return "templates" in parts and "base-app-structure" in parts


def is_app_root(path: Path) -> bool:
    """Return whether a directory looks like a generated app root."""
    return path.is_dir() and (
        (path / "application.apx").exists() or (path / ".apex" / "apexlang.json").exists()
    )


def resolve_app_root(path: Path) -> Path | None:
    """Resolve the nearest generated app root for a file or directory."""
    current = path.resolve()
    if current.is_file():
        current = current.parent

    while True:
        if is_template_base_app_structure_path(current):
            return None
        if is_app_root(current):
            return current
        if current.parent == current:
            return None
        current = current.parent


def collect_app_roots(paths: list[str]) -> list[Path]:
    """Collect generated app roots from explicit paths or from the default applications tree."""
    app_roots: set[Path] = set()
    if paths:
        for raw in paths:
            path = Path(raw)
            if not path.exists():
                continue
            if path.is_dir():
                resolved_root = resolve_app_root(path)
                if resolved_root:
                    app_roots.add(resolved_root)
                    continue
                for candidate in sorted(path.rglob("application.apx")):
                    app_root = resolve_app_root(candidate.parent)
                    if app_root:
                        app_roots.add(app_root)
            else:
                app_root = resolve_app_root(path)
                if app_root:
                    app_roots.add(app_root)
        return sorted(app_roots)

    applications_root = ROOT / "applications"
    if not applications_root.exists():
        return []
    for candidate in sorted(applications_root.rglob("application.apx")):
        app_root = resolve_app_root(candidate.parent)
        if app_root:
            app_roots.add(app_root)
    return sorted(app_roots)


def lint_app_root_contract(app_root: Path) -> list[str]:
    """Validate top-level generated app-root contents against the runtime-artifact allowlist."""
    issues: list[str] = []
    if is_template_base_app_structure_path(app_root) or not app_root.exists() or not app_root.is_dir():
        return issues

    for entry in sorted(app_root.iterdir(), key=lambda item: item.name):
        entry_name = entry.name
        if entry_name in APP_ROOT_FORBIDDEN_TEMPLATE_ARTIFACTS:
            issues.append(
                f"{display_path(entry)}:1: APP_TEMPLATE_ARTIFACT_LEAK_001 "
                f"generated app root contains forbidden template artifact '{entry_name}'"
            )
            continue
        if entry_name == "apex-exports":
            issues.append(
                f"{display_path(entry)}:1: APP_TEMPLATE_ARTIFACT_LEAK_001 "
                "generated app root must not retain 'apex-exports'; move export backups to "
                f"'artifacts/apex-exports/{app_root.name}/'"
            )
            continue
        if entry_name not in APP_ROOT_ALLOWED_ENTRIES:
            issues.append(
                f"{display_path(entry)}:1: APP_TEMPLATE_ARTIFACT_LEAK_001 "
                f"generated app root contains top-level entry outside runtime allowlist '{entry_name}'"
            )

    return issues


def layout_properties(layout_block: str) -> dict[str, tuple[str, int]]:
    """Extract layout property values with offsets from a layout block."""
    return {
        prop_name: (prop_value, prop_offset)
        for prop_name, prop_value, prop_offset in extract_property_values(layout_block)
    }


def is_equal_width_explicit_group(group: list[dict[str, object]]) -> bool:
    """Return whether a group uses explicit equal-width grid coordinates."""
    if len(group) < 2:
        return False
    spans = [region["column_span"] for region in group]
    columns = [region["column"] for region in group]
    if any(span is None or column is None for span, column in zip(spans, columns)):
        return False
    first_column = columns[0]
    first_span = spans[0]
    if first_column != 1 or first_span is None:
        return False
    if any(span != first_span for span in spans):
        return False
    expected_columns = [1 + idx * first_span for idx in range(len(group))]
    return columns == expected_columns


def has_explicit_coordinates(component: dict[str, object]) -> bool:
    """Return whether a component declares complete grid coordinates."""
    return component["column"] is not None or component["column_span"] is not None


def is_allowed_asymmetric_mixed_row(row: list[dict[str, object]]) -> bool:
    """Return whether a mixed row matches the canonical narrow-lead split recipe."""
    if len(row) != 2:
        return False

    first, second = row
    if first["kind"] != "region" or second["kind"] != "region":
        return False
    if first["column"] is not None or second["column"] is not None or second["column_span"] is not None:
        return False
    if first["column_span"] not in (3, 4):
        return False
    return second["start_new_row"] == "false"


def component_scope_label(component: dict[str, object]) -> str:
    """Build a concise label for diagnostics about a component scope."""
    scope_type = component["scope_type"]
    scope_name = component["scope_name"]
    scope_slot = component["scope_slot"]

    if scope_type == "page-slot":
        return f"page slot '{scope_name}'"
    if scope_type == "nested-region":
        return f"nested region scope parent '{scope_name}' slot '{scope_slot}'"
    if scope_type == "item-region":
        return f"item scope region '{scope_name}' slot '{scope_slot}'"
    if scope_type == "button-region":
        return f"button scope region '{scope_name}' slot '{scope_slot}'"
    return f"layout scope '{scope_name}'"


def infer_scope_rows(components: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    """Group components into inferred layout rows from explicit coordinates."""
    rows: list[list[dict[str, object]]] = []
    current_row: list[dict[str, object]] = []

    for component in sorted(components, key=lambda entry: (int(entry["sequence"]), int(entry["layout_offset"]))):
        if not current_row:
            current_row = [component]
            continue

        same_row = False
        if component["start_new_row"] == "false":
            same_row = True
        elif component["column"] is not None and int(component["column"]) > 1:
            same_row = True
        elif is_equal_width_explicit_group(current_row + [component]):
            same_row = True

        if same_row:
            current_row.append(component)
        else:
            rows.append(current_row)
            current_row = [component]

    if current_row:
        rows.append(current_row)

    return rows


def add_scoped_component(
    scoped_components: dict[tuple[str, str, str], list[dict[str, object]]],
    *,
    page_name: str,
    component_kind: str,
    component_name: str,
    component_start: int,
    layout_offset: int,
    props: dict[str, tuple[str, int]],
    scope_type: str,
    scope_name: str,
    scope_slot: str,
    fallback_sequence: int,
) -> None:
    """Append a component summary to the active layout scope."""
    start_new_row = props.get("startNewRow", ("", 0))[0]
    column = parse_int(props.get("column", ("", 0))[0] or None)
    column_span = parse_int(props.get("columnSpan", ("", 0))[0] or None)
    sequence = parse_int(props.get("sequence", ("", 0))[0] or None)

    scoped_components.setdefault((scope_type, scope_name, scope_slot), []).append(
        {
            "page_name": page_name,
            "kind": component_kind,
            "name": component_name,
            "layout_offset": component_start + layout_offset,
            "layout_props": props,
            "sequence": sequence if sequence is not None else fallback_sequence,
            "start_new_row": start_new_row,
            "column": column,
            "column_span": column_span,
            "scope_type": scope_type,
            "scope_name": scope_name,
            "scope_slot": scope_slot,
        }
    )


def lint_layout_scopes(path: Path, text: str) -> list[str]:
    """Validate layout scope and grid-coordinate consistency for an APEXLang file."""
    issues: list[str] = []

    for page_start, page_name, page_block in find_component_blocks(text, "page"):
        scoped_components: dict[tuple[str, str, str], list[dict[str, object]]] = {}

        for index, (region_offset, region_name, region_block) in enumerate(
            find_immediate_component_blocks(page_block, "region")
        ):
            top_level_blocks = extract_top_level_blocks(region_block)
            layout_meta = top_level_blocks.get("layout")
            if not layout_meta:
                continue
            layout_offset, layout_block = layout_meta
            props = layout_properties(layout_block)
            slot = props.get("slot", ("", 0))[0]
            parent_region = props.get("parentRegion", ("", 0))[0]
            if parent_region:
                add_scoped_component(
                    scoped_components,
                    page_name=page_name,
                    component_kind="region",
                    component_name=region_name,
                    component_start=page_start + region_offset,
                    layout_offset=layout_offset,
                    props=props,
                    scope_type="nested-region",
                    scope_name=parent_region,
                    scope_slot=slot or "SUB_REGIONS",
                    fallback_sequence=(index + 1) * 10,
                )
            elif slot:
                add_scoped_component(
                    scoped_components,
                    page_name=page_name,
                    component_kind="region",
                    component_name=region_name,
                    component_start=page_start + region_offset,
                    layout_offset=layout_offset,
                    props=props,
                    scope_type="page-slot",
                    scope_name=slot,
                    scope_slot=slot,
                    fallback_sequence=(index + 1) * 10,
                )

        for index, (item_offset, item_name, item_block) in enumerate(find_immediate_component_blocks(page_block, "pageItem")):
            top_level_blocks = extract_top_level_blocks(item_block)
            layout_meta = top_level_blocks.get("layout")
            if not layout_meta:
                continue
            layout_offset, layout_block = layout_meta
            props = layout_properties(layout_block)
            region = props.get("region", ("", 0))[0]
            slot = props.get("slot", ("", 0))[0] or "BODY"
            if not region:
                continue
            add_scoped_component(
                scoped_components,
                page_name=page_name,
                component_kind="pageItem",
                component_name=item_name,
                component_start=page_start + item_offset,
                layout_offset=layout_offset,
                props=props,
                scope_type="item-region",
                scope_name=region,
                scope_slot=slot,
                fallback_sequence=(index + 1) * 10,
            )

        for index, (button_offset, button_name, button_block) in enumerate(find_immediate_component_blocks(page_block, "button")):
            top_level_blocks = extract_top_level_blocks(button_block)
            layout_meta = top_level_blocks.get("layout")
            if not layout_meta:
                continue
            layout_offset, layout_block = layout_meta
            props = layout_properties(layout_block)
            region = props.get("region", ("", 0))[0]
            slot = props.get("slot", ("", 0))[0] or "BODY"
            if not region:
                continue
            add_scoped_component(
                scoped_components,
                page_name=page_name,
                component_kind="button",
                component_name=button_name,
                component_start=page_start + button_offset,
                layout_offset=layout_offset,
                props=props,
                scope_type="button-region",
                scope_name=region,
                scope_slot=slot,
                fallback_sequence=(index + 1) * 10,
            )

        for components in scoped_components.values():
            for row in infer_scope_rows(components):
                first = row[0]
                scope_label = component_scope_label(first)
                first_component_label = f"{first['kind']} '{first['name']}'"

                if first["start_new_row"] == "false":
                    issues.append(
                        f"{display_path(path)}:{line_no(text, int(first['layout_offset']))}: "
                        f"LAYOUT_RULE_ROW_START page '{page_name}' {scope_label} {first_component_label} must omit "
                        "layout.startNewRow on the first component in a row"
                    )

                if len(row) < 2:
                    continue

                inferred_equal_width = is_equal_width_explicit_group(row) or not any(
                    has_explicit_coordinates(component) for component in row
                )

                if inferred_equal_width:
                    for component in row[1:]:
                        if component["start_new_row"] != "false":
                            issues.append(
                                f"{display_path(path)}:{line_no(text, int(component['layout_offset']))}: "
                                f"LAYOUT_RULE_FLOW page '{page_name}' {scope_label} {component['kind']} "
                                f"'{component['name']}' must set layout.startNewRow: false for equal-width siblings"
                            )

                if is_equal_width_explicit_group(row):
                    for component in row:
                        for prop_name in ("column", "columnSpan"):
                            if prop_name not in component["layout_props"]:
                                continue
                            _value, prop_offset = component["layout_props"][prop_name]
                            issues.append(
                                f"{display_path(path)}:{line_no(text, int(component['layout_offset']) + prop_offset)}: "
                                f"LAYOUT_RULE_EQUAL_WIDTH page '{page_name}' {scope_label} {component['kind']} "
                                f"'{component['name']}' should omit layout.{prop_name} and rely on sequence plus "
                                "startNewRow: false for equal-width rows"
                            )

                explicit_spans = [int(component["column_span"]) for component in row if component["column_span"] is not None]
                if explicit_spans and sum(explicit_spans) > 12:
                    issues.append(
                        f"{display_path(path)}:{line_no(text, int(first['layout_offset']))}: "
                        f"LAYOUT_RULE_ROW_SPAN page '{page_name}' {scope_label} row starting at {first_component_label} "
                        f"exceeds the 12-column grid within that scope (sum={sum(explicit_spans)})"
                    )

                has_explicit = any(has_explicit_coordinates(component) for component in row)
                has_implicit = any(not has_explicit_coordinates(component) for component in row)
                if has_explicit and has_implicit and not is_allowed_asymmetric_mixed_row(row):
                    issues.append(
                        f"{display_path(path)}:{line_no(text, int(first['layout_offset']))}: "
                        f"LAYOUT_RULE_MIXED page '{page_name}' {scope_label} mixes implicit-flow and explicit-grid "
                        f"placement within the same row starting at {first_component_label}"
                    )

    return issues


def extract_property_names(block: str) -> list[tuple[str, int]]:
    """Extract top-level property names and offsets from a block."""
    props: list[tuple[str, int]] = []
    for match in re.finditer(r"(?m)^\s*([A-Za-z][A-Za-z0-9]*)\s*:", block):
        props.append((match.group(1), match.start()))
    return props


def extract_property_values(block: str) -> list[tuple[str, str, int]]:
    """Extract property values and offsets from a block body."""
    props: list[tuple[str, str, int]] = []
    line_offset = 0
    in_fence = False

    for line in block.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            line_offset += len(line)
            continue

        if in_fence:
            line_offset += len(line)
            continue

        match = re.match(r"^\s*([A-Za-z][A-Za-z0-9]*)\s*:\s*([^\n]+)$", line)
        if match:
            props.append((match.group(1), match.group(2).strip(), line_offset + match.start()))

        line_offset += len(line)
    return props


def extract_immediate_property_values(block: str) -> list[tuple[str, str, int]]:
    """Extract immediate property values without descending into nested blocks."""
    declaration_end = block.find("(")
    if declaration_end == -1:
        return []
    body_start = block.find("\n", declaration_end)
    if body_start == -1:
        return []
    body_offset = body_start + 1
    body = block[body_offset:]
    props: list[tuple[str, str, int]] = []
    for prop_name, prop_value, prop_offset in extract_property_values(body):
        paren_depth, brace_depth = nesting_depth(body, prop_offset)
        if paren_depth == 0 and brace_depth == 0:
            props.append((prop_name, prop_value, body_offset + prop_offset))
    return props


def extract_immediate_brace_property_values(block: str) -> list[tuple[str, str, int]]:
    """Extract immediate brace-property values without descending into nested braces."""
    props: list[tuple[str, str, int]] = []
    for prop_name, prop_value, prop_offset in extract_property_values(block):
        paren_depth, brace_depth = nesting_depth(block, prop_offset)
        if paren_depth == 0 and brace_depth == 1:
            props.append((prop_name, prop_value, prop_offset))
    return props


def extract_immediate_brace_property_names(block: str) -> list[tuple[str, int]]:
    """Extract immediate brace-property names, including multiline properties such as sqlQuery:."""
    props: list[tuple[str, int]] = []
    for prop_name, prop_offset in extract_property_names(block):
        paren_depth, brace_depth = nesting_depth(block, prop_offset)
        if paren_depth == 0 and brace_depth == 1:
            props.append((prop_name, prop_offset))
    return props


def normalize_value(value: str) -> str:
    """Normalize scalar text for comparison with schema values."""
    normalized = value.strip().rstrip(",")
    if normalized.startswith('"') and normalized.endswith('"') and len(normalized) >= 2:
        normalized = normalized[1:-1]
    return normalized.strip().lower()


def expected_value_text(value: object) -> str:
    """Render schema values for readable issue messages."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


BUTTON_TEMPLATE_OPTION_EMITTED_VALUES = {
    "t-Button--desktopHideIcon",
    "t-Button--gapBottom",
    "t-Button--gapRight",
    "t-Button--hoverIconPush",
    "t-Button--hoverIconSpin",
    "t-Button--iconLeft",
    "t-Button--iconRight",
    "t-Button--link",
    "t-Button--mobileHideLabel",
    "t-Button--noUI",
    "t-Button--padLeft",
    "t-Button--padTop",
    "t-Button--pillStart",
    "t-Button--primary",
    "t-Button--simple",
    "t-Button--stretch",
    "t-Button--success",
    "t-Button--tiny",
}

BUTTON_TEMPLATE_OPTION_CANONICAL_BY_NORMALIZED = {
    normalize_value(value): value for value in BUTTON_TEMPLATE_OPTION_EMITTED_VALUES
}

BUTTON_TEMPLATE_OPTION_ALIAS_MAP = {
    "desktophideicon": "t-Button--desktopHideIcon",
    "gapbottom": "t-Button--gapBottom",
    "gapright": "t-Button--gapRight",
    "hide-icon-on-desktop": "t-Button--desktopHideIcon",
    "hide-label-on-mobile": "t-Button--mobileHideLabel",
    "hover-icon-push": "t-Button--hoverIconPush",
    "hover-icon-spin": "t-Button--hoverIconSpin",
    "hovericonpush": "t-Button--hoverIconPush",
    "hovericonspin": "t-Button--hoverIconSpin",
    "icon-left": "t-Button--iconLeft",
    "icon-right": "t-Button--iconRight",
    "iconleft": "t-Button--iconLeft",
    "iconright": "t-Button--iconRight",
    "left": "t-Button--iconLeft",
    "link": "t-Button--link",
    "mobilehidelabel": "t-Button--mobileHideLabel",
    "no-ui": "t-Button--noUI",
    "noui": "t-Button--noUI",
    "pad-left": "t-Button--padLeft",
    "pad-top": "t-Button--padTop",
    "padleft": "t-Button--padLeft",
    "padtop": "t-Button--padTop",
    "pill-start": "t-Button--pillStart",
    "pillstart": "t-Button--pillStart",
    "primary": "t-Button--primary",
    "push": "t-Button--hoverIconPush",
    "right": "t-Button--iconRight",
    "simple": "t-Button--simple",
    "spin": "t-Button--hoverIconSpin",
    "stretch": "t-Button--stretch",
    "success": "t-Button--success",
    "tiny": "t-Button--tiny",
}

CALENDAR_LEGACY_SETTING_ALIASES = {
    "displayCol": "displayColumn",
    "startDateCol": "startDateColumn",
    "endDateCol": "endDateColumn",
    "allDayEventCol": "allDayEventColumn",
    "pkCol": "pkColumn",
}

CALENDAR_ADDITIONAL_VIEW_VALUES = {"list", "navigation"}

DYNAMIC_ACTION_ALLOWED_EVENTS = {
    "apexafterclosecanceldialog",
    "apexafterclosedialog",
    "apexafterrefresh",
    "apexbeforepagesubmit",
    "apexbeforerefresh",
    "apexdoubletap",
    "apexpan",
    "apexpress",
    "apexselectionchange",
    "apexswipe",
    "apextap",
    "change",
    "click",
    "custom",
    "dblclick",
    "focusin",
    "focusout",
    "input",
    "item/geocodedAddress/apexgeocoderresponse",
    "item/geocodedAddress/apexgeocoderselection",
    "item/markdownEditor/markdownified",
    "item/shuttle/shuttlechangeorder",
    "keydown",
    "keypress",
    "keyup",
    "load",
    "mousedown",
    "mouseenter",
    "mouseleave",
    "mousemove",
    "mouseup",
    "ready",
    "region/calendar/apexcalendardateselect",
    "region/calendar/apexcalendareventselect",
    "region/calendar/apexcalendarviewchange",
    "region/cards/tablemodelviewpagechange",
    "region/facetedSearch/facetsafterremovechart",
    "region/facetedSearch/facetsbeforeaddchart",
    "region/facetedSearch/facetschange",
    "region/interactiveGrid/apexbeginrecordedit",
    "region/interactiveGrid/gridpagechange",
    "region/interactiveGrid/interactivegridmodechange",
    "region/interactiveGrid/interactivegridreportchange",
    "region/interactiveGrid/interactivegridsave",
    "region/interactiveGrid/interactivegridselectionchange",
    "region/interactiveGrid/interactivegridviewchange",
    "region/map/spatialmapchanged",
    "region/map/spatialmapclick",
    "region/map/spatialmapinitialized",
    "region/map/spatialmapobjectclick",
    "region/smartFilters/facetschange",
    "region/tree/treeviewselectionchange",
    "resize",
    "scroll",
    "select",
    "unload",
}


def is_button_template_family_path(path: Path) -> bool:
    """Return whether a path belongs to the references/policies button template family."""
    normalized_path = display_path(path).replace("\\", "/").lower()
    return "/templates/buttons/" in normalized_path


def extract_template_option_entries(block_text: str) -> list[tuple[str, int]]:
    """Extract templateOptions entries from an appearance-like block."""
    entries: list[tuple[str, int]] = []
    array_match = re.search(r"(?ms)templateOptions\s*:\s*\[(.*?)\]", block_text)
    if array_match:
        body = array_match.group(1)
        line_offset = 0
        for line in body.splitlines(keepends=True):
            stripped = line.strip()
            if not stripped:
                line_offset += len(line)
                continue
            search_start = 0
            for part in [segment.strip() for segment in stripped.split(",") if segment.strip()]:
                part_offset = line.find(part, search_start)
                if part_offset == -1:
                    part_offset = line.find(part)
                entries.append((part, array_match.start(1) + line_offset + max(part_offset, 0)))
                search_start = max(part_offset, 0) + len(part)
            line_offset += len(line)
        return entries

    for prop_name, prop_value, prop_offset in extract_property_values(block_text):
        if prop_name == "templateOptions":
            entries.append((prop_value, prop_offset))
    return entries


def lint_button_template_option_values(
    *,
    issues: list[str],
    path: Path,
    text: str,
    component_start: int,
    component_label: str,
    block_offset: int,
    block_text: str,
    template_mode: bool,
) -> None:
    """Validate button appearance.templateOptions values against the canonical emitted-value contract."""
    issue_prefix = "DSL_TEMPLATE_VALUE" if template_mode else "DSL_RULE_VALUE"

    for token, token_offset in extract_template_option_entries(block_text):
        cleaned = token.strip().rstrip(",")
        if not cleaned or ("{{" in cleaned and "}}" in cleaned):
            continue
        if cleaned == "#DEFAULT#":
            continue

        normalized = normalize_value(cleaned)
        canonical = BUTTON_TEMPLATE_OPTION_CANONICAL_BY_NORMALIZED.get(normalized)
        alias_target = BUTTON_TEMPLATE_OPTION_ALIAS_MAP.get(normalized)
        absolute_offset = component_start + block_offset + token_offset

        if alias_target is not None:
            issues.append(
                f"{display_path(path)}:{line_no(text, absolute_offset)}: "
                f"{issue_prefix} {component_label} appearance.templateOptions must use canonical emitted value "
                f"'{alias_target}' instead of '{cleaned}'"
            )
            continue

        if canonical is not None:
            if cleaned != canonical:
                issues.append(
                    f"{display_path(path)}:{line_no(text, absolute_offset)}: "
                    f"{issue_prefix} {component_label} appearance.templateOptions must use canonical emitted value "
                    f"'{canonical}' instead of '{cleaned}'"
                )
            continue

        issues.append(
            f"{display_path(path)}:{line_no(text, absolute_offset)}: "
            f"{issue_prefix} {component_label} appearance.templateOptions must use an accepted canonical emitted "
            f"button value; got '{cleaned}'"
        )


def lint_button_template_option_contract(path: Path, text: str, *, template_mode: bool) -> list[str]:
    """Validate button appearance.templateOptions blocks in templates and final .apx files."""
    issues: list[str] = []

    for start, button_name, block in find_component_blocks(text, "button"):
        top_level_blocks = extract_top_level_blocks(block)
        appearance_meta = top_level_blocks.get("appearance")
        if not appearance_meta:
            continue
        block_offset, block_text = appearance_meta
        component_label = f"button '{button_name}'"
        lint_button_template_option_values(
            issues=issues,
            path=path,
            text=text,
            component_start=start,
            component_label=component_label,
            block_offset=block_offset,
            block_text=block_text,
            template_mode=template_mode,
        )

    return issues


def lint_button_template_option_inventory(path: Path, text: str) -> list[str]:
    """Validate button template-option inventories use canonical emitted values instead of static_id aliases."""
    issues: list[str] = []
    if not is_button_template_family_path(path):
        return issues

    for match in re.finditer(r"static_id\s*=\s*([A-Za-z0-9-]+)", text):
        alias = match.group(1)
        canonical = BUTTON_TEMPLATE_OPTION_ALIAS_MAP.get(normalize_value(alias))
        if canonical is None:
            continue
        issues.append(
            f"{display_path(path)}:{line_no(text, match.start(1))}: "
            f"DSL_TEMPLATE_VALUE button template-option inventory must use canonical emitted value "
            f"'{canonical}' instead of static_id '{alias}'"
        )

    return issues


def clean_scalar_value(value: str) -> str:
    """Remove wrapping quotes and whitespace from scalar DSL values."""
    cleaned = value.strip().rstrip(",")
    if cleaned.startswith('"') and cleaned.endswith('"') and len(cleaned) >= 2:
        cleaned = cleaned[1:-1]
    return cleaned.strip()


def extract_fenced_property_body(block: str, prop_name: str) -> str | None:
    """Return the fenced body for a multiline property such as sqlQuery:."""
    pattern = re.compile(
        rf"(?ms)^\s*{re.escape(prop_name)}\s*:\s*```[A-Za-z0-9_-]*\s*\n(.*?)^\s*```"
    )
    match = pattern.search(block)
    if not match:
        return None
    return match.group(1).strip()


def extract_property_object_block(block: str, prop_name: str) -> tuple[int, str] | None:
    """Return a property object block such as `item: { ... }` with its offset."""
    pattern = re.compile(rf"(?m)^[ \t]*{re.escape(prop_name)}\s*:\s*\{{")
    match = pattern.search(block)
    if not match:
        return None
    open_brace = block.find("{", match.start(), match.end())
    if open_brace == -1:
        return None

    depth = 0
    in_string = False
    for idx in range(open_brace, len(block)):
        ch = block[idx]
        if ch == '"' and (idx == 0 or block[idx - 1] != "\\"):
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return match.start(), block[match.start() : idx + 1]
    return None


def contains_sql_order_by_clause(sql_text: str) -> bool:
    """Return whether SQL text contains an ORDER BY clause outside quotes/comments."""
    stripped = strip_sql_comments(sql_text)
    in_single_quote = False
    in_double_quote = False
    idx = 0
    lower = stripped.lower()

    while idx < len(stripped):
        char = stripped[idx]
        next_char = stripped[idx + 1] if idx + 1 < len(stripped) else ""

        if in_single_quote:
            if char == "'" and next_char == "'":
                idx += 2
                continue
            if char == "'":
                in_single_quote = False
            idx += 1
            continue

        if in_double_quote:
            if char == '"':
                in_double_quote = False
            idx += 1
            continue

        if char == "'":
            in_single_quote = True
            idx += 1
            continue

        if char == '"':
            in_double_quote = True
            idx += 1
            continue

        if lower.startswith("order", idx):
            prev_char = stripped[idx - 1] if idx > 0 else " "
            after_order = idx + 5
            next_boundary = stripped[after_order] if after_order < len(stripped) else " "
            if prev_char.isalnum() or prev_char == "_" or next_boundary.isalnum() or next_boundary == "_":
                idx += 1
                continue
            by_match = re.match(r"\s+by\b", lower[after_order:])
            if by_match:
                return True

        idx += 1

    return False


def strip_sql_comments(sql_text: str) -> str:
    """Remove simple SQL comments to make lightweight select-list parsing more reliable."""
    without_block_comments = re.sub(r"(?s)/\*.*?\*/", " ", sql_text)
    return re.sub(r"(?m)--.*$", "", without_block_comments)


def split_sql_top_level(sql_text: str, delimiter: str) -> list[str]:
    """Split SQL text on a delimiter only when not nested inside parentheses or quotes."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_single_quote = False
    in_double_quote = False
    idx = 0

    while idx < len(sql_text):
        char = sql_text[idx]
        next_char = sql_text[idx + 1] if idx + 1 < len(sql_text) else ""

        if in_single_quote:
            current.append(char)
            if char == "'" and next_char == "'":
                current.append(next_char)
                idx += 2
                continue
            if char == "'":
                in_single_quote = False
            idx += 1
            continue

        if in_double_quote:
            current.append(char)
            if char == '"':
                in_double_quote = False
            idx += 1
            continue

        if char == "'":
            in_single_quote = True
            current.append(char)
            idx += 1
            continue

        if char == '"':
            in_double_quote = True
            current.append(char)
            idx += 1
            continue

        if char == "(":
            depth += 1
            current.append(char)
            idx += 1
            continue

        if char == ")":
            depth = max(depth - 1, 0)
            current.append(char)
            idx += 1
            continue

        if char == delimiter and depth == 0:
            parts.append("".join(current).strip())
            current = []
            idx += 1
            continue

        current.append(char)
        idx += 1

    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def extract_top_level_select_list(sql_text: str) -> list[str] | None:
    """Extract the top-level select-list expressions for simple SELECT queries."""
    stripped = strip_sql_comments(sql_text).strip().rstrip(";")
    if not stripped or re.match(r"(?is)^with\b", stripped):
        return None

    lower = stripped.lower()
    depth = 0
    in_single_quote = False
    in_double_quote = False
    select_start = -1
    from_start = -1
    idx = 0

    while idx < len(stripped):
        char = stripped[idx]
        next_char = stripped[idx + 1] if idx + 1 < len(stripped) else ""

        if in_single_quote:
            if char == "'" and next_char == "'":
                idx += 2
                continue
            if char == "'":
                in_single_quote = False
            idx += 1
            continue

        if in_double_quote:
            if char == '"':
                in_double_quote = False
            idx += 1
            continue

        if char == "'":
            in_single_quote = True
            idx += 1
            continue

        if char == '"':
            in_double_quote = True
            idx += 1
            continue

        if char == "(":
            depth += 1
            idx += 1
            continue

        if char == ")":
            depth = max(depth - 1, 0)
            idx += 1
            continue

        if depth == 0 and lower.startswith("select", idx):
            prev_char = stripped[idx - 1] if idx > 0 else " "
            next_boundary = stripped[idx + 6] if idx + 6 < len(stripped) else " "
            if not (prev_char.isalnum() or prev_char == "_") and not (next_boundary.isalnum() or next_boundary == "_"):
                select_start = idx + 6
                idx += 6
                continue

        if depth == 0 and select_start != -1 and lower.startswith("from", idx):
            prev_char = stripped[idx - 1] if idx > 0 else " "
            next_boundary = stripped[idx + 4] if idx + 4 < len(stripped) else " "
            if not (prev_char.isalnum() or prev_char == "_") and not (next_boundary.isalnum() or next_boundary == "_"):
                from_start = idx
                break

        idx += 1

    if select_start == -1 or from_start == -1 or from_start <= select_start:
        return None

    select_list = stripped[select_start:from_start].strip()
    if not select_list:
        return None
    return split_sql_top_level(select_list, ",")


def normalize_sql_identifier(value: str) -> str:
    """Normalize SQL identifiers for case-insensitive alias comparisons."""
    cleaned = clean_scalar_value(value)
    if cleaned.startswith('"') and cleaned.endswith('"') and len(cleaned) >= 2:
        cleaned = cleaned[1:-1]
    return cleaned.strip().lower()


def extract_select_expression_identifier(expression: str) -> str | None:
    """Best-effort alias/name extraction for simple top-level select expressions."""
    expr = expression.strip().rstrip(",")
    if not expr:
        return None

    alias_match = re.search(r'(?is)\bas\s+("?[A-Za-z][A-Za-z0-9_$#]*"?)\s*$', expr)
    if alias_match:
        return alias_match.group(1)

    trailing_alias_match = re.search(
        r'(?is)^(.*?)(?<![.(])\s+("?[A-Za-z][A-Za-z0-9_$#]*"?)\s*$',
        expr,
    )
    if trailing_alias_match:
        prefix = trailing_alias_match.group(1).strip()
        candidate = trailing_alias_match.group(2)
        if prefix and not re.fullmatch(r'"?[A-Za-z][A-Za-z0-9_$#]*"?(?:\."?[A-Za-z][A-Za-z0-9_$#]*"?)*', prefix):
            return candidate

    qualified_name_match = re.fullmatch(r'"?([A-Za-z][A-Za-z0-9_$#]*)"?(?:\."?([A-Za-z][A-Za-z0-9_$#]*)"?)?', expr)
    if qualified_name_match:
        return qualified_name_match.group(2) or qualified_name_match.group(1)

    return None


def normalize_lob_identifier(value: str) -> str:
    """Return the unqualified normalized identifier used for LOB-name heuristics."""
    cleaned = value.strip().strip('"')
    if "." in cleaned:
        cleaned = cleaned.split(".")[-1].strip().strip('"')
    return cleaned.lower()


def collect_blob_column_mappings(text: str) -> set[str]:
    """Collect explicit Cards/media raw BLOB aliases from DSL media.blobColumn mappings."""
    mappings: set[str] = set()
    for match in re.finditer(r"(?mi)^\s*blobColumn\s*:\s*([A-Za-z][A-Za-z0-9_$#]*)\s*$", text):
        mappings.add(normalize_lob_identifier(match.group(1)))
    return mappings


def likely_lob_identifier(identifier: str, known_lob_identifiers: set[str]) -> bool:
    """Return whether an identifier is likely to represent a raw LOB expression."""
    normalized = normalize_lob_identifier(identifier)
    if normalized in known_lob_identifiers:
        return True
    return bool(
        re.search(r"(?i)(?:^|_)(?:blob|clob|nclob|bfile)$", normalized)
        or re.search(r"(?i)_(?:image|file|content)$", normalized)
    )


def mask_dbms_lob_getlength(sql_text: str) -> str:
    """Mask scalar LOB-length calls so their raw LOB argument is not treated as a comparison key."""
    pattern = re.compile(r"(?is)\bdbms_lob\s*\.\s*getlength\s*\(")
    chars = list(sql_text)
    for match in pattern.finditer(sql_text):
        depth = 0
        idx = match.end() - 1
        while idx < len(sql_text):
            char = sql_text[idx]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    idx += 1
                    break
            idx += 1
        for pos in range(match.start(), min(idx, len(chars))):
            chars[pos] = " "
    return "".join(chars)


def find_lob_identifier_in_expression(expression: str, known_lob_identifiers: set[str]) -> tuple[str, int] | None:
    """Return the first likely raw LOB identifier in an expression after scalar wrappers are masked."""
    masked = mask_dbms_lob_getlength(expression)
    for match in re.finditer(
        r'(?i)(?<![.])"?[A-Za-z][A-Za-z0-9_$#]*"?(?:\."?[A-Za-z][A-Za-z0-9_$#]*"?)*',
        masked,
    ):
        token = match.group(0)
        if likely_lob_identifier(token, known_lob_identifiers):
            return token, match.start()
    return None


def sql_clause_body_pattern(clause: str, stop_clauses: str) -> re.Pattern[str]:
    """Build a lightweight clause body regex for SQL hygiene checks."""
    return re.compile(rf"(?is)\b{clause}\b(?P<body>.*?)(?=\b(?:{stop_clauses})\b|$)")


def inspect_lob_key_terms(
    *,
    issues: list[str],
    path: Path,
    text: str,
    snippet: str,
    snippet_base: int,
    body: str,
    body_base: int,
    context: str,
    known_lob_identifiers: set[str],
) -> None:
    """Inspect comma-separated key terms and report likely raw LOB usage."""
    for term in split_sql_top_level(body, ","):
        lob_meta = find_lob_identifier_in_expression(term, known_lob_identifiers)
        if not lob_meta:
            continue
        lob_identifier, rel_in_term = lob_meta
        term_offset = body.lower().find(term.lower().strip())
        rel_offset = max(term_offset, 0) + rel_in_term
        issues.append(
            f"{display_path(path)}:{line_no(text, snippet_base + body_base + rel_offset)}: "
            f"{LOB_COMPARISON_RULE_ID} {context} must not use raw LOB expression `{lob_identifier}` as a comparison key - "
            f"{LOB_COMPARISON_REMEDIATION}"
        )


def inspect_lob_comparison_predicates(
    *,
    issues: list[str],
    path: Path,
    text: str,
    snippet: str,
    snippet_base: int,
    body: str,
    body_base: int,
    context: str,
    known_lob_identifiers: set[str],
) -> None:
    """Inspect WHERE/HAVING/ON predicate bodies for direct raw LOB comparisons."""
    masked = mask_dbms_lob_getlength(body)
    token_pattern = re.compile(
        r'(?i)(?<![.])"?[A-Za-z][A-Za-z0-9_$#]*"?(?:\."?[A-Za-z][A-Za-z0-9_$#]*"?)*'
    )
    for match in token_pattern.finditer(masked):
        token = match.group(0)
        if not likely_lob_identifier(token, known_lob_identifiers):
            continue
        before = masked[max(0, match.start() - 48) : match.start()]
        after = masked[match.end() : match.end() + 48]
        compared_after = re.match(r"(?is)^\s*(=|<>|!=|<=|>=|<|>|\blike\b|\bin\s*\(|\bbetween\b)", after)
        compared_before = re.search(r"(?is)(=|<>|!=|<=|>=|<|>|\blike\b)\s*$", before)
        if not compared_after and not compared_before:
            continue
        issues.append(
            f"{display_path(path)}:{line_no(text, snippet_base + body_base + match.start())}: "
            f"{LOB_COMPARISON_RULE_ID} {context} must not compare raw LOB expression `{token}` directly - "
            f"{LOB_COMPARISON_REMEDIATION}"
        )


def lint_sql_lob_comparison_keys(path: Path, text: str) -> list[str]:
    """Reject obvious raw LOB expressions in SQL/PLSQL comparison-key positions."""
    issues: list[str] = []
    known_lob_identifiers = collect_blob_column_mappings(text)
    stop_clauses = (
        r"from|where|group\s+by|having|order\s+by|fetch|offset|union(?:\s+all)?|intersect|minus|"
        r"connect\s+by|start\s+with|model|returning"
    )

    def inspect_sql(snippet: str, snippet_base: int, label: str) -> None:
        sql = strip_sql_comments(snippet)
        if not sql.strip():
            return

        distinct_match = re.search(r"(?is)\bselect\s+distinct\s+(?P<body>.*?)(?=\bfrom\b|$)", sql)
        if distinct_match:
            inspect_lob_key_terms(
                issues=issues,
                path=path,
                text=text,
                snippet=snippet,
                snippet_base=snippet_base,
                body=distinct_match.group("body"),
                body_base=distinct_match.start("body"),
                context=f"{label} SELECT DISTINCT",
                known_lob_identifiers=known_lob_identifiers,
            )

        for clause, context in (
            (r"group\s+by", "GROUP BY"),
            (r"order\s+by", "ORDER BY"),
        ):
            for match in sql_clause_body_pattern(clause, stop_clauses).finditer(sql):
                inspect_lob_key_terms(
                    issues=issues,
                    path=path,
                    text=text,
                    snippet=snippet,
                    snippet_base=snippet_base,
                    body=match.group("body"),
                    body_base=match.start("body"),
                    context=f"{label} {context}",
                    known_lob_identifiers=known_lob_identifiers,
                )

        for over_match in re.finditer(r"(?is)\bover\s*\((?P<body>.*?)\)", sql):
            over_body = over_match.group("body")
            analytic_stop = r"partition\s+by|order\s+by|rows|range|groups"
            for clause, context in (
                (r"partition\s+by", "analytic PARTITION BY"),
                (r"order\s+by", "analytic ORDER BY"),
            ):
                for clause_match in sql_clause_body_pattern(clause, analytic_stop).finditer(over_body):
                    inspect_lob_key_terms(
                        issues=issues,
                        path=path,
                        text=text,
                        snippet=snippet,
                        snippet_base=snippet_base,
                        body=clause_match.group("body"),
                        body_base=over_match.start("body") + clause_match.start("body"),
                        context=f"{label} {context}",
                        known_lob_identifiers=known_lob_identifiers,
                    )

        if re.search(r"(?is)\b(?:union(?:\s+all)?|intersect|minus)\b", sql):
            for part in re.split(r"(?is)\b(?:union(?:\s+all)?|intersect|minus)\b", sql):
                part_offset = sql.find(part)
                select_list = extract_top_level_select_list(part)
                if not select_list:
                    continue
                body = ", ".join(select_list)
                inspect_lob_key_terms(
                    issues=issues,
                    path=path,
                    text=text,
                    snippet=snippet,
                    snippet_base=snippet_base,
                    body=body,
                    body_base=max(part_offset, 0),
                    context=f"{label} set operation SELECT list",
                    known_lob_identifiers=known_lob_identifiers,
                )

        predicate_patterns = [
            (r"where", "WHERE comparison predicate", stop_clauses),
            (r"having", "HAVING comparison predicate", stop_clauses),
            (
                r"on",
                "JOIN ON comparison predicate",
                r"(?:inner|left|right|full|cross)?\s*join|where|group\s+by|having|order\s+by|fetch|offset|union(?:\s+all)?|intersect|minus",
            ),
        ]
        for clause, context, stops in predicate_patterns:
            for match in sql_clause_body_pattern(clause, stops).finditer(sql):
                inspect_lob_comparison_predicates(
                    issues=issues,
                    path=path,
                    text=text,
                    snippet=snippet,
                    snippet_base=snippet_base,
                    body=match.group("body"),
                    body_base=match.start("body"),
                    context=f"{label} {context}",
                    known_lob_identifiers=known_lob_identifiers,
                )

    for fence_match in re.finditer(r"(?ms)```(?P<lang>sql|plsql)\s*(?P<body>.*?)\s*```", text):
        inspect_sql(
            fence_match.group("body"),
            fence_match.start("body"),
            f"fenced {fence_match.group('lang').upper()}",
        )

    for prop_match in re.finditer(r"(?m)^\s*(plsqlFunctionBody|plsqlExpression)\s*:\s*(.+)$", text):
        inspect_sql(prop_match.group(2), prop_match.start(2), prop_match.group(1))

    return issues


def normalize_component_reference(value: str) -> str:
    """Normalize APEX component references such as @alias for metadata lookup."""
    cleaned = clean_scalar_value(value)
    if cleaned.startswith("@"):
        cleaned = cleaned[1:]
    return normalize_sql_identifier(cleaned)


def is_select_star_expression(expression: str) -> bool:
    """Return whether a select-list expression is a wildcard projection."""
    cleaned = expression.strip().rstrip(",")
    return bool(re.fullmatch(r'(?is)(?:"?[A-Za-z][A-Za-z0-9_$#]*"?\.)?\*', cleaned))


def parse_markdown_frontmatter(markdown: str) -> dict[str, str]:
    """Parse simple YAML-like frontmatter used by schema dictionaries."""
    match = re.match(r"(?s)^---\n(.*?)\n---\n?", markdown)
    if not match:
        return {}
    frontmatter: dict[str, str] = {}
    for line in match.group(1).splitlines():
        field_match = re.match(r"^([A-Za-z0-9_]+):\s*(.+)$", line)
        if field_match:
            frontmatter[field_match.group(1)] = field_match.group(2).strip().strip('"')
    return frontmatter


def frontmatter_bool(value: str | None) -> bool:
    """Return true for common frontmatter boolean spellings."""
    return (value or "").strip().lower() in {"true", "yes", "y", "1"}


def parse_schema_dictionary_columns(markdown: str) -> dict[str, list[str]]:
    """Parse table/view columns from an offline schema dictionary markdown body."""
    columns_by_object: dict[str, list[str]] = {}
    sections = re.split(r"\n(?=##+\s+)", markdown)
    for section in sections:
        heading_match = re.match(r"(?is)^##+\s*(table|view|object|entity)\s*:?\s*([^\n]+)", section.strip())
        if not heading_match:
            continue
        object_name = normalize_sql_identifier(heading_match.group(2))
        if not object_name:
            continue
        columns: list[str] = []
        seen: set[str] = set()
        for line in section.splitlines():
            bullet_match = re.match(r'^\s*[-*]\s*`?([A-Za-z][A-Za-z0-9_$#]*)`?(?:\s*\(([^)]+)\))?', line)
            table_match = re.match(r'^\|\s*`?([A-Za-z][A-Za-z0-9_$#]*)`?\s*\|', line)
            column_name = ""
            if bullet_match:
                column_name = bullet_match.group(1)
            elif table_match and table_match.group(1).lower() not in {"column", "name"}:
                column_name = table_match.group(1)
            normalized = normalize_sql_identifier(column_name)
            if normalized and normalized not in seen:
                seen.add(normalized)
                columns.append(column_name)
        if columns:
            columns_by_object[object_name] = columns
    return columns_by_object


def load_schema_dictionary_columns() -> dict[str, list[str]]:
    """Load known offline schema dictionary columns from env and references/policies/db."""
    columns_by_object: dict[str, list[str]] = {}
    candidate_paths: list[Path] = []

    env_paths = os.environ.get("APEXLANG_SCHEMA_DICTIONARY_PATHS", "").strip()
    if env_paths:
        candidate_paths.extend(Path(raw).expanduser() for raw in env_paths.split(os.pathsep) if raw.strip())

    index_path = ROOT / "references/policies" / "db" / "index.json"
    if index_path.exists():
        try:
            index_payload = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            index_payload = {}
        for entry in index_payload.get("schemas", []) if isinstance(index_payload, dict) else []:
            if not isinstance(entry, dict):
                continue
            rel_path = entry.get("path") or entry.get("file") or entry.get("doc_path") or entry.get("selected_schema_doc_path")
            if isinstance(rel_path, str) and rel_path.strip():
                candidate_paths.append((ROOT / rel_path).resolve() if not Path(rel_path).is_absolute() else Path(rel_path))

    for candidate in candidate_paths:
        if not candidate.exists() or not candidate.is_file():
            continue
        try:
            markdown = candidate.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        frontmatter = parse_markdown_frontmatter(markdown)
        if frontmatter:
            if frontmatter.get("status", "").lower() != "active":
                continue
            if frontmatter.get("metadata_mode", "").lower() != "offline_dictionary":
                continue
            if not frontmatter_bool(frontmatter.get("covers_columns")):
                continue
        for object_name, columns in parse_schema_dictionary_columns(markdown).items():
            columns_by_object[object_name] = columns

    return columns_by_object


def collect_rest_profiles_from_text(text: str) -> dict[str, list[str]]:
    """Collect REST data profile columns from restDataSource blocks in APEXLang text."""
    profiles: dict[str, list[str]] = {}
    for _offset, rest_name, rest_block in find_component_blocks(text, "restDataSource"):
        columns: list[str] = []
        seen: set[str] = set()
        for _col_offset, col_identifier, col_block in find_immediate_component_blocks(rest_block, "dataProfileCol"):
            col_name = col_identifier
            for prop_name, prop_value, _prop_offset in extract_immediate_property_values(col_block):
                if prop_name == "colName":
                    col_name = clean_scalar_value(prop_value)
                    break
            normalized = normalize_sql_identifier(col_name)
            if normalized and normalized not in seen:
                seen.add(normalized)
                columns.append(col_name)
        if columns:
            profiles[normalize_component_reference(rest_name)] = columns
    return profiles


def build_validation_context(targets: list[Path]) -> dict[str, Any]:
    """Build cross-file validation context used for projection coverage checks."""
    rest_profiles: dict[str, list[str]] = {}
    for target in targets:
        try:
            text = target.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rest_profiles.update(collect_rest_profiles_from_text(text))
    return {
        "schema_columns": load_schema_dictionary_columns(),
        "rest_profiles": rest_profiles,
    }


def projection_columns_from_sql(sql_query_text: str) -> tuple[list[str], str | None]:
    """Return SQL projection aliases or an error message when aliases cannot be proven."""
    select_list = extract_top_level_select_list(sql_query_text)
    if not select_list:
        return [], "SQL projection could not be resolved; use an explicit simple select list or provide metadata"

    aliases: list[str] = []
    seen: set[str] = set()
    for expression in select_list:
        if is_select_star_expression(expression):
            return [], "SQL projection uses wildcard selection; enumerate columns explicitly"
        identifier = extract_select_expression_identifier(expression)
        if not identifier:
            return [], f"SQL projection expression '{expression.strip()}' must have a resolvable alias"
        normalized = normalize_sql_identifier(identifier)
        if normalized in seen:
            return [], f"SQL projection alias '{clean_scalar_value(identifier)}' is duplicated"
        seen.add(normalized)
        aliases.append(clean_scalar_value(identifier))
    return aliases, None


def source_projection_columns(
    top_level_blocks: dict[str, tuple[int, str]],
    validation_context: dict[str, Any] | None,
) -> tuple[list[str], str | None, str]:
    """Resolve source projection columns for SQL, table, or REST-backed regions."""
    source_meta = top_level_blocks.get("source")
    if not source_meta:
        return [], None, "none"

    _source_offset, source_block = source_meta
    prop_names = {prop_name for prop_name, _prop_offset in extract_immediate_brace_property_names(source_block)}
    source_props = {
        prop_name: (prop_value, prop_offset)
        for prop_name, prop_value, prop_offset in extract_immediate_brace_property_values(source_block)
    }
    source_type_meta = source_props.get("type")
    source_type = clean_scalar_value(source_type_meta[0]).lower() if source_type_meta else ""

    if source_type == "sqlquery" or "sqlQuery" in prop_names:
        sql_query_text = extract_fenced_property_body(source_block, "sqlQuery")
        if not sql_query_text:
            return [], None, "sql"
        aliases, error = projection_columns_from_sql(sql_query_text)
        return aliases, error, "sql"

    table_meta = source_props.get("tableName")
    if table_meta or source_type == "table":
        table_name = clean_scalar_value(table_meta[0]) if table_meta else ""
        if not table_name:
            return [], "table source projection requires source.tableName", "table"
        schema_columns = (validation_context or {}).get("schema_columns", {})
        columns = schema_columns.get(normalize_sql_identifier(table_name), []) if isinstance(schema_columns, dict) else []
        if not columns:
            return [], f"table source projection for '{table_name}' requires offline schema metadata with columns", "table"
        return columns, None, "table"

    rest_meta = source_props.get("restSource")
    location_meta = source_props.get("location")
    location = clean_scalar_value(location_meta[0]).lower() if location_meta else ""
    if rest_meta or location == "restsource":
        rest_name = clean_scalar_value(rest_meta[0]) if rest_meta else ""
        if not rest_name:
            return [], "REST source projection requires source.restSource", "rest"
        rest_profiles = (validation_context or {}).get("rest_profiles", {})
        rest_reference = normalize_component_reference(rest_name)
        columns = rest_profiles.get(rest_reference, []) if isinstance(rest_profiles, dict) else []
        if not columns:
            return [], f"REST source projection for '{rest_reference or rest_name}' requires resolvable dataProfileCol metadata", "rest"
        return columns, None, "rest"

    return [], None, "none"


def projection_source_requires_columns(region_type_key: str, top_level_blocks: dict[str, tuple[int, str]]) -> bool:
    """Return whether this region family must mirror source projections with child columns."""
    if region_type_key in {"classicReport", "interactiveReport", "interactiveGrid"}:
        return source_block_is_sql_or_table_backed(top_level_blocks) or source_block_is_rest_backed(top_level_blocks)
    if region_type_key in {"contentRow", "metricCard"}:
        return content_row_display_mode(top_level_blocks) == "report" and source_block_has_data_projection(top_level_blocks)
    return False


def projection_column_is_allowed_extra(region_type_key: str, column_block: str, normalized_name: str) -> bool:
    """Return whether an emitted child column may exist outside the source projection."""
    if normalized_name.startswith("apex$"):
        return True
    if region_type_key == "classicReport":
        props = {
            prop_name: clean_scalar_value(prop_value)
            for prop_name, prop_value, _prop_offset in extract_immediate_property_values(column_block)
        }
        return props.get("derivedColumn", "N").upper() != "N"
    return False


def collect_emitted_projection_columns(region_type_key: str, region_block: str) -> dict[str, tuple[str, str, bool]]:
    """Collect emitted child column names mapped to source projection aliases."""
    emitted: dict[str, tuple[str, str, bool]] = {}
    for _column_offset, column_identifier, column_block in find_immediate_component_blocks(region_block, "column"):
        source_name = column_identifier
        column_top_level_blocks = extract_top_level_blocks(column_block)
        source_meta = column_top_level_blocks.get("source")
        if source_meta:
            _source_offset, source_block = source_meta
            source_props = {
                prop_name: (prop_value, prop_offset)
                for prop_name, prop_value, prop_offset in extract_immediate_brace_property_values(source_block)
            }
            database_column_meta = source_props.get("databaseColumn")
            if database_column_meta:
                source_name = clean_scalar_value(database_column_meta[0])
        normalized = normalize_sql_identifier(source_name)
        if not normalized:
            continue
        emitted[normalized] = (
            source_name,
            column_identifier,
            projection_column_is_allowed_extra(region_type_key, column_block, normalized),
        )
    return emitted


def lint_map_initial_position_sql_aliases(
    *,
    issues: list[str],
    path: Path,
    text: str,
    component_start: int,
    component_label: str,
    block_offset: int,
    block_text: str,
) -> None:
    """Validate SQL-driven map initial-position aliases against configured column names."""
    prop_name_offsets = {
        prop_name: prop_offset for prop_name, prop_offset in extract_immediate_brace_property_names(block_text)
    }
    scalar_props = {
        prop_name: (prop_value, prop_offset)
        for prop_name, prop_value, prop_offset in extract_immediate_brace_property_values(block_text)
    }

    type_meta = scalar_props.get("type")
    if not type_meta or clean_scalar_value(type_meta[0]).lower() != "sqlquery":
        return

    sql_query_text = extract_fenced_property_body(block_text, "sqlQuery")
    if not sql_query_text:
        return

    select_list = extract_top_level_select_list(sql_query_text)
    if not select_list:
        return

    available_aliases = {
        normalize_sql_identifier(identifier)
        for identifier in (extract_select_expression_identifier(expression) for expression in select_list)
        if identifier
    }
    if not available_aliases:
        return

    geometry_meta = scalar_props.get("geometryColumnDataType")
    geometry_type = clean_scalar_value(geometry_meta[0]).lower() if geometry_meta else ""
    expected_props: list[str] = []
    if geometry_type == "longitudelatitude":
        expected_props.extend(["initialLongitudeColumn", "initialLatitudeColumn"])
    if "initialZoomlevelColumn" in scalar_props:
        expected_props.append("initialZoomlevelColumn")

    for prop_name in expected_props:
        prop_meta = scalar_props.get(prop_name)
        if not prop_meta:
            continue
        expected_value, prop_offset = prop_meta
        normalized_expected = normalize_sql_identifier(expected_value)
        if normalized_expected in available_aliases:
            continue
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + block_offset + prop_offset)}: "
            f"DSL_RULE_VALUE {component_label} initialPositionAndZoom.{prop_name} must match a SQL select-list alias; "
            f"query must return alias '{clean_scalar_value(expected_value)}'"
        )


def translation_language_suffixes(language: str) -> list[str]:
    """Return accepted translation filename suffixes for a language code."""
    cleaned = clean_scalar_value(language).replace("_", "-").strip().lower()
    if not cleaned:
        return []

    parts = [part for part in cleaned.split("-") if part]
    if not parts:
        return []

    candidates: list[str] = []

    def add_suffix(token: str) -> None:
        if token and token not in candidates:
            candidates.append(token)

    full_underscore = "_".join(part.upper() for part in parts)
    full_hyphen = "-".join(part.upper() for part in parts)
    add_suffix(f"_{full_underscore}")
    add_suffix(f"-{full_hyphen}")
    add_suffix(f"_{''.join(part.upper() for part in parts)}")

    primary = parts[0].upper()
    add_suffix(f"_{primary}")
    add_suffix(f"-{primary}")

    return candidates


def lint_translation_text_messages(path: Path, text: str) -> list[str]:
    """Validate translation message syntax and language file naming."""
    issues: list[str] = []

    for start, component_id, block in find_component_blocks(text, "textMessage"):
        top_level_blocks = extract_top_level_blocks(block)
        message_meta = top_level_blocks.get("message")
        if not message_meta:
            continue

        message_offset, message_block = message_meta
        message_props = {
            prop_name: (prop_value, prop_offset)
            for prop_name, prop_value, prop_offset in extract_property_values(message_block)
        }
        language_meta = message_props.get("language")
        if not language_meta:
            continue

        language_value, _language_offset = language_meta
        cleaned_component_id = clean_scalar_value(component_id)
        if not cleaned_component_id:
            continue

        for suffix in translation_language_suffixes(language_value):
            if cleaned_component_id.upper().endswith(suffix):
                issues.append(
                    f"{display_path(path)}:{line_no(text, start)}: "
                    f"DSL_TRANSLATION_STATIC_ID textMessage identifier '{cleaned_component_id}' must keep the same "
                    "message key across languages; remove the language suffix and rely on message.language "
                    "to distinguish variants"
                )
                break

    return issues


SAME_APP_F_URL_PATTERN = re.compile(
    r"f\?p\s*=\s*(?:&APP_ID\.|#APP_ID#|&FLOW_ID\.|#FLOW_ID#)",
    re.IGNORECASE,
)
APEX_PAGE_GET_URL_PATTERN = re.compile(r"\bapex_page\.get_url\s*\(", re.IGNORECASE)
SUBSTITUTION_TOKEN_PATTERN = re.compile(r"#[A-Za-z][A-Za-z0-9_$-]*#")
AMP_SUBSTITUTION_TOKEN_PATTERN = re.compile(r"&([A-Za-z][A-Za-z0-9_$-]*)\.")
REPORT_TARGET_ITEMS_PATTERN = re.compile(r"items\s*:\s*\{(?P<body>.*?)\n\s*\}", re.IGNORECASE | re.DOTALL)
REPORT_TARGET_ITEM_ASSIGNMENT_PATTERN = re.compile(
    r"(?m)^\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$"
)


def is_allowed_page_or_app_substitution(token: str) -> bool:
    """Return whether an ampersand substitution token is clearly page/app/session scoped."""
    normalized = token.upper()
    if re.fullmatch(r"P\d+_[A-Z0-9_]+", normalized):
        return True
    return normalized in {
        "APP_ID",
        "APP_SESSION",
        "SESSION",
        "DEBUG",
        "REQUEST",
        "FLOW_ID",
        "APP_PAGE_ID",
    }


def is_same_app_f_url(value: str) -> bool:
    """Return whether a scalar value is a same-application f?p URL string."""
    return bool(SAME_APP_F_URL_PATTERN.search(clean_scalar_value(value)))


def lint_declarative_button_targets(path: Path, text: str) -> list[str]:
    """Reject scalar or block-style same-application button redirect targets."""
    issues: list[str] = []

    for button_start, button_name, button_block in find_component_blocks(text, "button"):
        top_level_blocks = extract_top_level_blocks(button_block)
        behavior_meta = top_level_blocks.get("behavior")
        if not behavior_meta:
            continue

        behavior_offset, behavior_block = behavior_meta
        behavior_props = {
            prop_name: (prop_value, prop_offset)
            for prop_name, prop_value, prop_offset in extract_property_values(behavior_block)
        }
        behavior_blocks = extract_top_level_blocks(behavior_block)
        action_meta = behavior_props.get("action")
        if not action_meta or clean_scalar_value(action_meta[0]) != "redirectThisApp":
            continue

        target_block_meta = behavior_blocks.get("target")
        if target_block_meta:
            target_block_offset, _target_block = target_block_meta
            issues.append(
                f"{display_path(path)}:{line_no(text, button_start + behavior_offset + target_block_offset)}: "
                f"DECLARATIVE_BUTTON_TARGET_REQUIRED button '{button_name}' action redirectThisApp must use "
                "declarative target: { page, items, clearCache, action, request } syntax; bare 'target { ... }' blocks are invalid"
            )
            continue

        target_meta = behavior_props.get("target")
        if not target_meta:
            continue

        target_value, target_offset = target_meta
        if clean_scalar_value(target_value).startswith("{"):
            continue

        issues.append(
            f"{display_path(path)}:{line_no(text, button_start + behavior_offset + target_offset)}: "
            f"DECLARATIVE_BUTTON_TARGET_REQUIRED button '{button_name}' action redirectThisApp must use "
            "declarative target: { page, items, clearCache, action, request } syntax instead of a scalar URL target"
        )

    return issues


def link_block_uses_computed_target(link_block: str) -> bool:
    """Return whether a link block targets a SQL-projected URL column by substitution token."""
    for prop_name, prop_value, _prop_offset in extract_property_values(link_block):
        if prop_name == "target" and SUBSTITUTION_TOKEN_PATTERN.search(clean_scalar_value(prop_value)):
            return True

    return False


def report_region_has_computed_url_navigation(region_block: str) -> bool:
    """Return whether a report region appears to navigate through a SQL-computed URL column."""
    for _link_offset, link_block in find_immediate_named_brace_blocks(region_block, "link"):
        if link_block_uses_computed_target(link_block):
            return True

    for _column_offset, _column_name, column_block in find_immediate_component_blocks(region_block, "column"):
        for _link_offset, link_block in find_immediate_named_brace_blocks(column_block, "link"):
            if link_block_uses_computed_target(link_block):
                return True
        column_is_link = False
        column_has_computed_target = False
        for prop_name, prop_value, _prop_offset in extract_immediate_property_values(column_block):
            if prop_name == "type" and clean_scalar_value(prop_value).lower() == "link":
                column_is_link = True
            if prop_name == "target" and SUBSTITUTION_TOKEN_PATTERN.search(clean_scalar_value(prop_value)):
                column_has_computed_target = True
        if column_is_link and column_has_computed_target:
            return True

    return False


def lint_report_link_block_target(
    *,
    issues: list[str],
    path: Path,
    text: str,
    component_start: int,
    link_offset: int,
    link_block: str,
    component_label: str,
) -> None:
    """Reject scalar same-application f?p URL targets in Classic/Interactive Report link blocks."""
    for prop_name, prop_value, prop_offset in extract_property_values(link_block):
        if prop_name == "target" and is_same_app_f_url(prop_value):
            issues.append(
                f"{display_path(path)}:{line_no(text, component_start + link_offset + prop_offset)}: "
                f"DECLARATIVE_REPORT_LINK_REQUIRED {component_label} must use declarative target "
                "{ page, items, clearCache } syntax instead of scalar f?p same-application URLs"
            )
        if prop_name == "type" and clean_scalar_value(prop_value).lower() == "url" and SAME_APP_F_URL_PATTERN.search(link_block):
            issues.append(
                f"{display_path(path)}:{line_no(text, component_start + link_offset + prop_offset)}: "
                f"DECLARATIVE_REPORT_LINK_REQUIRED {component_label} must not use type: url for same-application navigation; "
                "use declarative target { page, items, clearCache } syntax"
            )
        if prop_name == "items":
            target_items_match = REPORT_TARGET_ITEMS_PATTERN.search(link_block)
            if not target_items_match:
                continue
            target_items_body = target_items_match.group("body")
            for assignment_match in REPORT_TARGET_ITEM_ASSIGNMENT_PATTERN.finditer(target_items_body):
                _dest_item = assignment_match.group(1)
                rhs = clean_scalar_value(assignment_match.group(2))
                amp_match = AMP_SUBSTITUTION_TOKEN_PATTERN.fullmatch(rhs)
                if not amp_match:
                    continue
                token = amp_match.group(1)
                if is_allowed_page_or_app_substitution(token):
                    continue
                issues.append(
                    f"{display_path(path)}:{line_no(text, component_start + link_offset + prop_offset)}: "
                    f"REPORT_LINK_ROW_SUBSTITUTION_REQUIRED {component_label} target.items uses '&{token}.'; "
                    f"use '#{token}#' for current Classic Report / Interactive Report row values and reserve '&ITEM.' for page/app/session substitutions"
                )


def lint_declarative_report_link_targets(path: Path, text: str) -> list[str]:
    """Reject scalar same-application f?p URL targets for Classic Report and Interactive Report links."""
    issues: list[str] = []
    report_types = {"classicReport", "interactiveReport"}

    for region_start, region_name, region_block in find_component_blocks(text, "region"):
        region_type_match = re.search(r"(?m)^\s*type\s*:\s*([A-Za-z][A-Za-z0-9]*)\s*$", region_block)
        if not region_type_match:
            continue
        region_type = region_type_match.group(1)
        if region_type not in report_types:
            continue

        region_label = f"region '{region_name}' type '{region_type}' link"
        for link_offset, link_block in find_immediate_named_brace_blocks(region_block, "link"):
            lint_report_link_block_target(
                issues=issues,
                path=path,
                text=text,
                component_start=region_start,
                link_offset=link_offset,
                link_block=link_block,
                component_label=region_label,
            )

        for column_offset, column_name, column_block in find_immediate_component_blocks(region_block, "column"):
            column_label = f"column '{column_name}' in region '{region_name}' type '{region_type}' link"
            for link_offset, link_block in find_immediate_named_brace_blocks(column_block, "link"):
                lint_report_link_block_target(
                    issues=issues,
                    path=path,
                    text=text,
                    component_start=region_start + column_offset,
                    link_offset=link_offset,
                    link_block=link_block,
                    component_label=column_label,
                )

        if not report_region_has_computed_url_navigation(region_block):
            continue

        region_blocks = extract_top_level_blocks(region_block)
        source_meta = region_blocks.get("source")
        if not source_meta:
            continue

        source_offset, source_block = source_meta
        sql_match = re.search(r"(?ms)```sql\s*(.*?)\s*```", source_block)
        if not sql_match:
            continue

        url_match = APEX_PAGE_GET_URL_PATTERN.search(sql_match.group(1))
        if url_match:
            issues.append(
                f"{display_path(path)}:{line_no(text, region_start + source_offset + sql_match.start(1) + url_match.start())}: "
                f"DECLARATIVE_REPORT_LINK_REQUIRED region '{region_name}' type '{region_type}' must not use "
                "SQL-generated apex_page.get_url(...) for report navigation when declarative target syntax is available"
            )

    return issues


def lint_declarative_navigation_targets(path: Path, text: str) -> list[str]:
    """Run declarative target checks for same-app buttons and report links."""
    issues = lint_declarative_button_targets(path, text)
    issues.extend(lint_declarative_report_link_targets(path, text))
    return issues


def lint_report_column_rendering(path: Path, text: str) -> list[str]:
    """Validate report column rendering rules and declarative column settings."""
    issues: list[str] = []
    report_types = {"classicReport", "interactiveReport", "interactiveGrid"}
    html_tag_pattern = re.compile(r"<\s*(span|div|a|img|style|script|svg)\b", re.IGNORECASE)

    for region_start, region_name, region_block in find_component_blocks(text, "region"):
        region_type_match = re.search(r"(?m)^\s*type\s*:\s*([A-Za-z][A-Za-z0-9]*)\s*$", region_block)
        if not region_type_match:
            continue
        region_type = region_type_match.group(1)
        if region_type not in report_types:
            continue

        region_blocks = extract_top_level_blocks(region_block)
        source_meta = region_blocks.get("source")
        if source_meta:
            source_offset, source_block = source_meta
            sql_match = re.search(r"(?ms)```sql\s*(.*?)\s*```", source_block)
            if sql_match and html_tag_pattern.search(sql_match.group(1)):
                issues.append(
                    f"{display_path(path)}:{line_no(text, region_start + source_offset + sql_match.start(1))}: "
                    f"DSL_REPORT_SQL_HTML region '{region_name}' type '{region_type}' SQL must be data-only; move "
                    "markup to columnFormatting.htmlExpression"
                )

        for column_offset, column_name, column_block in find_immediate_component_blocks(region_block, "column"):
            column_start = region_start + column_offset
            component_label = f"column '{column_name}' in region '{region_name}' type '{region_type}'"
            column_type: str | None = None

            for prop_name, prop_value, prop_offset in extract_immediate_property_values(column_block):
                if prop_name == "type":
                    normalized_type = clean_scalar_value(prop_value).lower()
                    if normalized_type:
                        column_type = normalized_type
                if prop_name == "htmlExpression":
                    issues.append(
                        f"{display_path(path)}:{line_no(text, column_start + prop_offset)}: "
                        f"DSL_REPORT_RENDER_PROP {component_label} must not use top-level htmlExpression; use "
                        "columnFormatting.htmlExpression"
                    )

            column_blocks = extract_top_level_blocks(column_block)
            security_meta = column_blocks.get("security")
            if security_meta and is_business_app_path(path):
                security_offset, security_block = security_meta
                for prop_name, prop_value, prop_offset in extract_property_values(security_block):
                    if prop_name == "escapeSpecialChars" and clean_scalar_value(prop_value).lower() == "false":
                        issues.append(
                            f"{display_path(path)}:{line_no(text, column_start + security_offset + prop_offset)}: "
                            f"REPORT_ESCAPE_REQUIRED_001 {component_label} must not disable escaping outside approved declarative formatting"
                        )

            if "columnFormatting" not in column_blocks:
                continue

            formatting_offset, formatting_block = column_blocks["columnFormatting"]
            formatting_props = extract_property_values(formatting_block)
            prop_names = {prop_name for prop_name, _prop_value, _prop_offset in formatting_props}

            for prop_name, _prop_value, prop_offset in formatting_props:
                if prop_name != "htmlExpression":
                    issues.append(
                        f"{display_path(path)}:{line_no(text, column_start + formatting_offset + prop_offset)}: "
                        f"DSL_REPORT_RENDER_BLOCK {component_label} columnFormatting.{prop_name} is not supported; "
                        "only columnFormatting.htmlExpression is allowed"
                    )

            has_html_expression = "htmlExpression" in prop_names or bool(
                re.search(r"(?m)^\s*htmlExpression\s*:\s*$", formatting_block)
            )
            if not has_html_expression:
                issues.append(
                    f"{display_path(path)}:{line_no(text, column_start + formatting_offset)}: "
                    f"DSL_REPORT_RENDER_BLOCK {component_label} columnFormatting must define htmlExpression"
                )
                continue

            if column_type == "richText":
                issues.append(
                    f"{display_path(path)}:{line_no(text, column_start + formatting_offset)}: "
                    f"DSL_REPORT_RENDER_TYPE {component_label} must not use type: richText when "
                    "columnFormatting.htmlExpression is present; keep plain text type implicit"
                )

    return issues


def lint_classic_report_default_templates(path: Path, text: str) -> list[str]:
    """Validate the hard default template block for classic report regions."""
    issues: list[str] = []
    default_appearance_options = ["#DEFAULT#"]
    default_component_options = [
        "#DEFAULT#",
        "t-Report--stretch",
        "t-Report--altRowsDefault",
        "t-Report--rowHighlight",
    ]

    def property_value(block_text: str, prop_name: str) -> tuple[str, int] | None:
        for found_name, found_value, found_offset in extract_property_values(block_text):
            if found_name == prop_name:
                return found_value, found_offset
        return None

    def template_options(block_text: str) -> list[tuple[str, int]]:
        entries: list[tuple[str, int]] = []
        for token, token_offset in extract_template_option_entries(block_text):
            cleaned = token.strip().rstrip(",")
            if not cleaned:
                continue
            entries.append((cleaned, token_offset))
        return entries

    for region_start, region_name, region_block in find_component_blocks(text, "region"):
        if extract_item_type(region_block) != "classicReport":
            continue

        top_level_blocks = extract_top_level_blocks(region_block)
        component_label = f"region '{region_name}' type 'classicReport'"

        component_meta = top_level_blocks.get("componentAppearance")
        if component_meta:
            component_offset, component_block = component_meta
            component_template_meta = property_value(component_block, "template")
            if component_template_meta and clean_scalar_value(component_template_meta[0]) == "@/contextual-info":
                continue

        appearance_meta = top_level_blocks.get("appearance")
        if not appearance_meta:
            issues.append(
                f"{display_path(path)}:{line_no(text, region_start)}: "
                f"DSL_RULE_VALUE {component_label} must define appearance with the canonical Classic Report default template block"
            )
        else:
            appearance_offset, appearance_block = appearance_meta
            appearance_template_meta = property_value(appearance_block, "template")
            if not appearance_template_meta or clean_scalar_value(appearance_template_meta[0]) != "@/standard":
                issue_offset = appearance_offset + (
                    appearance_template_meta[1] if appearance_template_meta else 0
                )
                issues.append(
                    f"{display_path(path)}:{line_no(text, region_start + issue_offset)}: "
                    f"DSL_RULE_VALUE {component_label} appearance.template must default to '@/standard'"
                )

            appearance_options = template_options(appearance_block)
            appearance_values = [value for value, _offset in appearance_options]
            if appearance_values != default_appearance_options:
                issue_offset = appearance_offset + (appearance_options[0][1] if appearance_options else 0)
                issues.append(
                    f"{display_path(path)}:{line_no(text, region_start + issue_offset)}: "
                    f"CLASSIC_REPORT_DEFAULT_TEMPLATE_REQUIRED_001 {component_label} appearance.templateOptions "
                    "must be exactly '#DEFAULT#'"
                )

        if not component_meta:
            issues.append(
                f"{display_path(path)}:{line_no(text, region_start)}: "
                f"CLASSIC_REPORT_DEFAULT_TEMPLATE_REQUIRED_001 {component_label} must define "
                "componentAppearance with the canonical Classic Report default template block"
            )
            continue

        component_offset, component_block = component_meta
        component_template_meta = property_value(component_block, "template")
        if not component_template_meta or clean_scalar_value(component_template_meta[0]) != "@/standard":
            issue_offset = component_offset + (
                component_template_meta[1] if component_template_meta else 0
            )
            issues.append(
                f"{display_path(path)}:{line_no(text, region_start + issue_offset)}: "
                f"DSL_RULE_VALUE {component_label} componentAppearance.template must default to '@/standard' unless the region uses the documented '@/contextual-info' variant"
            )

        component_options = template_options(component_block)
        component_values = [value for value, _offset in component_options]
        if component_values != default_component_options:
            issue_offset = component_offset + (component_options[0][1] if component_options else 0)
            issues.append(
                f"{display_path(path)}:{line_no(text, region_start + issue_offset)}: "
                f"CLASSIC_REPORT_DEFAULT_TEMPLATE_REQUIRED_001 {component_label} componentAppearance.templateOptions "
                "must be exactly '#DEFAULT#', 't-Report--stretch', 't-Report--altRowsDefault', "
                "'t-Report--rowHighlight' in that order"
            )

    return issues


def lint_classic_report_hidden_column_headings(path: Path, text: str) -> list[str]:
    """Reject hidden Classic Report columns that still emit heading blocks."""
    issues: list[str] = []
    for region_start, region_name, region_block in find_component_blocks(text, "region"):
        region_type = extract_item_type(region_block)
        if region_schema_key(region_type or "") != "classicReport":
            continue
        component_label = f"region '{region_name}' type '{region_type}'"
        for column_offset, column_identifier, column_block in find_immediate_component_blocks(region_block, "column"):
            column_props = {
                prop_name: clean_scalar_value(prop_value).lower()
                for prop_name, prop_value, _prop_offset in extract_immediate_property_values(column_block)
            }
            if column_props.get("type") != "hidden":
                continue
            heading_meta = extract_top_level_blocks(column_block).get("heading")
            if not heading_meta:
                continue
            issues.append(
                f"{display_path(path)}:{line_no(text, region_start + column_offset + heading_meta[0])}: "
                f"CLASSIC_REPORT_HIDDEN_COLUMN_HEADING_FORBIDDEN_001 {component_label} column "
                f"'{column_identifier}' type 'hidden' must omit the heading block"
            )
    return issues


def lint_smart_filter_results_regions(path: Path, text: str) -> list[str]:
    """Validate Smart Filters filteredRegion targets point to compatible results regions."""
    issues: list[str] = []
    region_types: dict[str, str] = {}
    for _start, region_name, region_block in find_component_blocks(text, "region"):
        region_type = extract_item_type(region_block)
        if region_type:
            region_types[region_name] = region_schema_key(region_type)

    for region_start, region_name, region_block in find_component_blocks(text, "region"):
        region_type = extract_item_type(region_block)
        if region_schema_key(region_type or "") != "smartFilters":
            continue
        component_label = f"region '{region_name}' type '{region_type}'"
        source_meta = extract_top_level_blocks(region_block).get("source")
        if not source_meta:
            continue
        source_offset, source_block = source_meta
        source_props = {
            prop_name: (prop_value, prop_offset)
            for prop_name, prop_value, prop_offset in extract_immediate_brace_property_values(source_block)
        }
        filtered_meta = source_props.get("filteredRegion")
        if not filtered_meta:
            continue
        filtered_value, filtered_offset = filtered_meta
        filtered_region = clean_scalar_value(filtered_value).lstrip("@")
        if not filtered_region or "{{" in filtered_region:
            continue
        target_region_type = region_types.get(filtered_region)
        if target_region_type is None:
            issues.append(
                f"{display_path(path)}:{line_no(text, region_start + source_offset + filtered_offset)}: "
                f"SMART_FILTER_RESULTS_REGION_REQUIRED_001 {component_label} filteredRegion must reference an "
                "existing page results region"
            )
            continue
        if (
            target_region_type in SMART_FILTER_FORBIDDEN_RESULTS_REGION_TYPES
            or target_region_type not in SMART_FILTER_ALLOWED_RESULTS_REGION_TYPES
        ):
            issues.append(
                f"{display_path(path)}:{line_no(text, region_start + source_offset + filtered_offset)}: "
                f"SMART_FILTER_RESULTS_REGION_REQUIRED_001 {component_label} filteredRegion must reference a "
                "compatible results region and must not target maps or filter regions"
            )
    return issues


def lint_image_upload_legacy_properties(path: Path, text: str) -> list[str]:
    """Reject stale image-upload properties that the current compiler contract does not expose."""
    issues: list[str] = []
    for item_start, item_name, item_block in find_component_blocks(text, "pageItem"):
        if extract_item_type(item_block) != "imageUpload":
            continue
        component_label = f"pageItem '{item_name}' type 'imageUpload'"
        top_level_blocks = extract_top_level_blocks(item_block)
        settings_meta = top_level_blocks.get("settings")
        if settings_meta:
            settings_offset, settings_block = settings_meta
            for prop_name, _prop_value, prop_offset in extract_property_values(settings_block):
                if prop_name not in IMAGE_UPLOAD_LEGACY_SETTINGS:
                    continue
                issues.append(
                    f"{display_path(path)}:{line_no(text, item_start + settings_offset + prop_offset)}: "
                    f"IMAGE_UPLOAD_LEGACY_PROPERTY_FORBIDDEN_001 {component_label} must not emit stale settings."
                    f"{prop_name}"
                )
        source_meta = top_level_blocks.get("source")
        if source_meta:
            source_offset, source_block = source_meta
            for prop_name, _prop_value, prop_offset in extract_property_values(source_block):
                if prop_name not in IMAGE_UPLOAD_LEGACY_SOURCE_PROPERTIES:
                    continue
                issues.append(
                    f"{display_path(path)}:{line_no(text, item_start + source_offset + prop_offset)}: "
                    f"IMAGE_UPLOAD_LEGACY_PROPERTY_FORBIDDEN_001 {component_label} must not emit stale source."
                    f"{prop_name}"
                )
    return issues


def lint_static_id_where_lower(path: Path, text: str) -> list[str]:
    """Validate lower-kebab static IDs in locations that require them."""
    issues: list[str] = []
    bare_cmp_pattern = re.compile(
        r"(?i)\b(?P<col>(?:[A-Za-z][A-Za-z0-9_$]*\.)?[A-Za-z][A-Za-z0-9_$]*_static_id)\b\s*(?P<op>=(?!>)|!=|<>|in\s*\()"
    )
    wrapped_non_lower_pattern = re.compile(
        r"(?i)\b(?P<fn>upper|trim|nvl|coalesce)\s*\(\s*(?P<col>(?:[A-Za-z][A-Za-z0-9_$]*\.)?[A-Za-z][A-Za-z0-9_$]*_static_id)\s*\)\s*(?P<op>=(?!>)|!=|<>|in\s*\()"
    )
    lower_rhs_pattern = re.compile(
        r"(?is)\blower\s*\(\s*(?P<col>(?:[A-Za-z][A-Za-z0-9_$]*\.)?[A-Za-z][A-Za-z0-9_$]*_static_id)\s*\)\s*(?P<op>=(?!>)|!=|<>)\s*(?P<rhs>[^;\n]+)"
    )

    def add_issue(abs_idx: int, detail: str) -> None:
        issues.append(
            f"{display_path(path)}:{line_no(text, abs_idx)}: "
            f"STATIC_ID_WHERE_LOWER_REQUIRED_001 {detail}"
        )

    def inspect_sql_snippet(snippet: str, base_idx: int, context: str) -> None:
        for match in bare_cmp_pattern.finditer(snippet):
            add_issue(
                base_idx + match.start("col"),
                f"{context} must normalize `_static_id` comparisons with LOWER() "
                f"(found `{match.group('col')} {match.group('op').strip()}`)",
            )

        for match in wrapped_non_lower_pattern.finditer(snippet):
            add_issue(
                base_idx + match.start("fn"),
                f"{context} must use LOWER() for `_static_id` comparisons, not {match.group('fn').upper()}()",
            )

        for match in lower_rhs_pattern.finditer(snippet):
            rhs = match.group("rhs").strip()
            if not rhs.lower().startswith("lower("):
                add_issue(
                    base_idx + match.start("rhs"),
                    f"{context} equality/inequality against `_static_id` must use lower(<value_or_bind>) on RHS",
                )

    for fence_match in re.finditer(r"(?ms)```(?:sql|plsql)\s*(.*?)\s*```", text):
        inspect_sql_snippet(
            fence_match.group(1),
            fence_match.start(1),
            "fenced SQL/PLSQL block",
        )

    for prop_match in re.finditer(r"(?m)^\s*(plsqlFunctionBody|plsqlExpression)\s*:\s*(.+)$", text):
        inspect_sql_snippet(
            prop_match.group(2),
            prop_match.start(2),
            f"{prop_match.group(1)}",
        )

    return issues


def lint_inline_code_block_char_limits(path: Path, text: str) -> list[str]:
    """Ensure inline code blocks stay below the configured character limit."""
    issues: list[str] = []

    if "applications" in path.parts:
        return issues

    def sql_block_context(body_start: int, lang: str) -> tuple[str, str]:
        if lang != "sql":
            return (f"inline {lang.upper()} body", "extract to `app_process_api` (or justified package) and reference it declaratively")

        path_text = display_path(path)
        if "shared-components/ai-agents/" not in path_text:
            return ("inline SQL body", "extract to a secure view and reference it from the page")

        prefix = text[:body_start]
        tool_match = None
        for candidate in re.finditer(r"(?m)^\s*tool\s+(?P<name>[A-Za-z0-9_-]+)\s*\(", prefix):
            tool_match = candidate

        if tool_match is None:
            return ("inline SQL body", "extract to a secure view and reference it from the page")

        sql_query_idx = prefix.rfind("sqlQuery:")
        if sql_query_idx < tool_match.start():
            return ("inline SQL body", "extract to a secure view and reference it from the page")

        tool_name = tool_match.group("name")
        return (
            f"aiAgent tool `{tool_name}` settings.sqlQuery",
            "extract prompt-independent logic to secure view(s) and keep settings.sqlQuery as a short wrapper query",
        )

    for match in re.finditer(r"(?ms)```(?P<lang>sql|plsql)\s*(?P<body>.*?)\s*```", text):
        lang = match.group("lang").lower()
        body = match.group("body")
        if len(body) <= INLINE_BLOCK_CHAR_LIMIT:
            continue

        issue_id = "SQL_INLINE_BLOCK_001" if lang == "sql" else "PLSQL_INLINE_BLOCK_001"
        context_label, remedy = sql_block_context(match.start("body"), lang)
        issues.append(
            f"{display_path(path)}:{line_no(text, match.start('body'))}: "
            f"{issue_id} {context_label} exceeds {INLINE_BLOCK_CHAR_LIMIT} characters ({len(body)}) "
            f"- {remedy}"
        )

    for prop_match in re.finditer(r"(?m)^\s*(plsqlFunctionBody|plsqlExpression)\s*:\s*(.+)$", text):
        body = prop_match.group(2)
        if len(body) <= INLINE_BLOCK_CHAR_LIMIT:
            continue
        issues.append(
            f"{display_path(path)}:{line_no(text, prop_match.start(2))}: "
            f"PLSQL_INLINE_BLOCK_001 inline PL/SQL body exceeds {INLINE_BLOCK_CHAR_LIMIT} characters ({len(body)}) "
            "- extract to `app_process_api` (or justified package) and reference it declaratively"
        )

    return issues


def is_lower_kebab(value: str) -> bool:
    """Return whether a value follows lower-kebab naming."""
    return bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value))


def extract_acl_referenced_roles(text: str) -> dict[str, int]:
    """Collect ACL role references and their source line numbers."""
    refs: dict[str, int] = {}

    def add_ref(role: str, idx: int) -> None:
        cleaned = role.strip().strip("\"'")
        if cleaned:
            refs.setdefault(cleaned, idx)

    for match in re.finditer(r"(?is)role_static_id[^;\n]*", text):
        segment = match.group(0)
        for literal in re.finditer(r"'([^']+)'", segment):
            add_ref(literal.group(1), match.start() + literal.start(1))

    block_pattern = re.compile(r"(?is)authorization\s+[A-Za-z0-9_$-]+\s*\((.*?)\n\)")
    for block in block_pattern.finditer(text):
        body = block.group(1)
        if not re.search(r"(?i)\btype\s*:\s*(isInRoleOrGroup|isNotInRoleOrGroup)\b", body):
            continue
        for names_match in re.finditer(r"(?im)^\s*names\s*:\s*(.+)$", body):
            raw = names_match.group(1).strip()
            if raw.startswith("[") and raw.endswith("]"):
                raw = raw[1:-1]
            for token in [part.strip() for part in raw.split(",") if part.strip()]:
                token = token.strip("\"'")
                if token.startswith("@"):
                    token = token[1:]
                add_ref(token, block.start(1) + names_match.start(1))

    return refs


def extract_acl_declared_roles(text: str) -> dict[str, int]:
    """Collect ACL role declarations and their source line numbers."""
    declared: dict[str, int] = {}

    for match in re.finditer(r"(?im)^\s*role\s+([A-Za-z0-9_-]+)\s*\(", text):
        declared.setdefault(match.group(1), match.start(1))

    return declared


def lint_acl_role_declarations(path: Path, text: str) -> list[str]:
    """Validate that referenced ACL roles are declared exactly once."""
    issues: list[str] = []
    if path.name != "authorizations.apx" or path.parent.name != "shared-components":
        return issues

    referenced = extract_acl_referenced_roles(text)
    if not referenced:
        return issues

    roles_path = path.parent / "acl-roles.apx"
    if not roles_path.exists():
        issues.append(
            f"{display_path(path)}:1: ACL_ROLE_DECLARATION_REQUIRED_001 "
            "role-based authorization checks require shared-components/acl-roles.apx"
        )
        return issues

    roles_text = roles_path.read_text(encoding="utf-8", errors="ignore")
    declared = extract_acl_declared_roles(roles_text)

    if not declared:
        issues.append(
            f"{display_path(roles_path)}:1: ACL_ROLE_DECLARATION_REQUIRED_001 "
            "acl-roles.apx must declare at least one role when role-based checks exist"
        )

    for role, idx in sorted(referenced.items()):
        if not is_lower_kebab(role):
            issues.append(
                f"{display_path(path)}:{line_no(text, idx)}: ACL_ROLE_DECLARATION_REQUIRED_001 "
                f"referenced ACL role '{role}' must be lowercase kebab-case"
            )
        if role not in declared:
            issues.append(
                f"{display_path(path)}:{line_no(text, idx)}: ACL_ROLE_DECLARATION_REQUIRED_001 "
                f"referenced ACL role '{role}' is not declared in {display_path(roles_path)}"
            )

    for role, idx in sorted(declared.items()):
        if not is_lower_kebab(role):
            issues.append(
                f"{display_path(roles_path)}:{line_no(roles_text, idx)}: ACL_ROLE_DECLARATION_REQUIRED_001 "
                f"declared ACL role '{role}' must be lowercase kebab-case"
            )

    return issues


def is_block_meta(block_meta: object) -> bool:
    """Return whether extracted block metadata has the expected tuple shape."""
    if not isinstance(block_meta, dict):
        return False
    return any(
        key in block_meta
        for key in ("allowedProperties", "requiredProperties", "enforcedValues", "propertyEnums", "forbiddenProperties")
    )


def lint_block_properties(
    *,
    issues: list[str],
    path: Path,
    text: str,
    component_start: int,
    component_label: str,
    block_name: str,
    block_offset: int,
    block_text: str,
    block_meta: dict,
) -> None:
    """Validate required, allowed, and enumerated properties for a schema block."""
    allowed_properties = set(block_meta.get("allowedProperties", []))
    required_properties = set(block_meta.get("requiredProperties", []))
    enforced_values = block_meta.get("enforcedValues", {})
    property_enums = block_meta.get("propertyEnums", {})
    forbidden_properties = set(block_meta.get("forbiddenProperties", []))
    property_values = extract_immediate_brace_property_values(block_text)
    present_props = {prop_name for prop_name, _prop_value, _prop_offset in property_values}

    if allowed_properties:
        for prop_name, _prop_value, prop_offset in property_values:
            if prop_name in forbidden_properties:
                continue
            if prop_name not in allowed_properties:
                issues.append(
                    f"{display_path(path)}:{line_no(text, component_start + block_offset + prop_offset)}: "
                    f"DSL_RULE_PROP {component_label} {block_name}.{prop_name} is not allowed"
                )

    missing_props = sorted(required_properties - present_props)
    for prop_name in missing_props:
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + block_offset)}: "
            f"DSL_RULE_REQUIRED {component_label} must define {block_name}.{prop_name}"
        )

    for prop_name, _prop_value, prop_offset in property_values:
        if prop_name in forbidden_properties:
            issues.append(
                f"{display_path(path)}:{line_no(text, component_start + block_offset + prop_offset)}: "
                f"DSL_RULE_PROP {component_label} {block_name}.{prop_name} is not allowed"
            )

    if isinstance(enforced_values, dict):
        for prop_name, expected in enforced_values.items():
            for actual_name, actual_value, prop_offset in property_values:
                if actual_name != prop_name:
                    continue
                if normalize_value(actual_value) != normalize_value(expected_value_text(expected)):
                    issues.append(
                        f"{display_path(path)}:{line_no(text, component_start + block_offset + prop_offset)}: "
                        f"DSL_RULE_VALUE {component_label} requires {block_name}.{prop_name}: {expected_value_text(expected)}"
                    )

    if isinstance(property_enums, dict):
        for prop_name, allowed_values in property_enums.items():
            if not isinstance(allowed_values, list) or not allowed_values:
                continue
            allowed_normalized = {normalize_value(str(value)) for value in allowed_values}
            for actual_name, actual_value, prop_offset in property_values:
                if actual_name != prop_name:
                    continue
                normalized_actual = normalize_value(actual_value)
                if normalized_actual not in allowed_normalized:
                    issues.append(
                        f"{display_path(path)}:{line_no(text, component_start + block_offset + prop_offset)}: "
                        f"DSL_RULE_ENUM {component_label} {block_name}.{prop_name} must be one of: "
                        + ", ".join(str(value) for value in allowed_values)
                    )


def lint_map_layer_children(
    *,
    issues: list[str],
    path: Path,
    text: str,
    component_start: int,
    component_label: str,
    region_block: str,
    map_schema: dict[str, Any],
) -> None:
    """Validate child layer blocks inside a map region."""
    layer_schema = map_schema.get("layer")
    if not isinstance(layer_schema, dict):
        return

    seen_layer_identifiers: set[str] = set()
    for layer_offset, layer_identifier, layer_block in find_immediate_component_blocks(region_block, "layer"):
        layer_label = f"{component_label} layer '{layer_identifier}'"
        absolute_start = component_start + layer_offset

        if layer_identifier in seen_layer_identifiers:
            issues.append(
                f"{display_path(path)}:{line_no(text, absolute_start)}: "
                f"DSL_RULE_IDENTIFIER {component_label} layer identifier '{layer_identifier}' must be unique within the region"
            )
        seen_layer_identifiers.add(layer_identifier)

        layer_props = extract_immediate_property_values(layer_block)
        present_props = {prop_name for prop_name, _prop_value, _prop_offset in layer_props}
        allowed_props = set(layer_schema.get("allowedProperties", []))
        required_props = set(layer_schema.get("requiredProperties", []))

        for prop_name, _prop_value, prop_offset in layer_props:
            if allowed_props and prop_name not in allowed_props:
                issues.append(
                    f"{display_path(path)}:{line_no(text, absolute_start + prop_offset)}: "
                    f"DSL_RULE_PROP {layer_label} {prop_name} is not allowed"
                )

        for prop_name in sorted(required_props - present_props):
            issues.append(
                f"{display_path(path)}:{line_no(text, absolute_start)}: "
                f"DSL_RULE_REQUIRED {layer_label} must define property '{prop_name}'"
            )

        layer_top_level_blocks = extract_top_level_blocks(layer_block)
        allowed_layer_blocks = set(layer_schema.get("allowedBlocks", []))
        required_layer_blocks = set(layer_schema.get("requiredBlocks", []))

        for block_name in sorted(required_layer_blocks - set(layer_top_level_blocks.keys())):
            issues.append(
                f"{display_path(path)}:{line_no(text, absolute_start)}: "
                f"DSL_RULE_REQUIRED {layer_label} must define block '{block_name}'"
            )

        for block_name, (block_offset, block_text) in layer_top_level_blocks.items():
            if allowed_layer_blocks and block_name not in allowed_layer_blocks:
                issues.append(
                    f"{display_path(path)}:{line_no(text, absolute_start + block_offset)}: "
                    f"DSL_RULE_BLOCK {layer_label} does not allow block '{block_name}'"
                )
            block_meta = layer_schema.get(block_name)
            if is_block_meta(block_meta):
                lint_block_properties(
                    issues=issues,
                    path=path,
                    text=text,
                    component_start=absolute_start,
                    component_label=layer_label,
                    block_name=block_name,
                    block_offset=block_offset,
                    block_text=block_text,
                    block_meta=block_meta,
                )

        source_meta = layer_top_level_blocks.get("source")
        if source_meta:
            source_offset, source_block = source_meta
            source_name_offsets = {
                prop_name: prop_offset for prop_name, prop_offset in extract_immediate_brace_property_names(source_block)
            }
            source_scalar_props = {
                prop_name: (prop_value, prop_offset)
                for prop_name, prop_value, prop_offset in extract_immediate_brace_property_values(source_block)
            }

            has_table_name = "tableName" in source_name_offsets
            has_source_type = "type" in source_name_offsets
            has_sql_query = "sqlQuery" in source_name_offsets
            has_plsql_function_body = "plsqlFunctionBody" in source_name_offsets
            source_type_meta = source_scalar_props.get("type")
            source_type = clean_scalar_value(source_type_meta[0]).lower() if source_type_meta else ""

            if has_table_name and has_source_type:
                issues.append(
                    f"{display_path(path)}:{line_no(text, absolute_start + source_offset + source_name_offsets['type'])}: "
                    f"DSL_RULE_VALUE {layer_label} source.tableName must not be combined with source.type"
                )

            if has_table_name and has_sql_query:
                issues.append(
                    f"{display_path(path)}:{line_no(text, absolute_start + source_offset + source_name_offsets['sqlQuery'])}: "
                    f"DSL_RULE_VALUE {layer_label} source.tableName must not be combined with source.sqlQuery"
                )

            if has_table_name and has_plsql_function_body:
                issues.append(
                    f"{display_path(path)}:{line_no(text, absolute_start + source_offset + source_name_offsets['plsqlFunctionBody'])}: "
                    f"DSL_RULE_VALUE {layer_label} source.tableName must not be combined with source.plsqlFunctionBody"
                )

            if has_sql_query and has_plsql_function_body and not has_source_type:
                issues.append(
                    f"{display_path(path)}:{line_no(text, absolute_start + source_offset + source_name_offsets['plsqlFunctionBody'])}: "
                    f"DSL_RULE_VALUE {layer_label} source.sqlQuery and source.plsqlFunctionBody must not be emitted together without source.type"
                )

            if source_type == "sqlquery":
                if not has_sql_query:
                    issues.append(
                        f"{display_path(path)}:{line_no(text, absolute_start + source_offset)}: "
                        f"DSL_RULE_REQUIRED {layer_label} source.type: sqlQuery requires source.sqlQuery"
                    )
                if has_plsql_function_body:
                    issues.append(
                        f"{display_path(path)}:{line_no(text, absolute_start + source_offset + source_name_offsets['plsqlFunctionBody'])}: "
                        f"DSL_RULE_VALUE {layer_label} source.type: sqlQuery must not define source.plsqlFunctionBody"
                    )
            elif source_type == "functionbody":
                if not has_plsql_function_body:
                    issues.append(
                        f"{display_path(path)}:{line_no(text, absolute_start + source_offset)}: "
                        f"DSL_RULE_REQUIRED {layer_label} source.type: functionBody requires source.plsqlFunctionBody"
                    )
                if has_sql_query:
                    issues.append(
                        f"{display_path(path)}:{line_no(text, absolute_start + source_offset + source_name_offsets['sqlQuery'])}: "
                        f"DSL_RULE_VALUE {layer_label} source.type: functionBody must not define source.sqlQuery"
                    )
                if has_plsql_function_body and not re.search(r"(?i)\breturn\b", source_block):
                    issues.append(
                        f"{display_path(path)}:{line_no(text, absolute_start + source_offset + source_name_offsets['plsqlFunctionBody'])}: "
                        f"DSL_RULE_REQUIRED {layer_label} source.plsqlFunctionBody must return SQL text"
                    )
            elif not has_source_type:
                if has_plsql_function_body:
                    issues.append(
                        f"{display_path(path)}:{line_no(text, absolute_start + source_offset + source_name_offsets['plsqlFunctionBody'])}: "
                        f"DSL_RULE_REQUIRED {layer_label} source.plsqlFunctionBody requires source.type: functionBody"
                    )
                if not has_table_name and not has_sql_query:
                    issues.append(
                        f"{display_path(path)}:{line_no(text, absolute_start + source_offset)}: "
                        f"DSL_RULE_REQUIRED {layer_label} must define source.tableName, legacy source.sqlQuery, or typed source.type"
                    )


def lint_region_contracts(path: Path, text: str, schema: dict, validation_context: dict[str, Any] | None = None) -> list[str]:
    """Validate all region blocks against component schema contracts."""
    issues: list[str] = []
    region_schema_root = schema["components"].get("region", {})

    for start, region_name, block in find_component_blocks(text, "region"):
        region_type_match = re.search(r"(?m)^\s*type\s*:\s*([A-Za-z][A-Za-z0-9_/-]*)\s*$", block)
        if not region_type_match:
            continue

        region_type = region_type_match.group(1)
        region_type_key = region_schema_key(region_type)
        top_level_blocks = extract_top_level_blocks(block)

        if region_type_key == "staticContent":
            content_meta = top_level_blocks.get("content")
            if content_meta:
                content_offset, content_block = content_meta
                content_props = {
                    prop_name: prop_offset for prop_name, prop_offset in extract_immediate_brace_property_names(content_block)
                }
                html_prop_offset = content_props.get("html")
                if html_prop_offset is not None:
                    issues.append(
                        f"{display_path(path)}:{line_no(text, start + content_offset + html_prop_offset)}: "
                        f"DSL_RULE_PROP staticContent region '{region_name}' must use source.htmlCode instead of content.html"
                    )
            source_meta = top_level_blocks.get("source")
            if source_meta:
                source_offset, source_block = source_meta
                source_props = {
                    prop_name: (prop_value, prop_offset)
                    for prop_name, prop_value, prop_offset in extract_immediate_brace_property_values(source_block)
                }
                html_code_meta = source_props.get("htmlCode")
                if html_code_meta:
                    html_code_value, html_code_offset = html_code_meta
                    if re.match(r"(?i)^q'", html_code_value.strip()):
                        issues.append(
                            f"{display_path(path)}:{line_no(text, start + source_offset + html_code_offset)}: "
                            f"DSL_RULE_VALUE staticContent region '{region_name}' source.htmlCode must use inline HTML "
                            f"or a fenced ```html block; SQL-style q quoting is not allowed"
                        )

        if region_type == "themeTemplateComponent/metricCard":
            settings_meta = top_level_blocks.get("settings")
            if settings_meta:
                settings_offset, settings_block = settings_meta
                for prop_name, _prop_value, prop_offset in extract_immediate_brace_property_values(settings_block):
                    if prop_name == "displayAvatar":
                        issues.append(
                            f"{display_path(path)}:{line_no(text, start + settings_offset + prop_offset)}: "
                            f"DSL_RULE_PROP region '{region_name}' type '{region_type}' must not emit settings.displayAvatar; "
                            f"use plugin-avatar.displayAvatar instead"
                        )
                    if prop_name == "displayBadge":
                        issues.append(
                            f"{display_path(path)}:{line_no(text, start + settings_offset + prop_offset)}: "
                            f"DSL_RULE_PROP region '{region_name}' type '{region_type}' must not emit settings.displayBadge; "
                            f"use plugin-badge.displayBadge instead"
                        )

        region_schema = region_schema_root.get(region_type_key)
        if not isinstance(region_schema, dict):
            continue

        component_label = f"region '{region_name}' type '{region_type}'"
        allowed_blocks = set(region_schema.get("allowedBlocks", []))
        required_blocks = set(region_schema.get("requiredBlocks", []))

        for block_name, (offset, _sub_block) in top_level_blocks.items():
            if allowed_blocks and block_name not in allowed_blocks:
                issues.append(
                    f"{display_path(path)}:{line_no(text, start + offset)}: "
                    f"DSL_RULE_BLOCK {component_label} does not allow block '{block_name}'"
                )

        for block_name in sorted(required_blocks - set(top_level_blocks.keys())):
            issues.append(
                f"{display_path(path)}:{line_no(text, start)}: "
                f"DSL_RULE_REQUIRED {component_label} must define block '{block_name}'"
            )

        if region_type_key == "calendar":
            settings_meta = top_level_blocks.get("settings")
            if settings_meta and is_block_meta(region_schema.get("settings")):
                block_offset, block_text = settings_meta
                lint_block_properties(
                    issues=issues,
                    path=path,
                    text=text,
                    component_start=start,
                    component_label=component_label,
                    block_name="settings",
                    block_offset=block_offset,
                    block_text=block_text,
                    block_meta=region_schema["settings"],
                )
                lint_calendar_settings_values(
                    issues=issues,
                    path=path,
                    text=text,
                    component_start=start,
                    component_label=component_label,
                    block_offset=block_offset,
                    block_text=block_text,
                    template_mode=False,
                )

        minimum_children = region_schema.get("minimumChildren", {})
        if isinstance(minimum_children, dict):
            for child_keyword, minimum_count in sorted(minimum_children.items()):
                if not isinstance(minimum_count, int):
                    continue
                actual_children = len(find_immediate_component_blocks(block, child_keyword))
                if actual_children < minimum_count:
                    issues.append(
                        f"{display_path(path)}:{line_no(text, start)}: "
                        f"DSL_RULE_REQUIRED {component_label} must define at least {minimum_count} {child_keyword} child block(s)"
                    )

        if region_type_key == "cards":
            media_source = ""
            media_props: dict[str, tuple[str, int]] = {}
            for block_name in ("media", "blobAttributes"):
                block_meta = region_schema.get(block_name)
                block_data = top_level_blocks.get(block_name)
                if block_data and is_block_meta(block_meta):
                    block_offset, block_text = block_data
                    lint_block_properties(
                        issues=issues,
                        path=path,
                        text=text,
                        component_start=start,
                        component_label=component_label,
                        block_name=block_name,
                        block_offset=block_offset,
                        block_text=block_text,
                        block_meta=block_meta,
                    )
                    if block_name == "media":
                        for prop_name, prop_value, _prop_offset in extract_immediate_brace_property_values(block_text):
                            media_props[prop_name] = (clean_scalar_value(prop_value), _prop_offset)
                            if prop_name == "source":
                                media_source = clean_scalar_value(prop_value)

            media_meta = top_level_blocks.get("media")
            normalized_media_source = normalize_value(media_source)
            if media_meta and normalized_media_source:
                media_offset, _media_block = media_meta
                source_required_props = {
                    "blobcolumn": ("blobColumn", "blobColumn"),
                    "urlcolumn": ("urlColumn", "urlColumn"),
                    "imageurl": ("url", "imageUrl"),
                }
                required_meta = source_required_props.get(normalized_media_source)
                if required_meta:
                    required_prop, source_label = required_meta
                    if required_prop not in media_props:
                        issues.append(
                            f"{display_path(path)}:{line_no(text, start + media_offset)}: "
                            f"DSL_RULE_REQUIRED {component_label} must define media.{required_prop} "
                            f"when media.source: {source_label}"
                        )
                    source_bound_props = {
                        "blobColumn": "blobColumn",
                        "urlColumn": "urlColumn",
                        "url": "imageUrl",
                    }
                    for prop_name, source_value in source_bound_props.items():
                        if prop_name == required_prop or prop_name not in media_props:
                            continue
                        _prop_value, prop_offset = media_props[prop_name]
                        issues.append(
                            f"{display_path(path)}:{line_no(text, start + media_offset + prop_offset)}: "
                            f"DSL_RULE_VALUE {component_label} media.{prop_name} is allowed only when "
                            f"media.source: {source_value}"
                        )

            blob_attributes_meta = top_level_blocks.get("blobAttributes")
            if blob_attributes_meta and normalized_media_source != "blobcolumn":
                blob_attributes_offset, _blob_attributes_block = blob_attributes_meta
                issues.append(
                    f"{display_path(path)}:{line_no(text, start + blob_attributes_offset)}: "
                    f"DSL_RULE_VALUE {component_label} blobAttributes is allowed only when media.source: blobColumn"
                )

        if region_type_key == "chart":
            chart_block_meta = top_level_blocks.get("chart")
            chart_type = ""
            if chart_block_meta:
                _chart_offset, chart_block = chart_block_meta
                for prop_name, prop_value, _prop_offset in extract_immediate_property_values(chart_block):
                    if prop_name == "type":
                        chart_type = clean_scalar_value(prop_value)
                        break

            axis_names: list[str] = []
            seen_axis_identifiers: set[str] = set()
            axis_schema = region_schema.get("axis", {})
            for child_offset, axis_identifier, axis_block in find_immediate_component_blocks(block, "axis"):
                axis_label = f"{component_label} axis '{axis_identifier}'"
                absolute_start = start + child_offset
                if axis_identifier in seen_axis_identifiers:
                    issues.append(
                        f"{display_path(path)}:{line_no(text, absolute_start)}: "
                        f"DSL_RULE_IDENTIFIER {component_label} axis identifier '{axis_identifier}' must be unique within the region"
                    )
                seen_axis_identifiers.add(axis_identifier)

                axis_props = extract_immediate_property_values(axis_block)
                allowed_props = set(axis_schema.get("allowedProperties", []))
                required_props = set(axis_schema.get("requiredProperties", []))
                present_props = {prop_name for prop_name, _prop_value, _prop_offset in axis_props}

                for prop_name, _prop_value, prop_offset in axis_props:
                    if allowed_props and prop_name not in allowed_props:
                        issues.append(
                            f"{display_path(path)}:{line_no(text, absolute_start + prop_offset)}: "
                            f"DSL_RULE_PROP {axis_label} {prop_name} is not allowed"
                        )

                for prop_name in sorted(required_props - present_props):
                    issues.append(
                        f"{display_path(path)}:{line_no(text, absolute_start)}: "
                        f"DSL_RULE_REQUIRED {axis_label} must define property '{prop_name}'"
                    )

                enums = axis_schema.get("propertyEnums", {})
                if isinstance(enums, dict):
                    allowed_names = enums.get("name")
                    if isinstance(allowed_names, list):
                        axis_name = next(
                            (clean_scalar_value(prop_value) for prop_name, prop_value, _prop_offset in axis_props if prop_name == "name"),
                            "",
                        )
                        if axis_name:
                            axis_names.append(axis_name)
                            if normalize_value(axis_name) not in {normalize_value(value) for value in allowed_names}:
                                issues.append(
                                    f"{display_path(path)}:{line_no(text, absolute_start)}: "
                                    f"DSL_RULE_ENUM {axis_label} name must be one of: "
                                    + ", ".join(str(value) for value in allowed_names)
                                )

                axis_top_level_blocks = extract_top_level_blocks(axis_block)
                allowed_axis_blocks = set(axis_schema.get("allowedBlocks", []))
                for block_name, (offset, _sub_block) in axis_top_level_blocks.items():
                    if allowed_axis_blocks and block_name not in allowed_axis_blocks:
                        issues.append(
                            f"{display_path(path)}:{line_no(text, absolute_start + offset)}: "
                            f"DSL_RULE_BLOCK {axis_label} does not allow block '{block_name}'"
                        )
                    block_meta = axis_schema.get(block_name)
                    if is_block_meta(block_meta):
                        lint_block_properties(
                            issues=issues,
                            path=path,
                            text=text,
                            component_start=absolute_start,
                            component_label=axis_label,
                            block_name=block_name,
                            block_offset=offset,
                            block_text=axis_top_level_blocks[block_name][1],
                            block_meta=block_meta,
                        )

            series_schema = region_schema.get("series", {})
            seen_series_identifiers: set[str] = set()
            for child_offset, series_identifier, series_block in find_immediate_component_blocks(block, "series"):
                series_label = f"{component_label} series '{series_identifier}'"
                absolute_start = start + child_offset
                if series_identifier in seen_series_identifiers:
                    issues.append(
                        f"{display_path(path)}:{line_no(text, absolute_start)}: "
                        f"DSL_RULE_IDENTIFIER {component_label} series identifier '{series_identifier}' must be unique within the region"
                    )
                seen_series_identifiers.add(series_identifier)

                series_props = extract_immediate_property_values(series_block)
                present_props = {prop_name for prop_name, _prop_value, _prop_offset in series_props}
                for prop_name in sorted(set(series_schema.get("requiredProperties", [])) - present_props):
                    issues.append(
                        f"{display_path(path)}:{line_no(text, absolute_start)}: "
                        f"DSL_RULE_REQUIRED {series_label} must define property '{prop_name}'"
                    )

                series_top_level_blocks = extract_top_level_blocks(series_block)
                allowed_series_blocks = set(series_schema.get("allowedBlocks", []))
                for block_name, (offset, _sub_block) in series_top_level_blocks.items():
                    if allowed_series_blocks and block_name not in allowed_series_blocks:
                        issues.append(
                            f"{display_path(path)}:{line_no(text, absolute_start + offset)}: "
                            f"DSL_RULE_BLOCK {series_label} does not allow block '{block_name}'"
                        )
                    block_meta = series_schema.get(block_name)
                    if is_block_meta(block_meta):
                        lint_block_properties(
                            issues=issues,
                            path=path,
                            text=text,
                            component_start=absolute_start,
                            component_label=series_label,
                            block_name=block_name,
                            block_offset=offset,
                            block_text=series_top_level_blocks[block_name][1],
                            block_meta=block_meta,
                        )

            chart_requirements = region_schema.get("chartTypeAxisRequirements", {})
            if chart_type and isinstance(chart_requirements, dict):
                chart_type_rules = chart_requirements.get(chart_type, {})
                if isinstance(chart_type_rules, dict):
                    minimum_children = chart_type_rules.get("minimumChildren", {})
                    if isinstance(minimum_children, dict):
                        minimum_axes = minimum_children.get("axis")
                        actual_axes = len(find_immediate_component_blocks(block, "axis"))
                        if isinstance(minimum_axes, int) and actual_axes < minimum_axes:
                            issues.append(
                                f"{display_path(path)}:{line_no(text, start)}: "
                                f"DSL_RULE_REQUIRED {component_label} chart type '{chart_type}' must define at least {minimum_axes} axis child blocks"
                            )
                    required_axis_names = chart_type_rules.get("requiredAxisNames", [])
                    if isinstance(required_axis_names, list):
                        normalized_axis_names = {normalize_value(name) for name in axis_names}
                        for axis_name in required_axis_names:
                            if normalize_value(str(axis_name)) not in normalized_axis_names:
                                issues.append(
                                    f"{display_path(path)}:{line_no(text, start)}: "
                                    f"DSL_RULE_REQUIRED {component_label} chart type '{chart_type}' must include axis name '{axis_name}'"
                                )
            continue

        if region_type_key == "map":
            initial_position_meta = top_level_blocks.get("initialPositionAndZoom")
            if initial_position_meta:
                lint_map_initial_position_sql_aliases(
                    issues=issues,
                    path=path,
                    text=text,
                    component_start=start,
                    component_label=component_label,
                    block_offset=initial_position_meta[0],
                    block_text=initial_position_meta[1],
                )
            lint_map_layer_children(
                issues=issues,
                path=path,
                text=text,
                component_start=start,
                component_label=component_label,
                region_block=block,
                map_schema=region_schema,
            )
            continue

        lint_required_region_column_children(
            issues=issues,
            path=path,
            text=text,
            component_start=start,
            component_label=component_label,
            region_type_key=region_type_key,
            region_block=block,
            top_level_blocks=top_level_blocks,
            validation_context=validation_context,
        )

        column_schema = region_schema.get("column")
        if isinstance(column_schema, dict):
            for child_offset, column_identifier, column_block in find_immediate_component_blocks(block, "column"):
                column_label = f"{component_label} column '{column_identifier}'"
                absolute_start = start + child_offset
                column_props = extract_immediate_property_values(column_block)
                allowed_props = set(column_schema.get("allowedProperties", []))
                required_props = set(column_schema.get("requiredProperties", []))
                present_props = {prop_name for prop_name, _prop_value, _prop_offset in column_props}

                for prop_name, _prop_value, prop_offset in column_props:
                    if allowed_props and prop_name not in allowed_props:
                        issues.append(
                            f"{display_path(path)}:{line_no(text, absolute_start + prop_offset)}: "
                            f"DSL_RULE_PROP {column_label} {prop_name} is not allowed"
                        )

                for prop_name in sorted(required_props - present_props):
                    issues.append(
                        f"{display_path(path)}:{line_no(text, absolute_start)}: "
                        f"DSL_RULE_REQUIRED {column_label} must define property '{prop_name}'"
                    )

                column_top_level_blocks = extract_top_level_blocks(column_block)
                lint_column_block_shape(
                    issues=issues,
                    path=path,
                    text=text,
                    component_start=absolute_start,
                    column_label=column_label,
                    column_block=column_block,
                    column_top_level_blocks=column_top_level_blocks,
                    require_layout_sequence=region_type_key in {"classicReport", "interactiveReport", "contentRow"},
                )
                allowed_column_blocks = set(column_schema.get("allowedBlocks", []))
                required_column_blocks = set(column_schema.get("requiredBlocks", []))
                for block_name in sorted(required_column_blocks - set(column_top_level_blocks)):
                    issues.append(
                        f"{display_path(path)}:{line_no(text, absolute_start)}: "
                        f"DSL_RULE_REQUIRED {column_label} must define block '{block_name}'"
                    )
                for block_name, (offset, sub_block) in column_top_level_blocks.items():
                    block_meta = column_schema.get(block_name)
                    if block_name == "config" and not is_block_meta(block_meta):
                        block_meta = CONFIG_BUILD_OPTION_BLOCK_META
                    if allowed_column_blocks and block_name not in allowed_column_blocks and block_name != "config":
                        issues.append(
                            f"{display_path(path)}:{line_no(text, absolute_start + offset)}: "
                            f"DSL_RULE_BLOCK {column_label} does not allow block '{block_name}'"
                        )
                    if is_block_meta(block_meta):
                        lint_block_properties(
                            issues=issues,
                            path=path,
                            text=text,
                            component_start=absolute_start,
                            component_label=column_label,
                            block_name=block_name,
                            block_offset=offset,
                            block_text=sub_block,
                            block_meta=block_meta,
                        )

    return issues


def source_block_has_location(top_level_blocks: dict[str, tuple[int, str]]) -> bool:
    """Return whether a region source block declares source.location."""
    source_meta = top_level_blocks.get("source")
    if not source_meta:
        return False
    _source_offset, source_block = source_meta
    return any(prop_name == "location" for prop_name, _prop_value, _prop_offset in extract_immediate_brace_property_values(source_block))


def source_block_is_sql_or_table_backed(top_level_blocks: dict[str, tuple[int, str]]) -> bool:
    """Return whether a report source block has SQL/table-backed shape that needs child columns."""
    source_meta = top_level_blocks.get("source")
    if not source_meta:
        return False
    _source_offset, source_block = source_meta
    prop_names = {prop_name for prop_name, _prop_offset in extract_immediate_brace_property_names(source_block)}
    scalar_props = {
        prop_name: clean_scalar_value(prop_value).lower()
        for prop_name, prop_value, _prop_offset in extract_immediate_brace_property_values(source_block)
    }
    source_type = scalar_props.get("type", "")
    return bool(
        "sqlQuery" in prop_names
        or "tableName" in prop_names
        or source_type in {"sqlquery", "table"}
    )


def source_block_is_rest_backed(top_level_blocks: dict[str, tuple[int, str]]) -> bool:
    """Return whether a report source block has REST-backed shape that needs child columns."""
    source_meta = top_level_blocks.get("source")
    if not source_meta:
        return False
    _source_offset, source_block = source_meta
    prop_names = {prop_name for prop_name, _prop_offset in extract_immediate_brace_property_names(source_block)}
    scalar_props = {
        prop_name: clean_scalar_value(prop_value).lower()
        for prop_name, prop_value, _prop_offset in extract_immediate_brace_property_values(source_block)
    }
    return "restSource" in prop_names or scalar_props.get("location") == "restsource"


def source_block_has_data_projection(top_level_blocks: dict[str, tuple[int, str]]) -> bool:
    """Return whether a source block exposes SQL/table/REST data projection semantics."""
    return source_block_is_sql_or_table_backed(top_level_blocks) or source_block_is_rest_backed(top_level_blocks)


def content_row_display_mode(top_level_blocks: dict[str, tuple[int, str]]) -> str:
    """Return the Content Row componentAppearance.display value when present."""
    component_meta = top_level_blocks.get("componentAppearance")
    if not component_meta:
        return ""
    _component_offset, component_block = component_meta
    for prop_name, prop_value, _prop_offset in extract_immediate_brace_property_values(component_block):
        if prop_name == "display":
            return clean_scalar_value(prop_value).lower()
    return ""


def lint_required_region_column_children(
    *,
    issues: list[str],
    path: Path,
    text: str,
    component_start: int,
    component_label: str,
    region_type_key: str,
    region_block: str,
    top_level_blocks: dict[str, tuple[int, str]],
    validation_context: dict[str, Any] | None = None,
) -> None:
    """Require compiler-visible child columns that fully cover source projections."""
    actual_columns = len(find_immediate_component_blocks(region_block, "column"))
    if region_type_key == "cards" and actual_columns > 0:
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start)}: "
            f"DSL_RULE_BLOCK {component_label} must not define report-style column child block(s); "
            "use native cards column-mapping blocks instead"
        )
        return

    if region_type_key not in PROJECTION_COVERAGE_REGION_TYPES or not projection_source_requires_columns(region_type_key, top_level_blocks):
        return

    expected_columns, projection_error, source_kind = source_projection_columns(top_level_blocks, validation_context)
    if projection_error:
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start)}: "
            f"DSL_PROJECTION_METADATA_REQUIRED {component_label} {projection_error}"
        )
        return

    if actual_columns == 0:
        if region_type_key in {"contentRow", "metricCard"}:
            issues.append(
                f"{display_path(path)}:{line_no(text, component_start)}: "
                f"DSL_RULE_REQUIRED {component_label} report display with data source must define immediate "
                "column child block(s) using multiline layout.sequence and source.databaseColumn/source.dataType"
            )
        else:
            issues.append(
                f"{display_path(path)}:{line_no(text, component_start)}: "
                f"DSL_RULE_REQUIRED {component_label} with SQL/table/REST source must define immediate column child block(s)"
        )
        return

    if not expected_columns or source_kind == "none":
        return

    emitted_columns = collect_emitted_projection_columns(region_type_key, region_block)
    normalized_expected = {normalize_sql_identifier(column): column for column in expected_columns}

    for normalized, display_name in normalized_expected.items():
        if normalized not in emitted_columns:
            issues.append(
                f"{display_path(path)}:{line_no(text, component_start)}: "
                f"DSL_PROJECTION_COLUMN_MISSING {component_label} source projects '{display_name}' but no matching child column is emitted"
            )

    for normalized, (source_name, column_identifier, allowed_extra) in emitted_columns.items():
        if allowed_extra or normalized in normalized_expected:
            continue
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start)}: "
            f"DSL_PROJECTION_COLUMN_UNKNOWN {component_label} child column '{column_identifier}' maps to '{source_name}', "
            "which is not returned by the region source projection"
        )


def lint_column_block_shape(
    *,
    issues: list[str],
    path: Path,
    text: str,
    component_start: int,
    column_label: str,
    column_block: str,
    column_top_level_blocks: dict[str, tuple[int, str]],
    require_layout_sequence: bool,
) -> None:
    """Validate compiler-safe multiline column block shape."""
    if not require_layout_sequence:
        return

    one_line_layout = re.search(r"(?m)^[ \t]*layout[ \t]*\{[ \t]*sequence[ \t]*:", column_block)
    if one_line_layout:
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + one_line_layout.start())}: "
            f"DSL_RULE_BLOCK {column_label} must emit layout as a multiline block with sequence on its own line"
        )
        return

    layout_meta = column_top_level_blocks.get("layout")
    if not layout_meta:
        return

    _layout_offset, layout_block = layout_meta
    layout_props = {
        prop_name
        for prop_name, _prop_value, _prop_offset in extract_immediate_brace_property_values(layout_block)
    }
    if "sequence" not in layout_props:
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + layout_meta[0])}: "
            f"DSL_RULE_REQUIRED {column_label} layout must define sequence on its own line"
        )


def collect_content_row_sort_identifiers(region_block: str, sql_query_text: str | None) -> set[str]:
    """Collect declared/projected Content Row identifiers that are valid static sort keys."""
    identifiers: set[str] = set()

    for _child_offset, column_identifier, column_block in find_immediate_component_blocks(region_block, "column"):
        if column_identifier:
            identifiers.add(normalize_sql_identifier(column_identifier))
        column_top_level_blocks = extract_top_level_blocks(column_block)
        source_meta = column_top_level_blocks.get("source")
        if not source_meta:
            continue
        _source_offset, source_block = source_meta
        source_props = {
            prop_name: (prop_value, prop_offset)
            for prop_name, prop_value, prop_offset in extract_immediate_brace_property_values(source_block)
        }
        database_column_meta = source_props.get("databaseColumn")
        if database_column_meta:
            identifiers.add(normalize_sql_identifier(database_column_meta[0]))

    if sql_query_text:
        select_list = extract_top_level_select_list(sql_query_text)
        if select_list:
            for expression in select_list:
                identifier = extract_select_expression_identifier(expression)
                if identifier:
                    identifiers.add(normalize_sql_identifier(identifier))

    return identifiers


def normalized_order_by_term_identifier(term: str) -> str | None:
    """Return the identifier part of a simple ORDER BY term, or None for expressions."""
    cleaned = term.strip().rstrip(",")
    cleaned = re.sub(r"(?i)\s+nulls\s+(first|last)\s*$", "", cleaned).strip()
    cleaned = re.sub(r"(?i)\s+(asc|desc)\s*$", "", cleaned).strip()
    if not re.fullmatch(r'"?[A-Za-z][A-Za-z0-9_$#]*"?', cleaned):
        return None
    return normalize_sql_identifier(cleaned)


def lint_content_row_order_by(
    *,
    issues: list[str],
    path: Path,
    text: str,
    component_start: int,
    component_label: str,
    region_block: str,
    top_level_blocks: dict[str, tuple[int, str]],
) -> None:
    """Validate Content Row SQL ordering uses the region-level orderBy block."""
    source_meta = top_level_blocks.get("source")
    if not source_meta:
        return

    source_offset, source_block = source_meta
    source_prop_names = {
        prop_name: prop_offset for prop_name, prop_offset in extract_immediate_brace_property_names(source_block)
    }
    source_props = {
        prop_name: (prop_value, prop_offset)
        for prop_name, prop_value, prop_offset in extract_immediate_brace_property_values(source_block)
    }
    source_type_meta = source_props.get("type")
    source_type = clean_scalar_value(source_type_meta[0]).lower() if source_type_meta else ""
    sql_query_text = extract_fenced_property_body(source_block, "sqlQuery")
    has_sql_source = source_type == "sqlquery" or "sqlQuery" in source_prop_names
    if not has_sql_source:
        return

    if sql_query_text and contains_sql_order_by_clause(sql_query_text):
        sql_offset = source_prop_names.get("sqlQuery", 0)
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + source_offset + sql_offset)}: "
            f"DSL_RULE_VALUE {component_label} source.sqlQuery must not contain ORDER BY; "
            "use the top-level orderBy block instead"
        )

    order_by_meta = top_level_blocks.get("orderBy")
    if not order_by_meta:
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + source_offset)}: "
            f"DSL_RULE_REQUIRED {component_label} with SQL source must define top-level orderBy"
        )
        return

    order_by_offset, order_by_block = order_by_meta
    order_by_props = {
        prop_name: (prop_value, prop_offset)
        for prop_name, prop_value, prop_offset in extract_immediate_brace_property_values(order_by_block)
    }
    type_meta = order_by_props.get("type")
    if not type_meta:
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + order_by_offset)}: "
            f"DSL_RULE_REQUIRED {component_label} orderBy must define type: staticValue or type: item"
        )
        return

    order_by_type_value, order_by_type_offset = type_meta
    order_by_type = clean_scalar_value(order_by_type_value)
    normalized_order_by_type = order_by_type.lower()
    if normalized_order_by_type not in {"staticvalue", "item"}:
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + order_by_offset + order_by_type_offset)}: "
            f"DSL_RULE_ENUM {component_label} orderBy.type must be one of: staticValue, item"
        )
        return

    item_object_meta = extract_property_object_block(order_by_block, "item")
    order_by_clause_meta = order_by_props.get("orderByClause")

    if normalized_order_by_type == "staticvalue":
        if item_object_meta:
            item_offset, _item_block = item_object_meta
            issues.append(
                f"{display_path(path)}:{line_no(text, component_start + order_by_offset + item_offset)}: "
                f"DSL_RULE_PROP {component_label} orderBy.item is only valid when orderBy.type: item"
            )
        if not order_by_clause_meta:
            issues.append(
                f"{display_path(path)}:{line_no(text, component_start + order_by_offset)}: "
                f"DSL_RULE_REQUIRED {component_label} orderBy.type: staticValue requires orderBy.orderByClause"
            )
            return

        order_by_clause, order_by_clause_offset = order_by_clause_meta
        if re.match(r"(?i)^\s*order\s+by\b", order_by_clause):
            issues.append(
                f"{display_path(path)}:{line_no(text, component_start + order_by_offset + order_by_clause_offset)}: "
                f"DSL_RULE_VALUE {component_label} orderBy.orderByClause must omit the leading ORDER BY keyword"
            )
            return

        sort_identifiers = collect_content_row_sort_identifiers(region_block, sql_query_text)
        if not sort_identifiers:
            return
        for term in split_sql_top_level(order_by_clause, ","):
            identifier = normalized_order_by_term_identifier(term)
            if identifier is None:
                issues.append(
                    f"{display_path(path)}:{line_no(text, component_start + order_by_offset + order_by_clause_offset)}: "
                    f"DSL_RULE_VALUE {component_label} orderBy.orderByClause must use declared Content Row column aliases; "
                    f"raw sort expression '{term}' is not allowed"
                )
                continue
            if identifier not in sort_identifiers:
                issues.append(
                    f"{display_path(path)}:{line_no(text, component_start + order_by_offset + order_by_clause_offset)}: "
                    f"DSL_RULE_VALUE {component_label} orderBy.orderByClause references undeclared sort column '{term.strip()}'"
                )
        return

    if order_by_clause_meta:
        _order_by_clause, order_by_clause_offset = order_by_clause_meta
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + order_by_offset + order_by_clause_offset)}: "
            f"DSL_RULE_PROP {component_label} orderBy.orderByClause is only valid when orderBy.type: staticValue"
        )
    if not item_object_meta:
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + order_by_offset)}: "
            f"DSL_RULE_REQUIRED {component_label} orderBy.type: item requires item object"
        )
        return

    item_offset, item_block = item_object_meta
    item_props = {
        prop_name: (prop_value, prop_offset)
        for prop_name, prop_value, prop_offset in extract_property_values(item_block)
    }
    item_name_meta = item_props.get("itemName")
    if not item_name_meta:
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + order_by_offset + item_offset)}: "
            f"DSL_RULE_REQUIRED {component_label} orderBy.item requires itemName"
        )
    else:
        item_name, item_name_offset = item_name_meta
        cleaned_item_name = clean_scalar_value(item_name)
        page_items = {name for _item_start, name, _item_block in find_component_blocks(text, "pageItem")}
        if "{{" not in cleaned_item_name and cleaned_item_name not in page_items:
            issues.append(
                f"{display_path(path)}:{line_no(text, component_start + order_by_offset + item_offset + item_name_offset)}: "
                f"DSL_RULE_VALUE {component_label} orderBy.item.itemName must reference an available page item"
            )

    order_bys_meta = extract_property_object_block(item_block, "orderBys")
    if not order_bys_meta:
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + order_by_offset + item_offset)}: "
            f"DSL_RULE_REQUIRED {component_label} orderBy.item requires orderBys"
        )
        return
    order_bys_offset, order_bys_block = order_bys_meta
    order_bys_entries = [
        (prop_name, prop_value)
        for prop_name, prop_value, _prop_offset in extract_property_values(order_bys_block)
        if prop_name != "orderBys"
    ]
    if not order_bys_entries:
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + order_by_offset + item_offset + order_bys_offset)}: "
            f"DSL_RULE_REQUIRED {component_label} orderBy.item.orderBys must define at least one item value to ORDER BY mapping"
        )


def lint_content_row_actions(
    *,
    issues: list[str],
    path: Path,
    text: str,
    component_start: int,
    component_label: str,
    region_block: str,
    action_schema: dict[str, Any],
) -> None:
    """Validate content-row action placement and required action metadata."""
    allowed_action_props = set(action_schema.get("allowedProperties", []))
    allowed_action_blocks = set(action_schema.get("allowedBlocks", []))
    menu_schema = action_schema.get("menu", {})

    for action_offset, action_identifier, action_block in find_immediate_component_blocks(region_block, "action"):
        action_label = f"{component_label} action '{action_identifier}'"
        absolute_action_start = component_start + action_offset

        for prop_name, _prop_value, prop_offset in extract_immediate_property_values(action_block):
            if allowed_action_props and prop_name not in allowed_action_props:
                issues.append(
                    f"{display_path(path)}:{line_no(text, absolute_action_start + prop_offset)}: "
                    f"DSL_RULE_PROP {action_label} {prop_name} is not allowed"
                )

        action_top_level_blocks = extract_top_level_blocks(action_block)
        for block_name, (block_offset, block_text) in action_top_level_blocks.items():
            if allowed_action_blocks and block_name not in allowed_action_blocks:
                issues.append(
                    f"{display_path(path)}:{line_no(text, absolute_action_start + block_offset)}: "
                    f"DSL_RULE_BLOCK {action_label} does not allow block '{block_name}'"
                )

            block_meta = action_schema.get(block_name)
            if is_block_meta(block_meta):
                lint_block_properties(
                    issues=issues,
                    path=path,
                    text=text,
                    component_start=absolute_action_start,
                    component_label=action_label,
                    block_name=block_name,
                    block_offset=block_offset,
                    block_text=block_text,
                    block_meta=block_meta,
                )

        if isinstance(menu_schema, dict):
            allowed_menu_props = set(menu_schema.get("allowedProperties", []))
            allowed_menu_blocks = set(menu_schema.get("allowedBlocks", []))
            for menu_offset, menu_identifier, menu_block in find_immediate_component_blocks(action_block, "menu"):
                menu_label = f"{action_label} menu '{menu_identifier}'"
                absolute_menu_start = absolute_action_start + menu_offset

                for prop_name, _prop_value, prop_offset in extract_immediate_property_values(menu_block):
                    if allowed_menu_props and prop_name not in allowed_menu_props:
                        issues.append(
                            f"{display_path(path)}:{line_no(text, absolute_menu_start + prop_offset)}: "
                            f"DSL_RULE_PROP {menu_label} {prop_name} is not allowed"
                        )

                menu_top_level_blocks = extract_top_level_blocks(menu_block)
                for block_name, (block_offset, block_text) in menu_top_level_blocks.items():
                    if allowed_menu_blocks and block_name not in allowed_menu_blocks:
                        issues.append(
                            f"{display_path(path)}:{line_no(text, absolute_menu_start + block_offset)}: "
                            f"DSL_RULE_BLOCK {menu_label} does not allow block '{block_name}'"
                        )

                    block_meta = menu_schema.get(block_name)
                    if is_block_meta(block_meta):
                        lint_block_properties(
                            issues=issues,
                            path=path,
                            text=text,
                            component_start=absolute_menu_start,
                            component_label=menu_label,
                            block_name=block_name,
                            block_offset=block_offset,
                            block_text=block_text,
                            block_meta=block_meta,
                        )


def lint_calendar_template_options(
    *,
    issues: list[str],
    path: Path,
    text: str,
    component_start: int,
    component_label: str,
    block_offset: int,
    block_text: str,
) -> None:
    """Validate calendar template option declarations."""
    options_match = re.search(r"(?ms)templateOptions\s*:\s*\[(.*?)\]", block_text)
    if not options_match:
        return

    options_body = options_match.group(1)
    for token_match in re.finditer(r"(?m)^\s*#DEFAULT#\S+\s*$", options_body):
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + block_offset + options_match.start(1) + token_match.start())}: "
            f"DSL_RULE_VALUE {component_label} appearance.templateOptions must keep "
            "'#DEFAULT#' as a standalone value"
        )
    has_split_hide = re.search(r"(?m)^\s*t-Region--hideHeader\s*$", options_body)
    has_split_desc = re.search(r"(?m)^\s*js-addHiddenHeadingRoleDesc\s*$", options_body)
    if has_split_hide and has_split_desc:
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + block_offset + options_match.start(1))}: "
            f"DSL_RULE_VALUE {component_label} appearance.templateOptions must keep "
            "'t-Region--hideHeader js-addHiddenHeadingRoleDesc' as one combined value"
        )


def lint_calendar_settings_values(
    *,
    issues: list[str],
    path: Path,
    text: str,
    component_start: int,
    component_label: str,
    block_offset: int,
    block_text: str,
    template_mode: bool,
) -> None:
    """Validate calendar settings aliases and additionalCalendarViews values."""
    issue_prefix = "DSL_TEMPLATE_VALUE" if template_mode else "DSL_RULE_VALUE"

    for prop_name, _prop_value, prop_offset in extract_immediate_brace_property_values(block_text):
        canonical_name = CALENDAR_LEGACY_SETTING_ALIASES.get(prop_name)
        if canonical_name is None:
            continue
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + block_offset + prop_offset)}: "
            f"{issue_prefix} {component_label} settings must use canonical property "
            f"'{canonical_name}' instead of legacy alias '{prop_name}'"
        )

    for array_match in re.finditer(r"(?ms)^\s*additionalCalendarViews\s*:\s*\[(.*?)\]", block_text):
        array_body = array_match.group(1)
        for token_match in re.finditer(r"[A-Za-z][A-Za-z0-9]*", array_body):
            token = token_match.group(0)
            if normalize_value(token) not in CALENDAR_ADDITIONAL_VIEW_VALUES:
                issues.append(
                    f"{display_path(path)}:{line_no(text, component_start + block_offset + array_match.start(1) + token_match.start())}: "
                    f"{issue_prefix} {component_label} settings.additionalCalendarViews must use only: list, navigation"
                )


def lint_exact_template_option_values(
    *,
    issues: list[str],
    path: Path,
    text: str,
    component_start: int,
    component_label: str,
    block_name: str,
    block_offset: int,
    block_text: str,
) -> None:
    """Validate generic templateOptions value formatting."""
    for options_match in re.finditer(r"(?ms)templateOptions\s*:\s*\[(.*?)\]", block_text):
        options_body = options_match.group(1)
        if "," in options_body:
            issues.append(
                f"{display_path(path)}:{line_no(text, component_start + block_offset + options_match.start(1))}: "
                f"DSL_RULE_VALUE {component_label} {block_name}.templateOptions must emit multi-value arrays with one accepted value per line and must not use comma-separated inline arrays"
            )
        for token_match in re.finditer(r"(?m)^\s*#DEFAULT#\S+\s*$", options_body):
            issues.append(
                f"{display_path(path)}:{line_no(text, component_start + block_offset + options_match.start(1) + token_match.start())}: "
                f"DSL_RULE_VALUE {component_label} {block_name}.templateOptions must keep "
                "'#DEFAULT#' as one standalone value"
            )


def lint_region_contract(path: Path, text: str, schema: dict) -> list[str]:
    """Validate a single region contract file against the schema."""
    issues: list[str] = []
    region_schema = schema["components"].get("region", {})

    for start, region_name, block in find_component_blocks(text, "region"):
        region_type = extract_item_type(block)
        if not region_type:
            continue
        region_type_key = region_schema_key(region_type)
        if region_type_key not in region_schema:
            continue

        component_schema = region_schema[region_type_key]
        allowed_blocks = set(component_schema.get("allowedBlocks", []))
        required_blocks = set(component_schema.get("requiredBlocks", []))
        top_level_blocks = extract_top_level_blocks(block)
        component_label = f"region '{region_name}' type '{region_type}'"

        for block_name, (offset, _sub_block) in top_level_blocks.items():
            if allowed_blocks and block_name not in allowed_blocks:
                issues.append(
                    f"{display_path(path)}:{line_no(text, start + offset)}: "
                    f"DSL_RULE_BLOCK {component_label} does not allow block '{block_name}'"
                )

        missing_blocks = sorted(required_blocks - set(top_level_blocks.keys()))
        for block_name in missing_blocks:
            issues.append(
                f"{display_path(path)}:{line_no(text, start)}: "
                f"DSL_RULE_REQUIRED {component_label} must define block '{block_name}'"
            )

        for block_name, (block_offset, block_text) in top_level_blocks.items():
            block_meta = component_schema.get(block_name)
            if not is_block_meta(block_meta):
                continue
            lint_block_properties(
                issues=issues,
                path=path,
                text=text,
                component_start=start,
                component_label=component_label,
                block_name=block_name,
                block_offset=block_offset,
                block_text=block_text,
                block_meta=block_meta,
            )
            lint_exact_template_option_values(
                issues=issues,
                path=path,
                text=text,
                component_start=start,
                component_label=component_label,
                block_name=block_name,
                block_offset=block_offset,
                block_text=block_text,
            )

            if region_type_key == "calendar" and block_name == "appearance":
                lint_calendar_template_options(
                    issues=issues,
                    path=path,
                    text=text,
                    component_start=start,
                    component_label=component_label,
                    block_offset=block_offset,
                    block_text=block_text,
                )
            if region_type_key == "calendar" and block_name == "settings":
                lint_calendar_settings_values(
                    issues=issues,
                    path=path,
                    text=text,
                    component_start=start,
                    component_label=component_label,
                    block_offset=block_offset,
                    block_text=block_text,
                    template_mode=True,
                )
            if region_type_key == "dynamicContent" and block_name == "source":
                source_props = {name: (value, offset) for name, value, offset in extract_property_values(block_text)}
                plsql_meta = source_props.get("plsqlFunctionBody")
                if plsql_meta and not re.search(r"(?i)\breturn\b", block_text):
                    issues.append(
                        f"{display_path(path)}:{line_no(text, start + block_offset + plsql_meta[1])}: "
                        f"DSL_RULE_REQUIRED {component_label} source.plsqlFunctionBody must return renderable content"
                    )

        if region_type_key == "map":
            lint_map_layer_children(
                issues=issues,
                path=path,
                text=text,
                component_start=start,
                component_label=component_label,
                region_block=block,
                map_schema=component_schema,
            )

        if region_type_key == "contentRow":
            lint_content_row_order_by(
                issues=issues,
                path=path,
                text=text,
                component_start=start,
                component_label=component_label,
                region_block=block,
                top_level_blocks=top_level_blocks,
            )

        if region_type_key == "contentRow" and is_block_meta(component_schema.get("action")):
            lint_content_row_actions(
                issues=issues,
                path=path,
                text=text,
                component_start=start,
                component_label=component_label,
                region_block=block,
                action_schema=component_schema["action"],
            )

    return issues


def lint_component_settings_contract(path: Path, text: str, schema: dict) -> list[str]:
    """Validate component settings snippets against the schema."""
    issues: list[str] = []
    if path.name != "component-settings.apx" or "shared-components" not in path.parts:
        return issues

    shared_schema = schema["components"].get("sharedComponent", {})
    setting_schema = shared_schema.get("componentSetting", {})
    allowed_blocks = set(setting_schema.get("allowedBlocks", []))
    required_blocks = set(setting_schema.get("requiredBlocks", []))

    for start, setting_name, block in find_component_blocks(text, "componentSetting"):
        top_level_blocks = extract_top_level_blocks(block)
        component_label = f"componentSetting '{setting_name}'"

        for block_name, (offset, _sub_block) in top_level_blocks.items():
            if allowed_blocks and block_name not in allowed_blocks:
                issues.append(
                    f"{display_path(path)}:{line_no(text, start + offset)}: "
                    f"DSL_RULE_BLOCK {component_label} does not allow block '{block_name}'"
                )

        missing_blocks = sorted(required_blocks - set(top_level_blocks.keys()))
        for block_name in missing_blocks:
            issues.append(
                f"{display_path(path)}:{line_no(text, start)}: "
                f"DSL_RULE_REQUIRED {component_label} must define block '{block_name}'"
            )

        settings_meta = top_level_blocks.get("settings")
        if settings_meta and is_block_meta(setting_schema.get("settings")):
            block_offset, block_text = settings_meta
            lint_block_properties(
                issues=issues,
                path=path,
                text=text,
                component_start=start,
                component_label=component_label,
                block_name="settings",
                block_offset=block_offset,
                block_text=block_text,
                block_meta=setting_schema["settings"],
            )

    return issues


def extract_property_value_at_brace_depth(
    block: str,
    prop_name: str,
    *,
    brace_depth: int,
) -> tuple[str, int] | None:
    """Return the first property value found at the requested brace depth."""
    for actual_name, actual_value, prop_offset in extract_property_values(block):
        if actual_name != prop_name:
            continue
        _paren_depth, actual_brace_depth = nesting_depth(block, prop_offset)
        if actual_brace_depth == brace_depth:
            return actual_value, prop_offset
    return None


def lint_list_entry_current_state_contract(
    *,
    issues: list[str],
    path: Path,
    text: str,
    component_start: int,
    component_label: str,
    top_level_blocks: dict[str, tuple[int, str]],
) -> None:
    """Validate one-to-one current-state page mappings for list entries."""
    is_current_meta = top_level_blocks.get("isCurrent")
    if not is_current_meta:
        return

    is_current_offset, is_current_block = is_current_meta
    is_current_props = {
        prop_name: (prop_value, prop_offset)
        for prop_name, prop_value, prop_offset in extract_immediate_brace_property_values(is_current_block)
    }
    type_meta = is_current_props.get("type")
    pages_meta = is_current_props.get("pages")
    if not type_meta or not pages_meta:
        return

    if normalize_value(type_meta[0]) != "pages":
        return

    pages_value = clean_scalar_value(pages_meta[0])
    pages_absolute_offset = component_start + is_current_offset + pages_meta[1]
    page_number = parse_int(pages_value)
    if page_number is None:
        issues.append(
            f"{display_path(path)}:{line_no(text, pages_absolute_offset)}: "
            f"DSL_RULE_VALUE {component_label} isCurrent.pages must contain exactly one integer page id "
            f"matching link.target.page; got '{pages_value}'"
        )
        return

    link_meta = top_level_blocks.get("link")
    if not link_meta:
        return

    link_offset, link_block = link_meta
    target_page_meta = extract_property_value_at_brace_depth(link_block, "page", brace_depth=2)
    if target_page_meta is None:
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + link_offset)}: "
            f"DSL_RULE_REQUIRED {component_label} must define link.target.page when isCurrent.type: pages is used"
        )
        return

    target_page_value = clean_scalar_value(target_page_meta[0])
    target_page_number = parse_int(target_page_value)
    if target_page_number is None:
        issues.append(
            f"{display_path(path)}:{line_no(text, component_start + link_offset + target_page_meta[1])}: "
            f"DSL_RULE_VALUE {component_label} link.target.page must be exactly one integer page id when "
            f"isCurrent.type: pages is used; got '{target_page_value}'"
        )
        return

    if page_number != target_page_number:
        issues.append(
            f"{display_path(path)}:{line_no(text, pages_absolute_offset)}: "
            f"DSL_RULE_VALUE {component_label} isCurrent.pages must match link.target.page "
            f"{target_page_number}; got {page_number}"
        )


def lint_shared_entry_contract(path: Path, text: str, schema: dict) -> list[str]:
    """Validate shared component snippets against the schema."""
    issues: list[str] = []
    if "shared-components" not in path.parts:
        return issues

    entry_schema_key: str | None = None
    if path.name == "lists.apx":
        entry_schema_key = "listEntry"
    elif path.name == "breadcrumbs.apx":
        entry_schema_key = "breadcrumbEntry"
    else:
        return issues

    shared_schema = schema["components"].get("sharedComponent", {})
    entry_schema = shared_schema.get(entry_schema_key, {})
    allowed_blocks = set(entry_schema.get("allowedBlocks", []))
    required_blocks = set(entry_schema.get("requiredBlocks", []))

    for start, entry_name, block in find_component_blocks(text, "entry"):
        top_level_blocks = extract_top_level_blocks(block)
        component_label = f"entry '{entry_name}'"

        for block_name, (offset, _sub_block) in top_level_blocks.items():
            if allowed_blocks and block_name not in allowed_blocks:
                issues.append(
                    f"{display_path(path)}:{line_no(text, start + offset)}: "
                    f"DSL_RULE_BLOCK {component_label} does not allow block '{block_name}'"
                )

        missing_blocks = sorted(required_blocks - set(top_level_blocks.keys()))
        for block_name in missing_blocks:
            issues.append(
                f"{display_path(path)}:{line_no(text, start)}: "
                f"DSL_RULE_REQUIRED {component_label} must define block '{block_name}'"
            )

        behavior_meta = top_level_blocks.get("behavior")
        if behavior_meta:
            behavior_offset, behavior_block = behavior_meta
            for prop_name, _prop_value, prop_offset in extract_property_values(behavior_block):
                issues.append(
                    f"{display_path(path)}:{line_no(text, start + behavior_offset + prop_offset)}: "
                    f"DSL_RULE_PROP {component_label} behavior.{prop_name} is not allowed; use link.target"
                )

        link_meta = top_level_blocks.get("link")
        if link_meta and is_block_meta(entry_schema.get("link")):
            block_offset, block_text = link_meta
            lint_block_properties(
                issues=issues,
                path=path,
                text=text,
                component_start=start,
                component_label=component_label,
                block_name="link",
                block_offset=block_offset,
                block_text=block_text,
                block_meta=entry_schema["link"],
            )
            link_props = {name: (value, offset) for name, value, offset in extract_property_values(block_text)}
            target_meta = link_props.get("target")
            if target_meta and clean_scalar_value(target_meta[0]) == "#":
                issues.append(
                    f"{display_path(path)}:{line_no(text, start + block_offset + target_meta[1])}: "
                    f"DSL_RULE_VALUE {component_label} link.target must use a structured target object, not '#'"
                )

        is_current_meta = top_level_blocks.get("isCurrent")
        if is_current_meta and is_block_meta(entry_schema.get("isCurrent")):
            block_offset, block_text = is_current_meta
            lint_block_properties(
                issues=issues,
                path=path,
                text=text,
                component_start=start,
                component_label=component_label,
                block_name="isCurrent",
                block_offset=block_offset,
                block_text=block_text,
                block_meta=entry_schema["isCurrent"],
            )
            lint_list_entry_current_state_contract(
                issues=issues,
                path=path,
                text=text,
                component_start=start,
                component_label=component_label,
                top_level_blocks=top_level_blocks,
            )

        appearance_meta = top_level_blocks.get("appearance")
        if appearance_meta and is_block_meta(entry_schema.get("appearance")):
            block_offset, block_text = appearance_meta
            lint_block_properties(
                issues=issues,
                path=path,
                text=text,
                component_start=start,
                component_label=component_label,
                block_name="appearance",
                block_offset=block_offset,
                block_text=block_text,
                block_meta=entry_schema["appearance"],
            )

        execution_meta = top_level_blocks.get("execution")
        if execution_meta and is_block_meta(entry_schema.get("execution")):
            block_offset, block_text = execution_meta
            lint_block_properties(
                issues=issues,
                path=path,
                text=text,
                component_start=start,
                component_label=component_label,
                block_name="execution",
                block_offset=block_offset,
                block_text=block_text,
                block_meta=entry_schema["execution"],
            )

    return issues



def is_reference_app_path(path: Path) -> bool:
    """Return true for intentional reference/demo apps excluded from business security hard-fail rules."""
    parts = path.parts
    if "applications" not in parts:
        return False
    app_index = parts.index("applications") + 1
    if app_index >= len(parts):
        return False
    return parts[app_index] in {"ut"}


def is_business_app_path(path: Path) -> bool:
    """Return true when security baseline validators should hard-fail the path."""
    return "applications" in path.parts and not is_reference_app_path(path)


def block_property_map(block: str) -> dict[str, tuple[str, int]]:
    return {name: (value, offset) for name, value, offset in extract_property_values(block)}


def clean_component_ref(value: str) -> str:
    """Normalize a component reference by removing scalar quoting and @ prefix."""
    cleaned = clean_scalar_value(value)
    return cleaned[1:] if cleaned.startswith("@") else cleaned


def has_security_review_rationale(block: str) -> bool:
    return bool(re.search(r"(?is)security[- ]review|public[- ]page[- ]review|reviewed\s+public", block))


def is_login_page(page_name: str, page_block: str) -> bool:
    if clean_scalar_value(page_name) in {"9999", "101"}:
        return True
    props = {name: clean_scalar_value(value).upper() for name, value, _offset in extract_immediate_property_values(page_block)}
    alias = props.get("alias", "")
    title = props.get("title", "")
    name = props.get("name", "")
    return alias == "LOGIN" or "LOGIN" in title or "LOGIN" in name


def is_global_page(page_name: str) -> bool:
    """Return true for the APEX Global Page artifact."""
    return clean_scalar_value(page_name) == "0"


def lint_global_page_contract(path: Path, text: str, page_start: int, page_name: str, page_block: str) -> list[str]:
    """Validate the special Page 0 contract.

    Page 0 is not a normal non-login page. It must not receive page-level
    security/access properties that belong to concrete pages.
    """
    issues: list[str] = []
    page_blocks = extract_top_level_blocks(page_block)
    if "security" in page_blocks:
        security_offset, security_block = page_blocks["security"]
        issues.append(
            f"{display_path(path)}:{line_no(text, page_start + security_offset)}: "
            f"PAGE0_GLOBAL_PAGE_MINIMAL_001 page '{page_name}' must not define a security block"
        )
        props = block_property_map(security_block)
        for prop_name in ("authorizationScheme", "authentication", "pageAccessProtection", "formAutoComplete"):
            prop_meta = props.get(prop_name)
            if prop_meta:
                issues.append(
                    f"{display_path(path)}:{line_no(text, page_start + security_offset + prop_meta[1])}: "
                    f"PAGE0_GLOBAL_PAGE_MINIMAL_001 page '{page_name}' must not define security.{prop_name}"
                )

    top_level_props = {name: offset for name, _value, offset in extract_immediate_property_values(page_block)}
    for prop_name in ("authorizationScheme", "authentication", "pageAccessProtection", "formAutoComplete"):
        prop_offset = top_level_props.get(prop_name)
        if prop_offset is not None:
            issues.append(
                f"{display_path(path)}:{line_no(text, page_start + prop_offset)}: "
                f"PAGE0_GLOBAL_PAGE_MINIMAL_001 page '{page_name}' must not define {prop_name}"
            )
    return issues


def lint_form_primary_key_contract(path: Path, text: str) -> list[str]:
    """Validate that every form region has at least one mapped primary-key item."""
    issues: list[str] = []

    for page_start, page_name, page_block in find_component_blocks(text, "page"):
        form_regions: dict[str, int] = {}
        for region_offset, region_name, region_block in find_immediate_component_blocks(page_block, "region"):
            region_type = extract_item_type(region_block)
            if region_type == "form":
                form_regions[region_name] = page_start + region_offset

        if not form_regions:
            continue

        pk_items_by_region: dict[str, list[str]] = {region_name: [] for region_name in form_regions}

        for item_offset, item_name, item_block in find_immediate_component_blocks(page_block, "pageItem"):
            top_level_blocks = extract_top_level_blocks(item_block)
            source_meta = top_level_blocks.get("source")
            if not source_meta:
                continue

            source_offset, source_block = source_meta
            source_props = block_property_map(source_block)
            form_region_meta = source_props.get("formRegion")
            if not form_region_meta:
                continue

            form_region_name = clean_component_ref(form_region_meta[0])
            if form_region_name not in form_regions:
                continue

            primary_key_meta = source_props.get("primaryKey")
            if not primary_key_meta:
                continue

            primary_key_value = clean_scalar_value(primary_key_meta[0]).lower()
            absolute_prop_offset = page_start + item_offset + source_offset + primary_key_meta[1]
            if primary_key_value == "true":
                pk_items_by_region[form_region_name].append(item_name)
            else:
                issues.append(
                    f"{display_path(path)}:{line_no(text, absolute_prop_offset)}: "
                    f"FORM_PRIMARY_KEY_REQUIRED_001 pageItem '{item_name}' maps to form region "
                    f"'{form_region_name}' and must not emit primaryKey: {clean_scalar_value(primary_key_meta[0])}; "
                    f"use primaryKey: true only for PK items and omit it for non-PK items"
                )

        for region_name, region_start in form_regions.items():
            if pk_items_by_region[region_name]:
                continue
            issues.append(
                f"{display_path(path)}:{line_no(text, region_start)}: "
                f"FORM_PRIMARY_KEY_REQUIRED_001 page '{page_name}' region '{region_name}' type 'form' "
                f"must have at least one pageItem with source.formRegion: @{region_name} and source.primaryKey: true"
            )

    return issues


def lint_form_edit_contract(path: Path, text: str) -> list[str]:
    """Reject stale form edit.allowedOperations syntax."""
    issues: list[str] = []

    for page_start, page_name, page_block in find_component_blocks(text, "page"):
        for region_offset, region_name, region_block in find_immediate_component_blocks(page_block, "region"):
            if extract_item_type(region_block) != "form":
                continue
            top_level_blocks = extract_top_level_blocks(region_block)
            edit_meta = top_level_blocks.get("edit")
            if not edit_meta:
                continue
            edit_offset, edit_block = edit_meta
            edit_props = block_property_map(edit_block)
            allowed_ops_meta = edit_props.get("allowedOperations")
            if not allowed_ops_meta:
                continue
            _allowed_ops_value, allowed_ops_offset = allowed_ops_meta
            issues.append(
                f"{display_path(path)}:{line_no(text, page_start + region_offset + edit_offset + allowed_ops_offset)}: "
                f"FORM_EDIT_ALLOWED_OPERATIONS_LEGACY_001 page '{page_name}' form region '{region_name}' "
                "must not define edit.allowedOperations; use direct edit.add, edit.update, and edit.delete properties"
            )

    return issues


def lint_saved_report_visibility_contract(path: Path, text: str) -> list[str]:
    """Reject legacy savedReport visibility tokens."""
    issues: list[str] = []

    for block_start, block_name, block in find_component_blocks(text, "savedReport"):
        for prop_name, prop_value, prop_offset in extract_immediate_property_values(block):
            if prop_name != "visibility":
                continue
            if clean_scalar_value(prop_value) != "primary":
                continue
            issues.append(
                f"{display_path(path)}:{line_no(text, block_start + prop_offset)}: "
                f"SAVED_REPORT_VISIBILITY_LEGACY_001 savedReport '{block_name}' must not define visibility: primary; "
                "use visibility: primaryDefault"
            )

    return issues


def lint_faceted_search_current_facets_selector_contract(path: Path, text: str) -> list[str]:
    """Reject stale faceted-search currentFacetsSelector usage."""
    issues: list[str] = []

    for page_start, page_name, page_block in find_component_blocks(text, "page"):
        for region_offset, region_name, region_block in find_immediate_component_blocks(page_block, "region"):
            if extract_item_type(region_block) != "facetedSearch":
                continue
            top_level_blocks = extract_top_level_blocks(region_block)
            settings_meta = top_level_blocks.get("settings")
            if not settings_meta:
                continue
            settings_offset, settings_block = settings_meta
            settings_props = block_property_map(settings_block)
            selector_meta = settings_props.get("currentFacetsSelector")
            if not selector_meta:
                continue
            _selector_value, selector_offset = selector_meta
            issues.append(
                f"{display_path(path)}:{line_no(text, page_start + region_offset + settings_offset + selector_offset)}: "
                f"FACETED_SEARCH_CURRENT_FACETS_SELECTOR_INVALID_001 page '{page_name}' facetedSearch region '{region_name}' "
                "must not define settings.currentFacetsSelector; the live importer rejects that property in this runtime"
            )

    return issues


def lint_interactive_report_link_column_contract(path: Path, text: str) -> list[str]:
    """Reject stale Interactive Report linkColumn values that use report aliases."""
    issues: list[str] = []
    allowed_values = {"customTarget", "exclude", "singleRowView"}

    for page_start, page_name, page_block in find_component_blocks(text, "page"):
        for region_offset, region_name, region_block in find_immediate_component_blocks(page_block, "region"):
            if extract_item_type(region_block) != "interactiveReport":
                continue
            for link_offset, link_block in find_immediate_named_brace_blocks(region_block, "link"):
                link_props = block_property_map(link_block)
                link_column_meta = link_props.get("linkColumn")
                if not link_column_meta:
                    continue
                value, prop_offset = link_column_meta
                value = clean_scalar_value(value)
                if value in allowed_values:
                    continue
                issues.append(
                    f"{display_path(path)}:{line_no(text, page_start + region_offset + link_offset + prop_offset)}: "
                    f"INTERACTIVE_REPORT_LINK_COLUMN_INVALID_001 page '{page_name}' interactiveReport region '{region_name}' "
                    f"must not define link.linkColumn: {value}; use compiler-backed values such as customTarget, exclude, or singleRowView"
                )

    return issues


def lint_page_item_layout_legacy_properties(path: Path, text: str) -> list[str]:
    """Validate legacy aliases inside page-item layout blocks."""
    issues: list[str] = []

    for item_start, item_name, item_block in find_component_blocks(text, "pageItem"):
        top_level_blocks = extract_top_level_blocks(item_block)
        layout_meta = top_level_blocks.get("layout")
        if not layout_meta:
            continue

        layout_offset, layout_block = layout_meta
        layout_props = block_property_map(layout_block)
        label_col_span_meta = layout_props.get("labelColSpan")
        if not label_col_span_meta:
            continue

        item_type = extract_item_type(item_block) or "unknown"
        issues.append(
            f"{display_path(path)}:{line_no(text, item_start + layout_offset + label_col_span_meta[1])}: "
            f"PAGE_ITEM_LAYOUT_LABEL_COL_SPAN_LEGACY_001 pageItem '{item_name}' type '{item_type}' "
            "must not define legacy alias layout.labelColSpan; use layout.labelColumnSpan"
        )

    return issues


def lint_page_item_region_slots(path: Path, text: str) -> list[str]:
    """Validate that region-bound page items use region slots instead of page body slots."""
    issues: list[str] = []

    for item_start, item_name, item_block in find_component_blocks(text, "pageItem"):
        top_level_blocks = extract_top_level_blocks(item_block)
        layout_meta = top_level_blocks.get("layout")
        if not layout_meta:
            continue

        layout_offset, layout_block = layout_meta
        layout_props = block_property_map(layout_block)
        if "region" not in layout_props or "slot" not in layout_props:
            continue

        slot_value, slot_offset = layout_props["slot"]
        if clean_scalar_value(slot_value) not in {"body", "BODY"}:
            continue

        issues.append(
            f"{display_path(path)}:{line_no(text, item_start + layout_offset + slot_offset)}: "
            f"PAGE_ITEM_REGION_SLOT_REQUIRED_001 pageItem '{item_name}' with layout.region must use "
            "layout.slot: regionBody instead of body"
        )

    return issues


def lint_display_only_source_types(path: Path, text: str) -> list[str]:
    """Validate compiler-backed displayOnly source.type constraints."""
    issues: list[str] = []

    for item_start, item_name, item_block in find_component_blocks(text, "pageItem"):
        item_type = (extract_item_type(item_block) or "").lower()
        if item_type != "displayonly":
            continue

        top_level_blocks = extract_top_level_blocks(item_block)
        source_meta = top_level_blocks.get("source")
        if not source_meta:
            continue

        source_offset, source_block = source_meta
        source_props = block_property_map(source_block)
        source_type_meta = source_props.get("type")
        if not source_type_meta:
            continue

        source_type_value = clean_scalar_value(source_type_meta[0])
        if source_type_value != "substitutionString":
            continue

        issues.append(
            f"{display_path(path)}:{line_no(text, item_start + source_offset + source_type_meta[1])}: "
            f"DISPLAY_ONLY_SOURCE_TYPE_INVALID_001 pageItem '{item_name}' type 'displayOnly' must not use "
            "source.type: substitutionString; use source.type: item with source.item, or another compiler-valid "
            "displayOnly source type"
        )

    return issues


def lint_generated_security_contract(path: Path, text: str) -> list[str]:
    """Validate generated business-app security defaults."""
    issues: list[str] = []
    if not is_business_app_path(path):
        return issues

    if path.name == "application.apx":
        for app_start, app_name, app_block in find_component_blocks(text, "app"):
            app_blocks = extract_top_level_blocks(app_block)
            if "sessionStateProtection" not in app_blocks:
                issues.append(
                    f"{display_path(path)}:{line_no(text, app_start)}: "
                    f"SECURITY_BASELINE_REQUIRED_001 app '{app_name}' must define sessionStateProtection"
                )
            session_meta = app_blocks.get("sessionManagement")
            if not session_meta:
                issues.append(
                    f"{display_path(path)}:{line_no(text, app_start)}: "
                    f"SECURITY_BASELINE_REQUIRED_001 app '{app_name}' must define sessionManagement with maxSessionIdleTime 3600 and maxSessionLength 28800"
                )
            else:
                session_offset, session_block = session_meta
                props = block_property_map(session_block)
                expected = {"maxSessionIdleTime": "3600", "maxSessionLength": "28800"}
                for prop_name, prop_value in expected.items():
                    actual_meta = props.get(prop_name)
                    if not actual_meta or clean_scalar_value(actual_meta[0]) != prop_value:
                        issues.append(
                            f"{display_path(path)}:{line_no(text, app_start + session_offset)}: "
                            f"SECURITY_BASELINE_REQUIRED_001 app '{app_name}' sessionManagement.{prop_name} must be {prop_value}"
                        )
        return issues

    if "pages" in path.parts and path.suffix == ".apx":
        for page_start, page_name, page_block in find_component_blocks(text, "page"):
            if is_global_page(page_name):
                issues.extend(lint_global_page_contract(path, text, page_start, page_name, page_block))
                continue
            page_blocks = extract_top_level_blocks(page_block)
            security_meta = page_blocks.get("security")
            if not security_meta:
                issues.append(
                    f"{display_path(path)}:{line_no(text, page_start)}: "
                    f"SECURITY_BASELINE_REQUIRED_001 page '{page_name}' must define security block"
                )
                continue
            security_offset, security_block = security_meta
            props = block_property_map(security_block)
            protection_meta = props.get("pageAccessProtection")
            if not protection_meta or clean_scalar_value(protection_meta[0]) != "argumentsMustHaveChecksum":
                issues.append(
                    f"{display_path(path)}:{line_no(text, page_start + security_offset)}: "
                    f"SECURITY_BASELINE_REQUIRED_001 page '{page_name}' must use pageAccessProtection: argumentsMustHaveChecksum"
                )

            authentication = clean_scalar_value(props.get("authentication", ("", 0))[0]).lower()
            is_public = authentication == "public"
            if is_login_page(page_name, page_block):
                continue
            if is_public:
                if not has_security_review_rationale(page_block):
                    issues.append(
                        f"{display_path(path)}:{line_no(text, page_start + security_offset + props.get('authentication', ('', 0))[1])}: "
                        f"PUBLIC_PAGE_REVIEW_REQUIRED_001 page '{page_name}' is public and must include security-review rationale"
                    )
                continue
            auth_meta = props.get("authorizationScheme")
            if not auth_meta:
                issues.append(
                    f"{display_path(path)}:{line_no(text, page_start + security_offset)}: "
                    f"SECURITY_BASELINE_REQUIRED_001 non-login page '{page_name}' must define authorizationScheme mustNotBePublicUser or a stricter @static-id scheme"
                )
            else:
                auth_value = clean_scalar_value(auth_meta[0])
                if auth_value == "@mustNotBePublicUser" or "must-not-be-public-user" in auth_value:
                    issues.append(
                        f"{display_path(path)}:{line_no(text, page_start + security_offset + auth_meta[1])}: "
                        f"SECURITY_BASELINE_REQUIRED_001 built-in Must Not Be Public User must be referenced as mustNotBePublicUser, not an @static-id alias"
                    )
                elif auth_value != "mustNotBePublicUser" and not auth_value.startswith("@"):
                    issues.append(
                        f"{display_path(path)}:{line_no(text, page_start + security_offset + auth_meta[1])}: "
                        f"SECURITY_BASELINE_REQUIRED_001 custom authorization schemes must be referenced as @<static-id>"
                    )

    for item_start, item_name, item_block in find_component_blocks(text, "pageItem"):
        item_type = (extract_item_type(item_block) or "").lower()
        id_style = bool(re.search(r"(?i)(?:^P\d+_.*(?:_ID|_PK|_KEY|_ROWID)$|^P\d+_ID$)", item_name))
        if item_type != "hidden" and not id_style:
            continue
        item_blocks = extract_top_level_blocks(item_block)
        security_meta = item_blocks.get("security")
        if not security_meta:
            issues.append(
                f"{display_path(path)}:{line_no(text, item_start)}: "
                f"HIDDEN_ITEM_SSP_REQUIRED_001 pageItem '{item_name}' must define security.sessionStateProtection"
            )
            continue
        security_offset, security_block = security_meta
        props = block_property_map(security_block)
        ssp_meta = props.get("sessionStateProtection")
        if not ssp_meta:
            issues.append(
                f"{display_path(path)}:{line_no(text, item_start + security_offset)}: "
                f"HIDDEN_ITEM_SSP_REQUIRED_001 pageItem '{item_name}' must define sessionStateProtection: checksumRequiredSessionLevel"
            )
            continue
        ssp_value = clean_scalar_value(ssp_meta[0])
        if ssp_value == "unrestricted":
            if not re.search(r"(?is)same-page dynamic action|same page dynamic action|dynamic-action", item_block):
                issues.append(
                    f"{display_path(path)}:{line_no(text, item_start + security_offset + ssp_meta[1])}: "
                    f"HIDDEN_ITEM_SSP_REQUIRED_001 pageItem '{item_name}' unrestricted session state requires same-page dynamic-action comments rationale"
                )
        elif ssp_value != "checksumRequiredSessionLevel":
            issues.append(
                f"{display_path(path)}:{line_no(text, item_start + security_offset + ssp_meta[1])}: "
                f"HIDDEN_ITEM_SSP_REQUIRED_001 pageItem '{item_name}' must use checksumRequiredSessionLevel"
            )

    return issues

def lint_application_contract(path: Path, text: str) -> list[str]:
    """Validate application-level DSL contract rules."""
    issues: list[str] = []
    if path.name != "application.apx":
        return issues

    app_blocks = find_component_blocks(text, "app")
    if not app_blocks:
        issues.append(f"{display_path(path)}:1: DSL_RULE_REQUIRED application.apx must define an app block")
        return issues

    start, app_name, app_block = app_blocks[0]
    top_level_blocks = extract_top_level_blocks(app_block)
    top_level_names = set(top_level_blocks.keys())
    required_blocks = ("navigation", "navigationMenu", "navigationBar")
    legacy_blocks = ("nav", "navMenu", "navBar")

    for block_name in required_blocks:
        if block_name not in top_level_names:
            issues.append(
                f"{display_path(path)}:{line_no(text, start)}: "
                f"DSL_RULE_REQUIRED app '{app_name}' must define block '{block_name}'"
            )

    for block_name in legacy_blocks:
        if block_name in top_level_names:
            block_offset, _ = top_level_blocks[block_name]
            issues.append(
                f"{display_path(path)}:{line_no(text, start + block_offset)}: "
                f"DSL_RULE_LEGACY app '{app_name}' must not use legacy block '{block_name}'"
            )

    navigation_meta = top_level_blocks.get("navigation")
    if navigation_meta:
        block_offset, block_text = navigation_meta
        props = {name for name, _value, _offset in extract_property_values(block_text)}
        for prop_name in ("homeUrl", "loginUrl"):
            if prop_name not in props:
                issues.append(
                    f"{display_path(path)}:{line_no(text, start + block_offset)}: "
                    f"DSL_RULE_REQUIRED app '{app_name}' navigation must define property '{prop_name}'"
                )

    for owning_block, required_props in (
        ("navigationMenu", ("list", "listTemplate", "templateOptions")),
        ("navigationBar", ("list", "listTemplate")),
    ):
        block_meta = top_level_blocks.get(owning_block)
        if not block_meta:
            continue
        block_offset, block_text = block_meta
        props = {name for name, _value, _offset in extract_property_values(block_text)}
        for prop_name in required_props:
            if prop_name not in props:
                issues.append(
                    f"{display_path(path)}:{line_no(text, start + block_offset)}: "
                    f"DSL_RULE_REQUIRED app '{app_name}' {owning_block} must define property '{prop_name}'"
                )

    root_props = {name for name, _value, _offset in extract_immediate_property_values(app_block)}
    for prop_name in ("homeUrl", "loginUrl", "list", "listTemplate", "templateOptions"):
        if prop_name in root_props:
            issues.append(
                f"{display_path(path)}:{line_no(text, start)}: "
                f"DSL_RULE_PROP_SCOPE app '{app_name}' must not define top-level property '{prop_name}'"
            )

    for block_name, disallowed_props in (
        ("navigationMenu", ("homeUrl", "loginUrl")),
        ("navigationBar", ("homeUrl", "loginUrl")),
        ("navigation", ("list", "listTemplate", "templateOptions")),
    ):
        block_meta = top_level_blocks.get(block_name)
        if not block_meta:
            continue
        block_offset, block_text = block_meta
        for prop_name, _prop_value, prop_offset in extract_property_values(block_text):
            if prop_name in disallowed_props:
                issues.append(
                    f"{display_path(path)}:{line_no(text, start + block_offset + prop_offset)}: "
                    f"DSL_RULE_PROP_SCOPE app '{app_name}' {block_name}.{prop_name} is not allowed in that block"
                )

    return issues


def lint_theme_contract(path: Path, text: str) -> list[str]:
    """Validate theme-level DSL contract rules."""
    issues: list[str] = []
    if path.name != "theme.apx" or "shared-components" not in path.parts:
        return issues

    theme_blocks = find_component_blocks(text, "theme")
    if not theme_blocks:
        issues.append(f"{display_path(path)}:1: DSL_RULE_REQUIRED theme.apx must define a theme block")
        return issues

    start, theme_name, theme_block = theme_blocks[0]
    top_level_blocks = extract_top_level_blocks(theme_block)
    immediate_props = {name: (value, offset) for name, value, offset in extract_immediate_property_values(theme_block)}

    if "themeNumber" not in immediate_props:
        issues.append(
            f"{display_path(path)}:{line_no(text, start)}: "
            f"DSL_RULE_REQUIRED theme '{theme_name}' must define property 'themeNumber'"
        )
    if "themeNo" in immediate_props:
        issues.append(
            f"{display_path(path)}:{line_no(text, start + immediate_props['themeNo'][1])}: "
            f"DSL_RULE_LEGACY theme '{theme_name}' must not use legacy property 'themeNo'"
        )
    if "javaScript" not in top_level_blocks:
        issues.append(
            f"{display_path(path)}:{line_no(text, start)}: "
            f"DSL_RULE_REQUIRED theme '{theme_name}' must define block 'javaScript'"
        )
    if "js" in top_level_blocks:
        block_offset, _ = top_level_blocks["js"]
        issues.append(
            f"{display_path(path)}:{line_no(text, start + block_offset)}: "
            f"DSL_RULE_LEGACY theme '{theme_name}' must not use legacy block 'js'"
        )

    theme_number_meta = immediate_props.get("themeNumber")
    base_theme_meta = immediate_props.get("baseTheme")
    version_meta = immediate_props.get("version")
    theme_number = clean_scalar_value(theme_number_meta[0]) if theme_number_meta else ""
    current_theme_style_uses_theme_relative_reference = False
    style_meta = top_level_blocks.get("style")
    if style_meta:
        block_offset, block_text = style_meta
        style_props = {name: (value, prop_offset) for name, value, prop_offset in extract_property_values(block_text)}
        current_theme_style_meta = style_props.get("currentThemeStyle")
        if current_theme_style_meta and clean_scalar_value(current_theme_style_meta[0]).startswith("@/"):
            current_theme_style_uses_theme_relative_reference = True

    if theme_number == "42" and current_theme_style_uses_theme_relative_reference and not base_theme_meta:
        if version_meta:
            issues.append(
                f"{display_path(path)}:{line_no(text, start + version_meta[1])}: "
                f"THEME_BASE_THEME_REQUIRED_001 theme '{theme_name}' must define baseTheme for Universal Theme; "
                "using legacy version without baseTheme causes downstream REFERENCE_NOT_FOUND failures for @/... references"
            )
        else:
            issues.append(
                f"{display_path(path)}:{line_no(text, start)}: "
                f"THEME_BASE_THEME_REQUIRED_001 theme '{theme_name}' must define baseTheme for Universal Theme; "
                "missing baseTheme causes downstream REFERENCE_NOT_FOUND failures for @/... references"
            )

    component_defaults = top_level_blocks.get("componentDefaults")
    if component_defaults:
        block_offset, block_text = component_defaults
        prop_map = {name: prop_offset for name, _value, prop_offset in extract_property_values(block_text)}
        legacy_props = (
            "navBarList",
            "navMenuListPosition",
            "navMenuListTop",
            "navMenuListSide",
        )
        for prop_name in legacy_props:
            if prop_name in prop_map:
                issues.append(
                    f"{display_path(path)}:{line_no(text, start + block_offset + prop_map[prop_name])}: "
                    f"DSL_RULE_LEGACY theme '{theme_name}' componentDefaults must not use legacy property '{prop_name}'"
                )

    for block_name, (block_offset, block_text) in top_level_blocks.items():
        for prop_name, _prop_value, prop_offset in extract_property_values(block_text):
            if prop_name == "fileUrls" and block_name not in {"javaScript", "css"}:
                issues.append(
                    f"{display_path(path)}:{line_no(text, start + block_offset + prop_offset)}: "
                    f"DSL_RULE_PROP_SCOPE theme '{theme_name}' {block_name}.fileUrls is not allowed; use javaScript.fileUrls or css.fileUrls"
                )

    return issues


def lint_breadcrumb_page_number_contract(path: Path, text: str) -> list[str]:
    """Validate breadcrumb page-number references."""
    issues: list[str] = []
    if path.name != "breadcrumbs.apx" or "shared-components" not in path.parts:
        return issues

    for start, entry_name, block in find_component_blocks(text, "entry"):
        props = {name: (value, offset) for name, value, offset in extract_immediate_property_values(block)}
        if "pageNo" in props:
            issues.append(
                f"{display_path(path)}:{line_no(text, start + props['pageNo'][1])}: "
                f"DSL_RULE_LEGACY entry '{entry_name}' must not use legacy property 'pageNo'"
            )
        if "pageNumber" not in props:
            issues.append(
                f"{display_path(path)}:{line_no(text, start)}: "
                f"DSL_RULE_REQUIRED entry '{entry_name}' must define property 'pageNumber'"
            )

    return issues


def lint_dynamic_action_contract(path: Path, text: str) -> list[str]:
    """Validate dynamic action property and event contracts."""
    issues: list[str] = []

    for start, dynamic_action_name, block in find_component_blocks(text, "dynamicAction"):
        top_level_blocks = extract_top_level_blocks(block)
        when_meta = top_level_blocks.get("when")
        if when_meta:
            block_offset, block_text = when_meta
            props = {name: (value, offset) for name, value, offset in extract_property_values(block_text)}
            event_meta = props.get("event")
            if event_meta:
                event_value = clean_scalar_value(event_meta[0])
                normalized_event = event_value.strip()
                if normalized_event == "dialogClosed":
                    issues.append(
                        f"{display_path(path)}:{line_no(text, start + block_offset + event_meta[1])}: "
                        f"DSL_RULE_ENUM dynamicAction '{dynamic_action_name}' when.event must not use alias 'dialogClosed'; use 'apexafterclosedialog'"
                    )
                elif normalized_event and normalized_event not in DYNAMIC_ACTION_ALLOWED_EVENTS:
                    issues.append(
                        f"{display_path(path)}:{line_no(text, start + block_offset + event_meta[1])}: "
                        f"DSL_RULE_ENUM dynamicAction '{dynamic_action_name}' when.event must be one of the approved dynamic action events"
                    )

        for action_offset, action_name, action_block in find_immediate_component_blocks(block, "action"):
            top_level_blocks = extract_top_level_blocks(action_block)
            execution_meta = top_level_blocks.get("execution")
            if not execution_meta:
                continue

            block_offset, block_text = execution_meta
            props = {name: (value, offset) for name, value, offset in extract_property_values(block_text)}
            event_meta = props.get("event")
            if not event_meta:
                continue

            issues.append(
                f"{display_path(path)}:{line_no(text, start + action_offset + block_offset + event_meta[1])}: "
                f"DSL_RULE_PROP dynamicAction '{dynamic_action_name}' action '{action_name}' execution.event must not be emitted; current APEXLang compilers ignore it"
            )

    return issues


def lint_template_option_arrays(path: Path, text: str) -> list[str]:
    """Validate generic templateOptions arrays in .apx files."""
    issues: list[str] = []
    for options_match in re.finditer(r"(?ms)templateOptions\s*:\s*\[(.*?)\]", text):
        options_body = options_match.group(1)
        if "," in options_body:
            issues.append(
                f"{display_path(path)}:{line_no(text, options_match.start(1))}: "
                "TEMPLATE_OPTIONS_MULTILINE_REQUIRED_001 templateOptions must emit multi-value arrays "
                "with one accepted value per line and must not use comma-separated inline arrays"
            )
        for token_match in re.finditer(r"(?m)^\s*#DEFAULT#\S+\s*$", options_body):
            issues.append(
                f"{display_path(path)}:{line_no(text, options_match.start(1) + token_match.start())}: "
                "TEMPLATE_OPTIONS_DEFAULT_ATOMIC_001 templateOptions must keep '#DEFAULT#' as one standalone value"
            )
    return issues


def _lint_multiline_structure_segment(path: Path, full_text: str, segment_text: str, segment_offset: int) -> list[str]:
    """Reject compressed inline structural object syntax within one DSL segment."""
    issues: list[str] = []
    offset = 0

    for raw_line in segment_text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        if not re.match(r"^\s*[A-Za-z][A-Za-z0-9]*\s*:", line):
            offset += len(raw_line)
            continue

        inline_object_match = re.match(r"^\s*([A-Za-z][A-Za-z0-9]*)\s*:\s*\{(.*)$", line)
        if inline_object_match:
            trailing = inline_object_match.group(2).strip()
            if trailing:
                issues.append(
                    f"{display_path(path)}:{line_no(full_text, segment_offset + offset)}: "
                    "DSL_MULTILINE_STRUCTURE_REQUIRED_001 object-valued properties must emit "
                    "`name: {` on its own line and place nested properties on following lines"
                )

        offset += len(raw_line)

    return issues


def lint_multiline_structure_rules(path: Path, text: str, *, template_mode: bool = False) -> list[str]:
    """Reject compressed inline structural object syntax."""
    return _lint_multiline_structure_segment(path=path, full_text=text, segment_text=text, segment_offset=0)


def lint_live_compiler_slot_contract(path: Path, text: str) -> list[str]:
    """Validate slot values known to drift from the live compiler contract."""
    issues: list[str] = []

    for page_start, page_name, page_block in find_component_blocks(text, "page"):
        page_top_level_blocks = extract_top_level_blocks(page_block)
        appearance_meta = page_top_level_blocks.get("appearance")
        is_modal_dialog = False
        if appearance_meta:
            _appearance_offset, appearance_block = appearance_meta
            appearance_props = block_property_map(appearance_block)
            page_mode = clean_scalar_value(appearance_props.get("pageMode", ("", 0))[0])
            is_modal_dialog = page_mode == "modalDialog"

        if is_modal_dialog:
            for region_offset, region_name, region_block in find_immediate_component_blocks(page_block, "region"):
                region_top_level_blocks = extract_top_level_blocks(region_block)
                layout_meta = region_top_level_blocks.get("layout")
                if not layout_meta:
                    continue
                layout_offset, layout_block = layout_meta
                layout_props = block_property_map(layout_block)
                slot_meta = layout_props.get("slot")
                if not slot_meta:
                    continue
                slot_value, slot_offset = slot_meta
                if clean_scalar_value(slot_value).lower() == "body":
                    region_type = extract_item_type(region_block) or "region"
                    issues.append(
                        f"{display_path(path)}:{line_no(text, page_start + region_offset + layout_offset + slot_offset)}: "
                        f"DSL_RULE_SLOT page '{page_name}' modal {region_type} region '{region_name}' must use "
                        "layout.slot: contentBody instead of body"
                    )
        else:
            if not is_login_page(page_name, page_block):
                for region_offset, region_name, region_block in find_immediate_component_blocks(page_block, "region"):
                    region_top_level_blocks = extract_top_level_blocks(region_block)
                    layout_meta = region_top_level_blocks.get("layout")
                    if not layout_meta:
                        continue
                    layout_offset, layout_block = layout_meta
                    layout_props = block_property_map(layout_block)
                    slot_meta = layout_props.get("slot")
                    if not slot_meta:
                        continue
                    slot_value, slot_offset = slot_meta
                    if clean_scalar_value(slot_value) == "contentBody":
                        region_type = extract_item_type(region_block) or "region"
                        issues.append(
                            f"{display_path(path)}:{line_no(text, page_start + region_offset + layout_offset + slot_offset)}: "
                            f"DSL_RULE_SLOT page '{page_name}' standard {region_type} region '{region_name}' must use "
                            "layout.slot: body instead of contentBody"
                        )

        if is_login_page(page_name, page_block):
            continue

        for button_offset, button_name, button_block in find_immediate_component_blocks(page_block, "button"):
            button_props = {
                prop_name: clean_scalar_value(prop_value)
                for prop_name, prop_value, _prop_offset in extract_immediate_property_values(button_block)
            }
            is_create_button = (
                clean_scalar_value(button_name).lower() == "create"
                or button_props.get("buttonName", "").upper() == "CREATE"
            )
            if not is_create_button:
                continue
            button_top_level_blocks = extract_top_level_blocks(button_block)
            layout_meta = button_top_level_blocks.get("layout")
            if not layout_meta:
                continue
            layout_offset, layout_block = layout_meta
            layout_props = block_property_map(layout_block)
            slot_meta = layout_props.get("slot")
            if not slot_meta:
                continue
            slot_value, slot_offset = slot_meta
            if clean_scalar_value(slot_value).lower() == "next":
                issues.append(
                    f"{display_path(path)}:{line_no(text, page_start + button_offset + layout_offset + slot_offset)}: "
                    f"DSL_RULE_SLOT page '{page_name}' create button '{button_name}' must use a valid region "
                    "button slot such as CREATE instead of next"
                )

    return issues


def build_lint_context(path: Path, schema: dict[str, Any], validation_context: dict[str, Any] | None = None) -> LintContext:
    """Build a normalized context object for one lint target."""
    runtime_component_map = schema.get("_runtimeComponentMap")
    if not isinstance(runtime_component_map, dict):
        runtime_component_map = None
    return LintContext(
        path=path,
        text=path.read_text(encoding="utf-8", errors="ignore"),
        schema=schema,
        validation_context=validation_context or {},
        runtime_component_map=runtime_component_map,
    )


def _ctx_path_text_lint(fn: Callable[[Path, str], list[str]]) -> LintRunner:
    """Lift a path/text lint into a context-aware runner."""

    @wraps(fn)
    def runner(ctx: LintContext) -> list[str]:
        return fn(ctx.path, ctx.text)

    return runner


def _ctx_path_text_schema_lint(fn: Callable[[Path, str, dict], list[str]]) -> LintRunner:
    """Lift a path/text/schema lint into a context-aware runner."""

    @wraps(fn)
    def runner(ctx: LintContext) -> list[str]:
        return fn(ctx.path, ctx.text, ctx.schema)

    return runner


def _ctx_path_text_schema_validation_lint(fn: Callable[[Path, str, dict, dict[str, Any] | None], list[str]]) -> LintRunner:
    """Lift a path/text/schema/cross-file-context lint into a context-aware runner."""

    @wraps(fn)
    def runner(ctx: LintContext) -> list[str]:
        return fn(ctx.path, ctx.text, ctx.schema, ctx.validation_context)

    return runner


def _ctx_button_template_option_lint(*, template_mode: bool) -> LintRunner:
    """Bind the template-mode flag for button template-option validation."""

    def runner(ctx: LintContext) -> list[str]:
        return lint_button_template_option_contract(ctx.path, ctx.text, template_mode=template_mode)

    runner.__name__ = f"lint_button_template_option_contract_{'template' if template_mode else 'apx'}"
    return runner


def run_lints(ctx: LintContext, lints: list[LintRunner]) -> list[str]:
    """Run a sequence of lint runners against one target."""
    issues: list[str] = []
    for lint in lints:
        issues.extend(lint(ctx))
    return issues


def lint_page_item_schema_contracts(ctx: LintContext) -> list[str]:
    """Validate page-item blocks against the loaded schema."""
    issues: list[str] = []
    page_item_schema = ctx.schema["components"].get("pageItem", {})

    for start, item_name, block in find_component_blocks(ctx.text, "pageItem"):
        item_type = extract_item_type(block)
        if not item_type or item_type not in page_item_schema:
            continue

        item_schema = page_item_schema[item_type]
        allowed_blocks = set(item_schema.get("allowedBlocks", []))
        required_blocks = set(item_schema.get("requiredBlocks", []))
        top_level_blocks = extract_top_level_blocks(block)

        for block_name, (offset, _sub_block) in top_level_blocks.items():
            if block_name not in allowed_blocks:
                issues.append(
                    f"{display_path(ctx.path)}:{line_no(ctx.text, start + offset)}: "
                    f"DSL_RULE_BLOCK pageItem '{item_name}' type '{item_type}' does not allow block '{block_name}'"
                )

        missing_blocks = sorted(required_blocks - set(top_level_blocks.keys()))
        for block_name in missing_blocks:
            issues.append(
                f"{display_path(ctx.path)}:{line_no(ctx.text, start)}: "
                f"DSL_RULE_REQUIRED pageItem '{item_name}' type '{item_type}' must define block '{block_name}'"
            )

        component_label = f"pageItem '{item_name}' type '{item_type}'"
        for block_name, (block_offset, block_text) in top_level_blocks.items():
            block_meta = item_schema.get(block_name)
            if not is_block_meta(block_meta):
                continue
            lint_block_properties(
                issues=issues,
                path=ctx.path,
                text=ctx.text,
                component_start=start,
                component_label=component_label,
                block_name=block_name,
                block_offset=block_offset,
                block_text=block_text,
                block_meta=block_meta,
            )

    return issues


def lint_template_item_schema_examples(ctx: LintContext) -> list[str]:
    """Validate template examples and docs against page-item schema contracts."""
    issues: list[str] = []
    page_item_schema = ctx.schema["components"].get("pageItem", {})

    for item_type, item_schema in page_item_schema.items():
        if f"type: {item_type}" not in ctx.text:
            continue

        for block_name, block_meta in item_schema.items():
            if not is_block_meta(block_meta):
                continue
            allowed_properties = set(block_meta.get("allowedProperties", []))
            if not allowed_properties:
                continue
            for bad_match in re.finditer(rf"{re.escape(block_name)}\.([A-Za-z][A-Za-z0-9]*)", ctx.text):
                prop_name = bad_match.group(1)
                line_text = ctx.text[
                    ctx.text.rfind("\n", 0, bad_match.start()) + 1 : ctx.text.find("\n", bad_match.start())
                    if ctx.text.find("\n", bad_match.start()) != -1
                    else len(ctx.text)
                ]
                if "Do not document or emit" in line_text or "unless the schema explicitly permits" in line_text:
                    continue
                if prop_name not in allowed_properties:
                    issues.append(
                        f"{display_path(ctx.path)}:{line_no(ctx.text, bad_match.start())}: "
                        f"DSL_TEMPLATE_PROP template documents unsupported property {block_name}.{prop_name} for item type '{item_type}'"
                    )

        for _, _name, block in find_component_blocks(ctx.text, "pageItem"):
            if f"type: {item_type}" not in block:
                continue
            top_level_blocks = extract_top_level_blocks(block)
            for block_name, (block_offset, block_text) in top_level_blocks.items():
                block_meta = item_schema.get(block_name)
                if not is_block_meta(block_meta):
                    continue
                allowed_properties = set(block_meta.get("allowedProperties", []))
                if not allowed_properties:
                    continue
                for prop_name, _prop_value, prop_offset in extract_property_values(block_text):
                    if prop_name not in allowed_properties:
                        issues.append(
                            f"{display_path(ctx.path)}:{line_no(ctx.text, block_offset + prop_offset)}: "
                            f"DSL_TEMPLATE_PROP template example emits unsupported property {block_name}.{prop_name} for item type '{item_type}'"
                        )

    return issues


def lint_calendar_template_contract(ctx: LintContext) -> list[str]:
    """Validate calendar template docs and examples against canonical calendar rules."""
    issues: list[str] = []
    region_schema = ctx.schema["components"].get("region", {})
    calendar_schema = region_schema.get("calendar")
    if isinstance(calendar_schema, dict) and ("type: calendar" in ctx.text or "/calendar/" in display_path(ctx.path)):
        for legacy_name, canonical_name in CALENDAR_LEGACY_SETTING_ALIASES.items():
            for match in re.finditer(rf"(?m)^\s*{re.escape(legacy_name)}\s*:\s*.+$", ctx.text):
                issues.append(
                    f"{display_path(ctx.path)}:{line_no(ctx.text, match.start())}: "
                    f"DSL_TEMPLATE_VALUE template example must use canonical calendar property "
                    f"'{canonical_name}' instead of legacy alias '{legacy_name}'"
                )

        for match in re.finditer(r"(?m)^\s*showTime\s*:\s*.+$", ctx.text):
            issues.append(
                f"{display_path(ctx.path)}:{line_no(ctx.text, match.start())}: "
                "DSL_TEMPLATE_PROP template example emits unsupported property settings.showTime for region type 'calendar'"
            )

        for match in re.finditer(r"(?m)^\s*additionalCalendarViews\s*:\s*([A-Za-z][A-Za-z0-9]*)\s*$", ctx.text):
            token = match.group(1)
            if normalize_value(token) in CALENDAR_ADDITIONAL_VIEW_VALUES:
                continue
            issues.append(
                f"{display_path(ctx.path)}:{line_no(ctx.text, match.start(1))}: "
                "DSL_TEMPLATE_VALUE settings.additionalCalendarViews must use only: list, navigation"
            )

        for match in re.finditer(r"(?ms)^\s*additionalCalendarViews\s*:\s*\[(.*?)\]", ctx.text):
            body = match.group(1)
            for token_match in re.finditer(r"[A-Za-z][A-Za-z0-9]*", body):
                token = token_match.group(0)
                if normalize_value(token) in CALENDAR_ADDITIONAL_VIEW_VALUES:
                    continue
                issues.append(
                    f"{display_path(ctx.path)}:{line_no(ctx.text, match.start(1) + token_match.start())}: "
                    "DSL_TEMPLATE_VALUE settings.additionalCalendarViews must use only: list, navigation"
                )

        for match in re.finditer(r"(?ms)templateOptions\s*:\s*\[(.*?)\]", ctx.text):
            body = match.group(1)
            for token_match in re.finditer(r"(?m)^\s*#DEFAULT#\S+\s*$", body):
                issues.append(
                    f"{display_path(ctx.path)}:{line_no(ctx.text, match.start(1) + token_match.start())}: "
                    "DSL_TEMPLATE_VALUE templateOptions must keep '#DEFAULT#' as one standalone templateOptions value"
                )
            if re.search(r"(?m)^\s*t-Region--hideHeader\s*$", body) and re.search(r"(?m)^\s*js-addHiddenHeadingRoleDesc\s*$", body):
                issues.append(
                    f"{display_path(ctx.path)}:{line_no(ctx.text, match.start(1))}: "
                    "DSL_TEMPLATE_VALUE calendar template must keep 't-Region--hideHeader js-addHiddenHeadingRoleDesc' as one combined templateOptions value"
                )

    return issues


APX_LINTERS: list[LintRunner] = [
    _ctx_path_text_lint(lint_layout_scopes),
    _ctx_path_text_lint(lint_template_option_arrays),
    _ctx_path_text_lint(lint_multiline_structure_rules),
    _ctx_path_text_lint(lint_live_compiler_slot_contract),
    _ctx_button_template_option_lint(template_mode=False),
    _ctx_path_text_schema_validation_lint(lint_region_contracts),
    _ctx_path_text_lint(lint_dynamic_action_contract),
    _ctx_path_text_lint(lint_application_contract),
    _ctx_path_text_lint(lint_theme_contract),
    _ctx_path_text_lint(lint_translation_text_messages),
    _ctx_path_text_lint(lint_declarative_navigation_targets),
    _ctx_path_text_lint(lint_report_column_rendering),
    _ctx_path_text_lint(lint_classic_report_default_templates),
    _ctx_path_text_lint(lint_classic_report_hidden_column_headings),
    _ctx_path_text_lint(lint_smart_filter_results_regions),
    _ctx_path_text_lint(lint_image_upload_legacy_properties),
    _ctx_path_text_lint(lint_generated_security_contract),
    _ctx_path_text_lint(lint_inline_code_block_char_limits),
    _ctx_path_text_lint(lint_static_id_where_lower),
    _ctx_path_text_lint(lint_sql_lob_comparison_keys),
    _ctx_path_text_lint(lint_acl_role_declarations),
    _ctx_path_text_lint(lint_form_primary_key_contract),
    _ctx_path_text_lint(lint_form_edit_contract),
    _ctx_path_text_lint(lint_saved_report_visibility_contract),
    _ctx_path_text_lint(lint_faceted_search_current_facets_selector_contract),
    _ctx_path_text_lint(lint_interactive_report_link_column_contract),
    _ctx_path_text_lint(lint_page_item_layout_legacy_properties),
    _ctx_path_text_lint(lint_page_item_region_slots),
    _ctx_path_text_lint(lint_display_only_source_types),
    _ctx_path_text_schema_lint(lint_component_settings_contract),
    _ctx_path_text_schema_lint(lint_shared_entry_contract),
    _ctx_path_text_lint(lint_breadcrumb_page_number_contract),
    _ctx_path_text_schema_lint(lint_region_contract),
    lint_page_item_schema_contracts,
]

TEMPLATE_LINTERS: list[LintRunner] = [
    _ctx_button_template_option_lint(template_mode=True),
    _ctx_path_text_lint(lint_button_template_option_inventory),
    _ctx_path_text_lint(lint_multiline_structure_rules),
    _ctx_path_text_lint(lint_declarative_navigation_targets),
    _ctx_path_text_lint(lint_page_item_layout_legacy_properties),
    _ctx_path_text_lint(lint_page_item_region_slots),
    _ctx_path_text_lint(lint_display_only_source_types),
    _ctx_path_text_lint(lint_classic_report_hidden_column_headings),
    _ctx_path_text_lint(lint_smart_filter_results_regions),
    _ctx_path_text_lint(lint_image_upload_legacy_properties),
    _ctx_path_text_lint(lint_sql_lob_comparison_keys),
    lint_template_item_schema_examples,
    lint_calendar_template_contract,
]


def lint_apx_file(path: Path, schema: dict, validation_context: dict[str, Any] | None = None) -> list[str]:
    """Run all relevant validators for an application DSL file."""
    return run_lints(build_lint_context(path, schema, validation_context), APX_LINTERS)


def lint_template_file(path: Path, schema: dict) -> list[str]:
    """Run template-specific validators for Markdown template files."""
    return run_lints(build_lint_context(path, schema), TEMPLATE_LINTERS)


def main(argv: list[str]) -> int:
    """Parse CLI arguments, run validators, write reports, and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--templates", action="store_true", help="Validate Markdown item templates instead of .apx files")
    parser.add_argument("--report-path", default="", help="Optional JSON report output path.")
    parser.add_argument("paths", nargs="*", help="Files or directories to lint")
    args = parser.parse_args(argv)

    schema = load_schema()
    runtime_component_map = schema.get("_runtimeComponentMap")

    def report_runtime_meta() -> dict[str, Any]:
        if not isinstance(runtime_component_map, dict):
            return {
                "source": schema.get("_runtimeComponentMapSource", "component-attributes-only"),
                "buildID": None,
                "normalizerVersion": None,
            }
        return {
            "source": schema.get("_runtimeComponentMapSource", "query-valid-props"),
            "buildID": runtime_component_map.get("buildID"),
            "normalizerVersion": runtime_component_map.get("normalizerVersion"),
        }

    if args.templates:
        targets = collect_targets(args.paths, (".md",))
        if not targets:
            targets = sorted((ROOT / "references/policies/apexlang/templates").rglob("*.md"))
        issues: list[str] = []
        for target in targets:
            issues.extend(lint_template_file(target, schema))
        if args.report_path:
            report_path = Path(args.report_path).expanduser()
            if not report_path.is_absolute():
                report_path = (Path.cwd() / report_path).resolve()
            write_report(
                report_path,
                {
                    "mode": "templates",
                    "status": "fail" if issues else "pass",
                    "runtimeComponentMap": report_runtime_meta(),
                    "targets": [display_path(target) for target in targets],
                    "issues": [issue_to_record(issue) for issue in issues],
                },
            )
        if issues:
            print("APEXLANG_TEMPLATE_LINT_FAILED")
            for issue in issues:
                print(" -", issue)
            return 1
        print("APEXLANG_TEMPLATE_LINT_OK")
        return 0

    app_roots = collect_app_roots(args.paths)
    targets = collect_targets(args.paths, (".apx",))
    if not targets:
        targets = sorted((ROOT / "applications").rglob("*.apx"))
    validation_context = build_validation_context(targets)

    issues: list[str] = []
    for app_root in app_roots:
        issues.extend(lint_app_root_contract(app_root))
    for target in targets:
        issues.extend(lint_apx_file(target, schema, validation_context))

    if args.report_path:
        report_path = Path(args.report_path).expanduser()
        if not report_path.is_absolute():
            report_path = (Path.cwd() / report_path).resolve()
        write_report(
            report_path,
            {
                "mode": "dsl",
                "status": "fail" if issues else "pass",
                "runtimeComponentMap": report_runtime_meta(),
                "targets": sorted({display_path(target) for target in targets} | {display_path(app_root) for app_root in app_roots}),
                "issues": [issue_to_record(issue) for issue in issues],
            },
        )

    if issues:
        print("APEXLANG_DSL_LINT_FAILED")
        for issue in issues:
            print(" -", issue)
        return 1

    print("APEXLANG_DSL_LINT_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
