import os, re

results = []
for r, d, fs in os.walk('app'):
    for f in fs:
        if not f.endswith('.py'):
            continue
        path = os.path.join(r, f)
        try:
            lines = open(path, encoding='utf-8', errors='replace').readlines()
        except Exception:
            continue
        for i, l in enumerate(lines):
            if re.search(r'\.role\b|\.company\b', l):
                results.append(path + ':' + str(i+1) + ': ' + l.rstrip())

with open('role_company_results.txt', 'w', encoding='utf-8') as out:
    out.write('\n'.join(results))

print(f"Found {len(results)} hits, written to role_company_results.txt")
