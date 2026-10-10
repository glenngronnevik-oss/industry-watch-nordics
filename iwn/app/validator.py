#!/usr/bin/env python3
"""Validate and prune Industry Watch data.

Used by the portal (ingest API and GitHub sync) and as a CLI that is
compatible with the original OneDrive validate.py:

    python3 validator.py path/to/industry-watch-data.js

The file format is either `window.IW_DATA = {JSON};` or plain JSON.
"""
from __future__ import annotations

import datetime as dt
import json
import sys

REQ = ["id", "date", "category", "vendor", "title", "summary", "tungsten_product",
       "signal", "impact", "tungsten_angle", "source", "url"]
CATS = {"PDF editors", "Print management", "Capture & workflow", "Standards & format", "Tungsten"}
SIGNALS = {"Threat", "Opportunity", "Parity", "Watch"}
IMPACTS = {"High", "Medium", "Low"}
TOP_KEYS = ["updated", "updated_label", "briefing", "items", "matrices", "watchlist"]
MAX_ITEMS = 60


class ValidationError(ValueError):
    """Raised when the data does not meet the schema."""


def parse_text(raw: str) -> dict:
    """Accept `window.IW_DATA = {...};` or a bare JSON object."""
    raw = raw.strip()
    if not raw:
        raise ValidationError("empty payload")
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
    except ValueError as exc:
        raise ValidationError("no JSON object found") from exc
    try:
        data = json.loads(raw[start:end])
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError("top level must be an object")
    return data


def _date(value, what: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValidationError(f"bad date in {what}: {value!r}") from exc


def validate_and_prune(data: dict) -> dict:
    """Return a cleaned copy. Raises ValidationError on schema problems."""
    for k in TOP_KEYS:
        if k not in data:
            raise ValidationError(f"missing top-level key {k}")
    if not isinstance(data["items"], list):
        raise ValidationError("items must be a list")
    try:
        dt.datetime.fromisoformat(data["updated"])
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"bad updated timestamp {data['updated']!r}") from exc

    cutoff = _date(data["updated"], "updated") - dt.timedelta(days=int(data.get("window_days", 240)))
    seen, kept = set(), []
    for it in data["items"]:
        if not isinstance(it, dict):
            raise ValidationError("every item must be an object")
        missing = [k for k in REQ if not it.get(k)]
        if missing:
            raise ValidationError(f"item {it.get('id')} missing {missing}")
        if it["category"] not in CATS:
            raise ValidationError(f"bad category {it['category']!r} in {it['id']}")
        if it["signal"] not in SIGNALS:
            raise ValidationError(f"bad signal in {it['id']}")
        if it["impact"] not in IMPACTS:
            raise ValidationError(f"bad impact in {it['id']}")
        if not str(it["url"]).startswith("https://"):
            raise ValidationError(f"bad url in {it['id']}")
        d = _date(it["date"], it["id"])
        if it.get("added"):
            _date(it["added"], f"{it['id']}.added")
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        if d >= cutoff or it["category"] == "Tungsten":
            kept.append(it)
    kept.sort(key=lambda i: i["date"], reverse=True)
    out = dict(data)
    out["items"] = kept[:MAX_ITEMS]

    blob = json.dumps(out, ensure_ascii=False)
    if "—" in blob:
        raise ValidationError("em dash found in data; replace it with a comma, semicolon or parentheses")
    return out


def to_js(data: dict) -> str:
    """Serialize as the JS file the page loads. `</` is escaped for safety."""
    blob = json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")
    return "window.IW_DATA = " + blob + ";\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validator.py path/to/industry-watch-data.js", file=sys.stderr)
        return 2
    path = argv[1]
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    try:
        data = validate_and_prune(parse_text(raw))
    except ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(to_js(data))
    print(f"OK: {len(data['items'])} items, updated {data['updated_label']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
