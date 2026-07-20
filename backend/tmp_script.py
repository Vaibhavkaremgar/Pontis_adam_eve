import re, os
from pathlib import Path
from sqlalchemy import create_engine, inspect
env = Path(r'C:\Users\hp\pontis\backend\.env')
for line in env.read_text().splitlines():
    if line.startswith('DATABASE_URL='):
        os.environ['DATABASE_URL'] = line.split('=', 1)[1].strip()
engine = create_engine(os.environ['DATABASE_URL'])
ins = inspect(engine)
text = Path(r'C:\Users\hp\pontis\backend\app\models\entities.py').read_text()
table_names = re.findall(r'__tablename__\s*=\s*["\']([^"\']+)["\']', text)
print('Model tablenames:', table_names)
print('Missing in DB:')
for t in table_names:
    if t not in ins.get_table_names():
        print(' ', t)
