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
classes = []
re_class = re.compile(r'^class\s+(\w+)\(Base\):', re.MULTILINE)
indices = [(m.start(), m.group(1)) for m in re_class.finditer(text)]
for i, (pos, name) in enumerate(indices):
    start = pos
    end = indices[i+1][0] if i+1 < len(indices) else len(text)
    block = text[start:end]
    tab_match = re.search(r'__tablename__\s*=\s*["\']([^"\']+)["\']', block)
    tab = tab_match.group(1) if tab_match else None
    cols = set()
    for mc in re.finditer(r'mapped_column\(([^\n)]*)\)', block):
        args = mc.group(1)
        m = re.match(r'\s*["\']([^"\']+)["\']', args)
        if m:
            cols.add(m.group(1))
        else:
            # no explicit column name, use variable name if possible
            # find line before mapped_column
            line = text[:start+mc.start()].splitlines()[-1]
            name_match = re.match(r'\s*([\w_]+)\s*:', line)
            if name_match:
                cols.add(name_match.group(1))
    classes.append((name, tab, cols))
for name, tab, cols in classes:
    print('CLASS', name, 'TABLE', tab)
    if tab is None:
        continue
    actual = set(c['name'] for c in ins.get_columns(tab)) if tab in ins.get_table_names() else None
    if actual is None:
        print('  TABLE NOT IN DB')
        continue
    missing = actual - cols
    extra = cols - actual
    print('  mapped', sorted(cols))
    print('  actual', sorted(actual))
    print('  missing', sorted(missing)[:30])
    print('  extra', sorted(extra)[:30])
    print()
