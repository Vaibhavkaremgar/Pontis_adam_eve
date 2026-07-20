import re

files_and_patterns = [
    ("app/db/repositories.py", r"row\.role\s*=\s*_clamp_text"),
    ("app/services/apify_enrichment_service.py", r"profile\.role\s*="),
    ("app/services/candidate_selection_service.py", r"profile\.role\s*=\s*selected_role"),
    ("app/services/candidate_service.py", r"role\s*=\s*profile\.role\s+if\s+profile"),
    ("app/services/outreach_service.py", r"their_role\s*=\s*candidate_profile\.role"),
    ("app/services/resend_inbound_service.py", r"candidate_profile\.role\s*="),
    ("app/services/results_service.py", r"cp\.role\s+AS\s+candidate_role"),
    ("app/services/serpapi_sourcing_service.py", r"row\.current_company\s+or\s+row\.company"),
    ("app/services/ranking/semantic_reranking_service.py", r'"candidateRole":\s*profile\.role'),
    ("app/services/automation_service.py", r'"candidateRole":\s*profile\.role'),
]

for path, pattern in files_and_patterns:
    content = open(path, "rb").read()
    text = content.decode("utf-8", errors="replace")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.search(pattern, line):
            # Show the raw bytes of this line
            raw_line = content.split(b"\n")[i] if b"\r\n" not in content else content.split(b"\r\n")[i]
            print(f"{path}:{i+1}: {repr(line.strip())}")
            break
    else:
        print(f"{path}: PATTERN NOT FOUND: {pattern}")
