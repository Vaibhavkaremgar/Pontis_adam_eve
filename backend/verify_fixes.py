import re, py_compile

# Verify the fixes landed correctly
checks = [
    # (file, pattern_that_must_exist, description)
    ("app/db/repositories.py", r"row\.current_role\s*=\s*_clamp_text", "repositories upsert current_role"),
    ("app/db/repositories.py", r"row\.current_company\s*=\s*_clamp_text", "repositories upsert current_company"),
    ("app/services/apify_enrichment_service.py", r"profile\.current_role\s*=", "apify profile.current_role"),
    ("app/services/apify_enrichment_service.py", r"profile\.current_company\s*=", "apify profile.current_company"),
    ("app/services/candidate_selection_service.py", r"profile\.current_role\s*=\s*selected_role", "selection profile.current_role"),
    ("app/services/candidate_selection_service.py", r"profile\.current_company\s*=\s*selected_company", "selection profile.current_company"),
    ("app/services/candidate_service.py", r"role\s*=\s*profile\.current_role\s+if\s+profile", "candidate_service profile.current_role L1847"),
    ("app/services/candidate_service.py", r"profile\.current_company\s+if\s+profile", "candidate_service profile.current_company L2129"),
    ("app/services/candidate_service.py", r'getattr\(profile,\s*"current_role"', "candidate_service getattr current_role L4854"),
    ("app/services/candidate_service.py", r"role=row\.current_role", "candidate_service row.current_role L5010"),
    ("app/services/candidate_service.py", r"company=row\.current_company", "candidate_service row.current_company L5011"),
    ("app/services/outreach_service.py", r"candidate_profile\.current_role\s+or", "outreach current_role"),
    ("app/services/outreach_service.py", r"candidate_profile\.current_company", "outreach current_company"),
    ("app/services/resend_inbound_service.py", r"candidate_profile\.current_role\s*=", "resend current_role"),
    ("app/services/resend_inbound_service.py", r"candidate_profile\.current_company\s*=", "resend current_company"),
    ("app/services/results_service.py", r"cp\.current_role\s+AS\s+candidate_role", "results cp.current_role"),
    ("app/services/results_service.py", r"cp\.current_company\s+AS\s+candidate_company", "results cp.current_company"),
    ("app/services/serpapi_sourcing_service.py", r"row\.current_company\b", "serpapi row.current_company"),
    ("app/services/ranking/semantic_reranking_service.py", r'"candidateRole":\s*profile\.current_role', "reranking candidateRole"),
    ("app/services/ranking/semantic_reranking_service.py", r'"candidateCompany":\s*profile\.current_company', "reranking candidateCompany"),
    ("app/services/automation_service.py", r'"candidateRole":\s*profile\.current_role', "automation candidateRole"),
    ("app/services/automation_service.py", r"profile\.current_role\s+or\s+'this role'", "automation body current_role"),
]

print("=== VERIFICATION ===")
all_ok = True
for path, pattern, desc in checks:
    text = open(path, encoding="utf-8", errors="replace").read()
    if re.search(pattern, text):
        print(f"  OK  {desc}")
    else:
        print(f"  MISSING  {desc}  ({path})")
        all_ok = False

# Also check no broken .role / .company remain on CandidateProfileEntity instances
broken_patterns = [
    ("app/db/repositories.py", r"row\.role\s*=\s*_clamp_text", "repositories broken row.role"),
    ("app/services/apify_enrichment_service.py", r"profile\.role\s*=", "apify broken profile.role"),
    ("app/services/candidate_selection_service.py", r"profile\.role\s*=\s*selected_role", "selection broken profile.role"),
    ("app/services/candidate_service.py", r"role\s*=\s*profile\.role\s+if\s+profile", "candidate_service broken profile.role L1847"),
    ("app/services/candidate_service.py", r"profile\.company\s+if\s+profile\s+else\s+str\(payload", "candidate_service broken profile.company L2129"),
    ("app/services/candidate_service.py", r'getattr\(profile,\s*"role"', "candidate_service broken getattr role L4854"),
    ("app/services/candidate_service.py", r"role=row\.role,", "candidate_service broken row.role L5010"),
    ("app/services/outreach_service.py", r"candidate_profile\.role\s+or", "outreach broken role"),
    ("app/services/resend_inbound_service.py", r"candidate_profile\.role\s*=", "resend broken role"),
    ("app/services/results_service.py", r"cp\.role\s+AS\s+candidate_role", "results broken cp.role"),
    ("app/services/ranking/semantic_reranking_service.py", r'"candidateRole":\s*profile\.role', "reranking broken profile.role"),
    ("app/services/automation_service.py", r'"candidateRole":\s*profile\.role\b', "automation broken profile.role"),
]

print("\n=== BROKEN PATTERNS (should all be CLEAN) ===")
for path, pattern, desc in broken_patterns:
    text = open(path, encoding="utf-8", errors="replace").read()
    if re.search(pattern, text):
        print(f"  STILL BROKEN  {desc}")
        all_ok = False
    else:
        print(f"  CLEAN  {desc}")

# Compile check
print("\n=== COMPILE CHECK ===")
files = list({p for p, _, _ in checks})
for path in sorted(files):
    try:
        py_compile.compile(path, doraise=True)
        print(f"  OK  {path}")
    except py_compile.PyCompileError as e:
        print(f"  FAIL  {path}: {e}")
        all_ok = False

print(f"\n{'ALL CHECKS PASSED' if all_ok else 'SOME CHECKS FAILED'}")
