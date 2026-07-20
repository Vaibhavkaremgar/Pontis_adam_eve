"""
apply_candidate_entity_fixes.py
Fixes all CandidateProfileEntity .role / .company references to use
the canonical .current_role / .current_company columns.
"""
import py_compile, sys, os

FIXES = [
    # (file_path, old_text, new_text)

    # ── repositories.py ──────────────────────────────────────────────────────
    (
        "app/db/repositories.py",
        "        row.role = _clamp_text(role, max_length=255)\n        row.company = _clamp_text(company, max_length=255)",
        "        row.current_role = _clamp_text(role, max_length=255)\n        row.current_company = _clamp_text(company, max_length=255)",
    ),

    # ── apify_enrichment_service.py ──────────────────────────────────────────
    # _extract_candidate_identity: getattr(profile, "role") / getattr(profile, "company")
    (
        "app/services/apify_enrichment_service.py",
        '        "headline": _first_non_empty(getattr(profile, "role", ""), raw_data.get("headline"), raw_data.get("title")),\n        "linkedin_url": _extract_profile_url(profile),\n        "current_company": _first_non_empty(getattr(profile, "current_company", ""), getattr(profile, "company", ""), raw_data.get("current_company"), raw_data.get("company")),',
        '        "headline": _first_non_empty(getattr(profile, "current_role", ""), raw_data.get("headline"), raw_data.get("title")),\n        "linkedin_url": _extract_profile_url(profile),\n        "current_company": _first_non_empty(getattr(profile, "current_company", ""), raw_data.get("current_company"), raw_data.get("company")),',
    ),
    # enrich_candidate_with_apify: profile.role / profile.company assignments
    (
        "app/services/apify_enrichment_service.py",
        "    profile.role = profile_payload[\"headline\"] or profile.role\n    profile.company = profile_payload[\"current_company\"] or profile.company",
        "    profile.current_role = profile_payload[\"headline\"] or profile.current_role\n    profile.current_company = profile_payload[\"current_company\"] or profile.current_company",
    ),

    # ── candidate_selection_service.py ───────────────────────────────────────
    (
        "app/services/candidate_selection_service.py",
        "                profile.role = selected_role",
        "                profile.current_role = selected_role",
    ),
    (
        "app/services/candidate_selection_service.py",
        "                profile.company = selected_company",
        "                profile.current_company = selected_company",
    ),

    # ── candidate_service.py ─────────────────────────────────────────────────
    # Line 1847: profile.role
    (
        "app/services/candidate_service.py",
        "            role = profile.role if profile else \"\"",
        "            role = profile.current_role if profile else \"\"",
    ),
    # Lines 2129-2130: profile.company / profile.role
    (
        "app/services/candidate_service.py",
        "        company = (profile.company if profile else str(payload.get(\"company\") or \"\")).strip()\n        role = (profile.role if profile else str(payload.get(\"role\") or \"\")).strip() or \"Unknown Role\"",
        "        company = (profile.current_company if profile else str(payload.get(\"company\") or \"\")).strip()\n        role = (profile.current_role if profile else str(payload.get(\"role\") or \"\")).strip() or \"Unknown Role\"",
    ),
    # Lines 4854-4855: getattr(profile, "role") / getattr(profile, "company")
    (
        "app/services/candidate_service.py",
        '                "role": (getattr(profile, "role", "") or (snapshot_candidate.role if snapshot_candidate else "") or "").strip(),\n                "company": (getattr(profile, "company", "") or (snapshot_candidate.company if snapshot_candidate else "") or "").strip(),',
        '                "role": (getattr(profile, "current_role", "") or (snapshot_candidate.role if snapshot_candidate else "") or "").strip(),\n                "company": (getattr(profile, "current_company", "") or (snapshot_candidate.company if snapshot_candidate else "") or "").strip(),',
    ),
    # Lines 5010-5011: row.role / row.company (CandidateProfileEntity -> CandidateResult constructor)
    (
        "app/services/candidate_service.py",
        "                role=row.role,\n                company=row.company,",
        "                role=row.current_role,\n                company=row.current_company,",
    ),

    # ── outreach_service.py ──────────────────────────────────────────────────
    (
        "app/services/outreach_service.py",
        "    their_role = candidate_profile.role or \"your background\"\n    their_company = candidate_profile.company",
        "    their_role = candidate_profile.current_role or \"your background\"\n    their_company = candidate_profile.current_company",
    ),
    (
        "app/services/outreach_service.py",
        "            f\"{sanitize_prompt_block('Candidate current role', candidate_profile.role or 'unknown', max_length=120)}\\n\"\n            f\"{sanitize_prompt_block('Candidate current company', candidate_profile.company or 'unknown', max_length=120)}\\n\"",
        "            f\"{sanitize_prompt_block('Candidate current role', candidate_profile.current_role or 'unknown', max_length=120)}\\n\"\n            f\"{sanitize_prompt_block('Candidate current company', candidate_profile.current_company or 'unknown', max_length=120)}\\n\"",
    ),

    # ── resend_inbound_service.py ────────────────────────────────────────────
    (
        "app/services/resend_inbound_service.py",
        "    candidate_profile.role = str(profile.get(\"headline\") or candidate_profile.role or \"\").strip()\n    candidate_profile.company = current_company or candidate_profile.company or \"\"\n",
        "    candidate_profile.current_role = str(profile.get(\"headline\") or candidate_profile.current_role or \"\").strip()\n    candidate_profile.current_company = current_company or candidate_profile.current_company or \"\"\n",
    ),
    # Also fix the line that reads candidate_profile.role to set current_title
    (
        "app/services/resend_inbound_service.py",
        "    candidate_profile.current_title = candidate_profile.role",
        "    candidate_profile.current_title = candidate_profile.current_role",
    ),

    # ── results_service.py ───────────────────────────────────────────────────
    (
        "app/services/results_service.py",
        "                    cp.role                    AS candidate_role,\n                    cp.company                 AS candidate_company,",
        "                    cp.current_role            AS candidate_role,\n                    cp.current_company         AS candidate_company,",
    ),

    # ── serpapi_sourcing_service.py ──────────────────────────────────────────
    (
        "app/services/serpapi_sourcing_service.py",
        "                name_company = _normalize_lower(f\"{row.name}|{row.current_company or row.company}\")",
        "                name_company = _normalize_lower(f\"{row.name}|{row.current_company}\")",
    ),

    # ── semantic_reranking_service.py ────────────────────────────────────────
    # candidate_document_text call (CandidateResult schema — these are fine, but profile.role/company below are not)
    # Lines 402-403: profile.role / profile.company (CandidateProfileEntity)
    (
        "app/services/ranking/semantic_reranking_service.py",
        '            "candidateRole": profile.role,\n            "candidateCompany": profile.company,',
        '            "candidateRole": profile.current_role,\n            "candidateCompany": profile.current_company,',
    ),
    (
        "app/services/ranking/semantic_reranking_service.py",
        '            "role": profile.role,\n            "company": profile.company,',
        '            "role": profile.current_role,\n            "company": profile.current_company,',
    ),

    # ── automation_service.py ────────────────────────────────────────────────
    # Lines 268, 309: profile.role on CandidateProfileEntity
    (
        "app/services/automation_service.py",
        '                        "candidateRole": profile.role or "",',
        '                        "candidateRole": profile.current_role or "",',
    ),
    (
        "app/services/automation_service.py",
        "        body = f\"You haven't reviewed {profile.name or profile.candidate_id} for {profile.role or 'this role'} yet\"",
        "        body = f\"You haven't reviewed {profile.name or profile.candidate_id} for {profile.current_role or 'this role'} yet\"",
    ),
]

errors = []
applied = []

for path, old, new in FIXES:
    try:
        content = open(path, encoding="utf-8", errors="replace").read()
        if old not in content:
            # Try with \r\n line endings
            old_crlf = old.replace("\n", "\r\n")
            new_crlf = new.replace("\n", "\r\n")
            if old_crlf in content:
                content = content.replace(old_crlf, new_crlf)
                open(path, "w", encoding="utf-8").write(content)
                applied.append(f"DONE (crlf): {path}")
            else:
                errors.append(f"NOT FOUND: {path!r}\n  old={old[:80]!r}")
        else:
            content = content.replace(old, new)
            open(path, "w", encoding="utf-8").write(content)
            applied.append(f"DONE: {path}")
    except Exception as e:
        errors.append(f"ERROR: {path}: {e}")

print("\n=== APPLIED ===")
for a in applied:
    print(a)

print("\n=== ERRORS ===")
for e in errors:
    print(e)

# Validate compilation
print("\n=== COMPILE CHECK ===")
files_to_check = list({path for path, _, _ in FIXES})
for path in files_to_check:
    try:
        py_compile.compile(path, doraise=True)
        print(f"OK: {path}")
    except py_compile.PyCompileError as e:
        print(f"FAIL: {path}: {e}")
