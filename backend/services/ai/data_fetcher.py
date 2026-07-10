"""Serialization and aggregation helpers for AI-queried data."""

import json
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

_VALID_OPS = {"sum", "avg", "count", "min", "max"}
_OP_MAP = {
    "sum": "$sum",
    "avg": "$avg",
    "count": "$sum",  # count uses $sum: 1
    "min": "$min",
    "max": "$max",
}

# Only allow safe field names (alphanumeric, underscores, dots for nested)
_SAFE_FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,63}$")


def _is_safe_field(name: str | None) -> bool:
    """Validate that a field name is safe for use in MongoDB expressions."""
    if not name:
        return False
    return bool(_SAFE_FIELD_RE.match(name)) and not name.startswith("$")


def serialize(docs: list, max_docs: int = 200, total_count: int | None = None, has_summary: bool = False) -> str:
    """Convert MongoDB documents to a JSON string safe for LLM prompts."""
    trimmed = docs[:max_docs]
    for d in trimmed:
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()

    header = ""
    if total_count is not None and total_count > len(trimmed):
        if has_summary:
            header = f"[Showing {len(trimmed)} of {total_count} matching records. Pre-computed summary covers all {total_count}.]\n"
        else:
            header = f"[Showing {len(trimmed)} of {total_count} matching records.]\n"

    return header + json.dumps(trimmed, default=str, indent=None)


def build_aggregation_pipeline(match_filter: dict, aggregations: list) -> list[dict]:
    """Convert safe aggregation specs into MongoDB aggregation definitions.

    Each spec: {"field": "amount", "op": "sum", "group_by": "stage"}.
    Returns a list of wrapper dicts, each containing the normalized
    aggregation metadata (op, field, group_by) plus a pipeline key
    with the MongoDB aggregation stages to execute. Returns an empty
    list if nothing valid.
    """
    if not aggregations:
        return []

    pipelines = []
    for spec in aggregations:
        op = spec.get("op", "").lower()
        if op not in _VALID_OPS:
            continue

        field = spec.get("field")
        group_by = spec.get("group_by")

        # Validate field names to prevent injection
        if field and not _is_safe_field(field):
            logger.warning(f"Rejected unsafe aggregation field: {field}")
            continue
        if group_by and not _is_safe_field(group_by):
            logger.warning(f"Rejected unsafe aggregation group_by: {group_by}")
            continue

        # count doesn't need a field; others do
        if op != "count" and not field:
            continue

        group_id = f"${group_by}" if group_by else None
        if op == "count":
            group_stage = {"_id": group_id, "value": {"$sum": 1}}
        else:
            group_stage = {"_id": group_id, "value": {_OP_MAP[op]: f"${field}"}}

        pipeline = [{"$match": match_filter}, {"$group": group_stage}]

        if group_by:
            pipeline.append({"$sort": {"value": -1}})

        pipelines.append({
            "op": op,
            "field": field,
            "group_by": group_by,
            "pipeline": pipeline,
        })

    return pipelines


async def run_aggregations(db, collection: str, match_filter: dict, aggregations: list) -> list[dict]:
    """Run aggregation specs against a collection and return structured results.

    Returns a list like:
    [
        {"label": "sum of amount by stage", "op": "sum", "field": "amount", "group_by": "stage",
         "results": [{"group": "Closed Won", "value": 1234567}, ...]}
    ]
    """
    if not aggregations:
        return []

    pipelines = build_aggregation_pipeline(match_filter, aggregations)
    results = []

    for entry in pipelines:
        try:
            max_groups = 500
            cursor = db[collection].aggregate(entry["pipeline"])
            formatted = []
            truncated = False
            async for row in cursor:
                if len(formatted) >= max_groups:
                    truncated = True
                    break
                group_key = row.get("_id") or "Total"
                value = row.get("value", 0)
                if isinstance(value, float):
                    value = round(value, 2)
                formatted.append({"group": group_key, "value": value})

            op = entry["op"]
            field = entry.get("field") or ""
            group_by = entry.get("group_by") or ""
            label = f"{op} of {field}".strip() if field else "count"
            if group_by:
                label += f" by {group_by}"
            if truncated:
                label += f" (showing top {max_groups})"

            results.append({
                "label": label,
                "op": op,
                "field": field,
                "group_by": group_by,
                "results": formatted,
            })
        except Exception as e:
            logger.warning(f"Aggregation failed for {collection}.{entry['op']}: {e}")
            continue

    return results


def format_summary(all_summaries: dict[str, list[dict]]) -> str:
    """Format aggregation results from multiple collections into a readable summary string.

    all_summaries: {"zoho_deals": [...results...], "deals": [...results...]}
    Includes collection names to disambiguate identical labels across sources.
    """
    if not all_summaries or all(not v for v in all_summaries.values()):
        return ""

    lines = ["PRE-COMPUTED SUMMARY (calculated by the database — use these numbers, do not recalculate):"]
    for collection, results in all_summaries.items():
        if not results:
            continue
        coll_label = collection.replace("_", " ").title()
        lines.append(f"  [{coll_label}]")
        for r in results:
            label = r["label"]
            entries = r["results"]
            if not entries:
                continue
            if len(entries) == 1 and entries[0]["group"] == "Total":
                lines.append(f"    {label}: {entries[0]['value']}")
            else:
                lines.append(f"    {label}:")
                for e in entries:
                    lines.append(f"      {e['group']}: {e['value']}")

    return "\n".join(lines) if len(lines) > 1 else ""
