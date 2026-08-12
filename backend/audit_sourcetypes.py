import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv()
from app.core.config import QDRANT_URL, QDRANT_API_KEY, INTERNAL_CANDIDATE_COLLECTION_NAME
from qdrant_client import QdrantClient
from collections import Counter

client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
all_points = []
offset = None
while True:
    kw = dict(collection_name=INTERNAL_CANDIDATE_COLLECTION_NAME,
              limit=100, with_payload=True, with_vectors=False)
    if offset is not None:
        kw["offset"] = offset
    resp = client.scroll(**kw)
    batch, nxt = resp if isinstance(resp, tuple) else (resp, None)
    batch = list(batch or [])
    if not batch:
        break
    all_points.extend(batch)
    if nxt is None:
        break
    offset = nxt

print("Total scrolled:", len(all_points), flush=True)

st_counts = Counter()
combo_counts = Counter()
for pt in all_points:
    pl = getattr(pt, "payload", None) or {}
    st   = str(pl.get("sourceType") or "<missing>")
    aid  = bool(pl.get("agencyId"))
    crid = bool(pl.get("candidateRecordId"))
    st_counts[st] += 1
    combo_counts[(st, aid, crid)] += 1

print("\nsourceType distribution:")
for k, v in st_counts.most_common():
    print(f"  {k!r}: {v}")

print("\nBreakdown (sourceType, has_agencyId, has_candidateRecordId):")
for k, v in sorted(combo_counts.items()):
    print(f"  st={k[0]!r}  aid={k[1]}  crid={k[2]}  n={v}")

# Show all unique sourceType values for points missing agencyId
print("\nPoints missing agencyId — sourceType values:")
missing_aid = [pt for pt in all_points if not (getattr(pt,"payload",None) or {}).get("agencyId")]
print(f"  Count: {len(missing_aid)}")
for pt in missing_aid[:5]:
    pl = getattr(pt,"payload",None) or {}
    print(f"  id={pt.id}  sourceType={pl.get('sourceType')!r}  candidateRecordId={pl.get('candidateRecordId')!r}")
