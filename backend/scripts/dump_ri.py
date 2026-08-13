from dotenv import load_dotenv; load_dotenv('.env')
import os, json, sys
sys.path.insert(0, '.')
from sqlalchemy import create_engine, text
from app.db.database_url import normalize_database_url
engine = create_engine(normalize_database_url(os.environ['DATABASE_URL']), pool_pre_ping=True)
JOB_ID = '6ac18079-c93d-4951-9d24-7b5fb1c9066e'
with engine.connect() as c:
    row = c.execute(text('SELECT structured_data FROM job_descriptions WHERE id = :jid'), {'jid': JOB_ID}).mappings().first()
    sd = dict(row['structured_data']) if row and row['structured_data'] else {}
    ri = sd.get('recruiterIntelligence', {})
    print('=== recruiterIntelligence keys ===')
    print(list(ri.keys()))
    print()
    print('=== transcript field (len) ===')
    t = ri.get('transcript', '')
    print(f'len={len(t)}')
    print(repr(t[:500]))
    print()
    print('=== voiceTranscript field (len) ===')
    vt = ri.get('voiceTranscript', '')
    print(f'len={len(vt)}')
    print(repr(vt[:500]))
    print()
    print('=== voiceSummary field ===')
    print(repr(ri.get('voiceSummary', '(MISSING)')))
    print()
    print('=== voice_summary field ===')
    print(repr(ri.get('voice_summary', '(MISSING)')))
    print()
    print('=== stage / status ===')
    print('stage:', ri.get('stage'))
    print('status:', ri.get('status'))
    print()
    print('=== ALL top-level structured_data keys ===')
    for k in sorted(sd.keys()):
        v = sd[k]
        if isinstance(v, str):
            print(f'  {k}: str len={len(v)} -> {v[:80]!r}')
        elif isinstance(v, dict):
            print(f'  {k}: dict keys={list(v.keys())[:6]}')
        elif isinstance(v, list):
            print(f'  {k}: list len={len(v)}')
        else:
            print(f'  {k}: {type(v).__name__} = {v!r}')
