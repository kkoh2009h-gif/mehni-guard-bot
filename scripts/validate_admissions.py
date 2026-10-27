#!/usr/bin/env python3
"""Validate the official admissions registry without inventing eligibility rules."""
import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "admissions_2026_2027.json"
data = json.loads(DATA_PATH.read_text(encoding="utf-8"))

assert data.get("academic_year") == "2026-2027", "Unexpected academic year"
assert data.get("status") == "partial_verified_dataset", "Do not label partial data as complete"
sources = {item["id"]: item for item in data.get("sources", [])}
assert sources, "No official sources defined"
for source in sources.values():
    parsed = urlparse(source.get("url", ""))
    assert parsed.scheme == "https" and parsed.netloc, f"Invalid source URL: {source.get('id')}"

pathways = data.get("extraction", {}).get("verified_pathways", [])
assert pathways, "No verified technical pathways found"
for i, item in enumerate(pathways, start=1):
    assert item.get("department"), f"Pathway {i}: missing department"
    assert item.get("college_group"), f"Pathway {i}: missing institution group"
    assert item.get("vocational_branches"), f"Pathway {i}: missing vocational branches"
    assert isinstance(item.get("source_page"), int), f"Pathway {i}: missing printed source page"
    assert item.get("minimum_average") is None, (
        f"Pathway {i}: a pathway mapping must not be treated as a competitive cutoff"
    )
    assert all(isinstance(branch, str) and branch.strip() for branch in item["vocational_branches"])

private = data.get("private_admissions", {})
minimums = private.get("published_minimums", [])
for i, item in enumerate(minimums, start=1):
    assert item.get("department"), f"Private minimum {i}: missing department"
    assert item.get("study_mode") in {"morning", "evening"}, f"Private minimum {i}: invalid study mode"
    assert isinstance(item.get("minimum_average"), (int, float)), f"Private minimum {i}: missing average"
    assert 0 <= item["minimum_average"] <= 100, f"Private minimum {i}: average out of range"
    assert item.get("minimum_average_type") == "official_minimum_to_apply"
    assert isinstance(item.get("source_page"), int), f"Private minimum {i}: missing source page"

private_inputs = private.get("vocational_admission_inputs", [])
for i, item in enumerate(private_inputs, start=1):
    assert item.get("department"), f"Private input {i}: missing department"
    assert item.get("vocational_branches"), f"Private input {i}: missing branches"
    assert isinstance(item.get("source_page"), int), f"Private input {i}: missing source page"
    assert item.get("minimum_average") is None, (
        f"Private input {i}: do not infer a minimum average from an eligibility mapping"
    )

print(
    "Admissions registry OK: "
    f"{len(pathways)} technical pathways, "
    f"{len(private_inputs)} private vocational mappings, "
    f"{len(minimums)} explicit private minimums. "
    "Dataset remains marked partial."
)
