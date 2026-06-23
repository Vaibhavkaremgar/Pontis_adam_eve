"""Fix _diversity_guard to only suppress genuinely identical queries."""
path = "app/services/serpapi_sourcing_service.py"
data = open(path, "r", encoding="utf-8").read()

# The correct diversity guard:
# - Each named family (role_query_1, stack_query_1, etc.) is always kept
#   because they are *by design* different sourcing angles.
# - Only suppress if the same QUERY TEXT appears more than once
#   (true near-duplicate, i.e. overlap of meaningful tokens >= 0.95)
NEW_GUARD = (
    "# Tokens present in every X-Ray query that carry no diversity signal\n"
    "_DIVERSITY_BOILERPLATE = {\n"
    "    \"site\", \"linkedin\", \"com\", \"or\", \"and\", \"view\",\n"
    "    \"profile\", \"about\", \"experience\", \"skills\", \"company\", \"jobs\", \"hiring\",\n"
    "}\n"
    "\n"
    "\n"
    "def _meaningful_tokens_for_diversity(query: str) -> set[str]:\n"
    "    return set(_tokenize_query_terms(query)) - _DIVERSITY_BOILERPLATE\n"
    "\n"
    "\n"
    "def _diversity_guard(\n"
    "    layers: list[XRayQueryLayer],\n"
    "    *,\n"
    "    max_overlap_ratio: float = 0.95,\n"
    ") -> list[XRayQueryLayer]:\n"
    "    \"\"\"\n"
    "    Sprint 3 diversity enforcement.\n"
    "\n"
    "    Each named query family (role_query_1, stack_query_1, adjacent_title_1,\n"
    "    domain_query_1, recall_query_1, project_query_1) is kept by design.\n"
    "    Only suppress a layer when its meaningful-token set overlaps >= 95%\n"
    "    with an already-admitted layer, meaning the queries are virtually\n"
    "    identical (a true near-duplicate).\n"
    "    \"\"\"\n"
    "    admitted: list[tuple[XRayQueryLayer, set[str]]] = []\n"
    "    result: list[XRayQueryLayer] = []\n"
    "    for layer in layers:\n"
    "        if not layer.enabled or not layer.query:\n"
    "            result.append(layer)\n"
    "            continue\n"
    "        tokens = _meaningful_tokens_for_diversity(layer.query)\n"
    "        suppress_reason = \"\"\n"
    "        for _admitted_layer, admitted_tokens in admitted:\n"
    "            if not admitted_tokens or not tokens:\n"
    "                continue\n"
    "            overlap = len(tokens & admitted_tokens)\n"
    "            smaller = min(len(tokens), len(admitted_tokens))\n"
    "            if smaller > 0 and overlap / smaller >= max_overlap_ratio:\n"
    "                suppress_reason = (\n"
    "                    f\"overlap_ratio={round(overlap / smaller, 2):.2f} \"\n"
    "                    f\"vs {_admitted_layer.layer_type}\"\n"
    "                )\n"
    "                break\n"
    "        if suppress_reason:\n"
    "            logger.info(\"diversity_guard_suppressed layer=%s reason=%s\", layer.layer_type, suppress_reason)\n"
    "            result.append(XRayQueryLayer(\n"
    "                layer_type=layer.layer_type,\n"
    "                query=layer.query,\n"
    "                enabled=False,\n"
    "                pages=layer.pages,\n"
    "                signals={**dict(layer.signals or {}), \"suppressed_by_diversity_guard\": True, \"suppress_reason\": suppress_reason},\n"
    "            ))\n"
    "        else:\n"
    "            admitted.append((layer, tokens))\n"
    "            result.append(layer)\n"
    "    return result\n"
    "\n"
    "\n"
)

GUARD_MARKER = "def _diversity_guard("
LOOKUP_MARKER = "# ---------------------------------------------------------------------------\n# Sprint 3"

positions = []
idx = 0
while True:
    pos = data.find(GUARD_MARKER, idx)
    if pos == -1:
        break
    positions.append(pos)
    idx = pos + len(GUARD_MARKER)

print(f"Found {len(positions)} _diversity_guard definitions")

lookup_start = data.find(LOOKUP_MARKER)

if len(positions) >= 1 and lookup_start != -1:
    before = data[:positions[0]]
    after_lookup = data[lookup_start:]

    # Remove any _diversity_guard inside after_lookup
    while True:
        pos2 = after_lookup.find(GUARD_MARKER)
        if pos2 == -1:
            break
        end2 = after_lookup.find("\ndef build_linkedin_xray_query_layers(", pos2)
        if end2 == -1:
            break
        after_lookup = after_lookup[:pos2] + after_lookup[end2 + 1:]
        print("Removed a _diversity_guard from after_lookup section")

    data = before + NEW_GUARD + after_lookup
    open(path, "w", encoding="utf-8").write(data)
    remaining = data.count(GUARD_MARKER)
    print(f"Done. Remaining _diversity_guard definitions: {remaining}")
    print("_DIVERSITY_BOILERPLATE present:", "_DIVERSITY_BOILERPLATE" in data)
    print("threshold 0.95 present:", "0.95" in data)
else:
    print("ERROR: markers not found", len(positions), lookup_start)
