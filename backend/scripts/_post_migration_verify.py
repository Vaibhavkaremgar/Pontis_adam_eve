"""Post-migration read-only schema verification."""
import sys
print("=== POST-MIGRATION SCHEMA VERIFICATION ===", flush=True)

from sqlalchemy import create_engine, inspect, text

DB_URL = "postgresql://postgres:wYynBCKGyAlRNKkFrAIIiVdtqRYIyDxH@tokaido.proxy.rlwy.net:26186/railway"
engine = create_engine(DB_URL, connect_args={"connect_timeout": 20})
conn = engine.connect()
insp = inspect(engine)
print("connected ok", flush=True)

# --- alembic_version ---
av = conn.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).fetchall()
print(f"\nalembic_version rows: {[r[0] for r in av]}", flush=True)

# --- candidate_voice_intakes ---
print("\n=== candidate_voice_intakes ===", flush=True)
cols = {c["name"]: c for c in insp.get_columns("candidate_voice_intakes")}
print(f"columns: {list(cols.keys())}", flush=True)
for expected in ["id", "candidate_id", "transcript", "voice_notes", "status", "created_at", "completed_at"]:
    present = expected in cols
    print(f"  {expected}: {'OK' if present else 'MISSING'}", flush=True)

fks = insp.get_foreign_keys("candidate_voice_intakes")
print(f"foreign_keys:", flush=True)
for fk in fks:
    print(f"  {fk['constrained_columns']} -> {fk['referred_table']}.{fk['referred_columns']} ondelete={fk['options'].get('ondelete')}", flush=True)

idxs = {i["name"]: i for i in insp.get_indexes("candidate_voice_intakes")}
print(f"indexes: {list(idxs.keys())}", flush=True)
cvi_idx = idxs.get("idx_cvi_candidate")
if cvi_idx:
    print(f"  idx_cvi_candidate columns: {cvi_idx['column_names']} OK", flush=True)
else:
    print("  idx_cvi_candidate: MISSING", flush=True)

cvi_rows = conn.execute(text("SELECT COUNT(*) FROM candidate_voice_intakes")).scalar()
print(f"row_count: {cvi_rows}", flush=True)

# --- candidate_job_recommendations ---
print("\n=== candidate_job_recommendations ===", flush=True)
cjr_cols = {c["name"]: c for c in insp.get_columns("candidate_job_recommendations")}
print(f"columns: {list(cjr_cols.keys())}", flush=True)

expected_cols = [
    "tracked_at", "viewed_at", "agency_id", "job_role", "status", "updated_at",
    "applied_at", "application_status", "application_notes", "ats_application_id",
]
for col in expected_cols:
    present = col in cjr_cols
    dtype = str(cjr_cols[col]["type"]) if present else "N/A"
    print(f"  {col}: {'OK' if present else 'MISSING'} ({dtype})", flush=True)

cjr_idxs = {i["name"]: i for i in insp.get_indexes("candidate_job_recommendations")}
print(f"indexes: {list(cjr_idxs.keys())}", flush=True)

for idx_name in ["idx_cjr_candidate_status", "ix_cjr_application_status"]:
    if idx_name in cjr_idxs:
        print(f"  {idx_name}: OK columns={cjr_idxs[idx_name]['column_names']}", flush=True)
    else:
        print(f"  {idx_name}: MISSING", flush=True)

# Check partial index definition via pg_indexes
partial = conn.execute(text(
    "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_cjr_candidate_status'"
)).scalar()
print(f"  idx_cjr_candidate_status definition: {partial}", flush=True)

cjr_rows = conn.execute(text("SELECT COUNT(*) FROM candidate_job_recommendations")).scalar()
print(f"row_count: {cjr_rows}", flush=True)

conn.close()
print("\n=== DONE ===", flush=True)
