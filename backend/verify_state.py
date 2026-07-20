import re

files = [
    "app/db/repositories.py",
    "app/services/apify_enrichment_service.py",
    "app/services/candidate_selection_service.py",
    "app/services/candidate_service.py",
    "app/services/outreach_service.py",
    "app/services/resend_inbound_service.py",
    "app/services/results_service.py",
    "app/services/serpapi_sourcing_service.py",
    "app/services/ranking/semantic_reranking_service.py",
    "app/services/automation_service.py",
]

pattern = re.compile(r'\.(role|company)\b')

for path in files:
    lines = open(path, encoding="utf-8", errors="replace").readlines()
    hits = []
    for i, line in enumerate(lines):
        if pattern.search(line):
            hits.append(f"  {i+1}: {line.rstrip()}")
    if hits:
        print(f"\n{path}:")
        for h in hits:
            print(h)
    else:
        print(f"\n{path}: CLEAN")
