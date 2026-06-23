"""Sprint 3 validation script — run from backend/ directory."""
import os, sys
os.environ.setdefault("DATABASE_URL", "sqlite:///./validate_sprint3.db")
os.environ.setdefault("SERPAPI_API_KEY", "test-key")
os.environ.setdefault("SERPAPI_ENABLED", "true")

from app.services.serpapi_sourcing_service import (
    build_linkedin_xray_query_layers,
    _diversity_guard,
    _select_primary_query_layers,
    XRayQueryLayer,
)
from app.services.sourcing_diagnostics import (
    resolve_no_results_reason,
    QueryFamilyDiagnostic,
    build_query_family_diagnostics,
    SourcingDiagnostics,
)
from app.services.sourcing.xray_service import _fallback_broadening_layers

print("\n" + "="*60)
print("SPRINT 3 VALIDATION")
print("="*60)

# ── 1. Six query families ────────────────────────────────────────
print("\n[1] 6-family query generation")
layers = build_linkedin_xray_query_layers(
    role="Senior Backend Engineer",
    seniority="Senior",
    skills=["Python", "FastAPI", "PostgreSQL", "Kafka"],
    location="Bangalore",
    company_stage="Series B",
    hiring_preferences="ownership",
    industry="fintech",
    leadership_expectations="",
)
assert len(layers) == 6, f"Expected 6 layers, got {len(layers)}"
family_names = [l.layer_type for l in layers]
assert "role_query_1"    in family_names, "Missing role_query_1"
assert "stack_query_1"   in family_names, "Missing stack_query_1"
assert "project_query_1" in family_names, "Missing project_query_1"
assert "adjacent_title_1" in family_names, "Missing adjacent_title_1"
assert "domain_query_1"  in family_names, "Missing domain_query_1"
assert "recall_query_1"  in family_names, "Missing recall_query_1"
print(f"  OK — {len(layers)} families generated: {family_names}")

# ── 2. Family purposes logged in signals ────────────────────────
print("\n[2] Family purposes in signals")
for l in layers:
    purpose = (l.signals or {}).get("family_purpose", "")
    assert purpose, f"Layer {l.layer_type} missing family_purpose signal"
    suppressed = (l.signals or {}).get("suppressed_by_diversity_guard", False)
    status = "SUPPRESSED" if suppressed else "active"
    print(f"  {status:10s} {l.layer_type}: {purpose}")
    print(f"             {l.query[:100]}")
print("  OK")

# ── 3. Recall family has no location ────────────────────────────
print("\n[3] Recall family — no location constraint")
recall = next(l for l in layers if l.layer_type == "recall_query_1")
loc = (recall.signals or {}).get("location", "UNSET")
assert loc == "", f"recall_query_1 should have empty location, got: {loc!r}"
print(f"  OK — recall location='{loc}'")

# ── 4. All 6 active layers selected ─────────────────────────────
print("\n[4] _select_primary_query_layers allows up to 6")
selected = _select_primary_query_layers(layers, max_layers=6)
active_selected = [l for l in selected if l.enabled]
assert len(active_selected) >= 3, f"Expected >=3 active, got {len(active_selected)}"
print(f"  OK — {len(active_selected)} active layers selected from {len(selected)}")

# ── 5. Diversity guard suppresses near-duplicates ───────────────
print("\n[5] Diversity guard")
identical_query = "site:linkedin.com/in backend engineer python fastapi"
dup_layers = [
    XRayQueryLayer(layer_type="role_query_1",    query=identical_query, enabled=True),
    XRayQueryLayer(layer_type="stack_query_1",   query=identical_query, enabled=True),
    XRayQueryLayer(layer_type="project_query_1", query="site:linkedin.com/in data scientist ml tensorflow", enabled=True),
]
guarded = _diversity_guard(dup_layers)
suppressed_count = sum(1 for l in guarded if not l.enabled)
assert suppressed_count == 1, f"Expected 1 suppressed, got {suppressed_count}"
assert guarded[0].enabled, "First layer should remain active"
assert not guarded[1].enabled, "Duplicate should be suppressed"
assert guarded[2].enabled, "Different query should stay active"
print(f"  OK — 1 duplicate suppressed, 2 distinct queries kept active")

# ── 6. Fallback broadening triggers on zero results ─────────────
print("\n[6] Fallback broadening — zero results trigger")
fb_layers = _fallback_broadening_layers(
    role="Senior Backend Engineer",
    seniority="Senior",
    skills=["Python", "FastAPI"],
    location="Bangalore",
    raw_result_count=0,
    strict_layer_count=0,
)
assert len(fb_layers) <= 2, f"Fallback should be capped at 2, got {len(fb_layers)}"
assert len(fb_layers) >= 1, "Should generate at least 1 fallback layer"
for fb in fb_layers:
    assert (fb.signals or {}).get("is_fallback") is True, "Fallback must be marked is_fallback=True"
    assert (fb.signals or {}).get("fallback_trigger"), "Fallback must have trigger reason"
    print(f"  {fb.layer_type}: {(fb.signals or {}).get('family_purpose','')}")
print("  OK — fallback layers are capped and marked correctly")

# ── 7. Fallback does NOT trigger when results are sufficient ─────
print("\n[7] Fallback broadening — no trigger when results >= threshold")
fb_none = _fallback_broadening_layers(
    role="Backend Engineer",
    seniority="",
    skills=["Python"],
    location="",
    raw_result_count=10,
    strict_layer_count=3,
)
assert fb_none == [], f"Should return empty when results sufficient, got {fb_none}"
print("  OK — no fallback generated when results are sufficient")

# ── 8. No-results reason codes ──────────────────────────────────
print("\n[8] No-results reason codes")
cases = [
    dict(serpapi_disabled=True,  quota_exhausted=False, raw_count=0, deduped_count=0, ranked_count=0, delivered_count=0, expected="provider_disabled"),
    dict(serpapi_disabled=False, quota_exhausted=True,  raw_count=0, deduped_count=0, ranked_count=0, delivered_count=0, expected="quota_exhausted"),
    dict(serpapi_disabled=False, quota_exhausted=False, raw_count=0, deduped_count=0, ranked_count=0, delivered_count=0, expected="query_too_narrow"),
    dict(serpapi_disabled=False, quota_exhausted=False, raw_count=5, deduped_count=0, ranked_count=0, delivered_count=0, expected="filters_reduced_recall"),
    dict(serpapi_disabled=False, quota_exhausted=False, raw_count=5, deduped_count=3, ranked_count=0, delivered_count=0, expected="all_ranked_filtered"),
    dict(serpapi_disabled=False, quota_exhausted=False, raw_count=5, deduped_count=3, ranked_count=2, delivered_count=0, expected="all_filtered"),
    dict(serpapi_disabled=False, quota_exhausted=False, raw_count=5, deduped_count=3, ranked_count=2, delivered_count=2, expected=""),
]
for c in cases:
    expected = c.pop("expected")
    got = resolve_no_results_reason(**c)
    assert got == expected, f"Expected '{expected}', got '{got}' for {c}"
    print(f"  OK  {expected or '(delivered)'!r:30s} <- {c}")

# ── 9. QueryFamilyDiagnostic dataclass ──────────────────────────
print("\n[9] QueryFamilyDiagnostic")
fd = QueryFamilyDiagnostic(
    query_family_name="role_query_1",
    query_family_purpose="exact title + must-have stack + location",
    actual_query_text='site:linkedin.com/in "backend engineer"',
    is_fallback=False,
    raw_serpapi_results_count=8,
    normalized_candidates_count=7,
    deduped_candidates_count=6,
    ranked_candidates_count=4,
    produced_delivered_candidates=True,
)
assert fd.query_family_name == "role_query_1"
assert fd.produced_delivered_candidates is True
print(f"  OK — family={fd.query_family_name} raw={fd.raw_serpapi_results_count} delivered={fd.produced_delivered_candidates}")

print("\n" + "="*60)
print("ALL SPRINT 3 CHECKS PASSED")
print("="*60 + "\n")
