"""Pre-migration read-only inspection of the shared Railway DB."""
import sys
print("=== PRE-MIGRATION INSPECTION ===", flush=True)

try:
    from sqlalchemy import create_engine, inspect, text
    print("sqlalchemy imported ok", flush=True)
except Exception as e:
    print(f"IMPORT ERROR: {e}", flush=True)
    sys.exit(1)

DB_URL = "postgresql://postgres:wYynBCKGyAlRNKkFrAIIiVdtqRYIyDxH@tokaido.proxy.rlwy.net:26186/railway"
print(f"host: {DB_URL.split('@')[1]}", flush=True)

try:
    engine = create_engine(DB_URL, connect_args={"connect_timeout": 20})
    conn = engine.connect()
    print("connected ok", flush=True)
except Exception as e:
    print(f"CONNECTION ERROR: {e}", flush=True)
    sys.exit(1)

try:
    insp = inspect(engine)
    tables = insp.get_table_names()
    print(f"total tables: {len(tables)}", flush=True)

    for tname in ["candidate_voice_intakes", "candidate_job_recommendations"]:
        exists = tname in tables
        print(f"\n--- {tname} ---", flush=True)
        print(f"  exists: {exists}", flush=True)
        if exists:
            cols = [c["name"] for c in insp.get_columns(tname)]
            print(f"  columns: {cols}", flush=True)
            idxs = [i["name"] for i in insp.get_indexes(tname)]
            print(f"  indexes: {idxs}", flush=True)
            if tname == "candidate_voice_intakes":
                fks = insp.get_foreign_keys(tname)
                print(f"  foreign_keys: {fks}", flush=True)
            n = conn.execute(text(f"SELECT COUNT(*) FROM {tname}")).scalar()
            print(f"  row_count: {n}", flush=True)

    # alembic_version
    av = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    print(f"\nalembic_version rows: {av}", flush=True)

except Exception as e:
    print(f"INSPECTION ERROR: {e}", flush=True)
    import traceback; traceback.print_exc()
finally:
    conn.close()
    print("\n=== DONE ===", flush=True)
