"""Diagnose alembic_version state and migration graph."""
import sys
print("=== ALEMBIC VERSION DIAGNOSIS ===", flush=True)

from sqlalchemy import create_engine, text

DB_URL = "postgresql://postgres:wYynBCKGyAlRNKkFrAIIiVdtqRYIyDxH@tokaido.proxy.rlwy.net:26186/railway"
engine = create_engine(DB_URL, connect_args={"connect_timeout": 20})
conn = engine.connect()

rows = conn.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).fetchall()
print(f"alembic_version rows ({len(rows)}):", flush=True)
for r in rows:
    print(f"  {r[0]}", flush=True)

conn.close()
print("=== DONE ===", flush=True)
