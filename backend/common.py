from __future__ import annotations
import json, pathlib, sqlite3, os

try:
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).with_name('.env'))
except ImportError:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = pathlib.Path(os.environ.get('DATA_DIR', ROOT / 'data' / 'raw' / 'sap-o2c-data'))
def configured_db_path():
    direct=os.environ.get('DATABASE_PATH')
    if direct:
        path=pathlib.Path(direct)
        return path if path.is_absolute() else ROOT/path
    url=os.environ.get('DATABASE_URL','')
    if not url: return ROOT/'data'/'processed'/'o2c.sqlite3'
    if not url.startswith('sqlite:///'): raise ValueError('DATABASE_URL must use SQLite; set DATABASE_PATH for a local SQLite file path.')
    location=url[len('sqlite:///'):]
    if os.name=='nt' and re.match(r'^/[A-Za-z]:/',location): location=location[1:]
    path=pathlib.Path(location)
    return path if path.is_absolute() else ROOT/path

import re
DB_PATH = configured_db_path()
REPORT = ROOT / 'data' / 'reports' / 'profile.json'

def table_files():
    if not DATA.exists(): return {}
    return {p.name: sorted(p.glob('*.jsonl')) for p in DATA.iterdir() if p.is_dir()}

def rows_for(table):
    for path in table_files().get(table, []):
        with path.open(encoding='utf-8-sig') as f:
            for n, line in enumerate(f, 1):
                try:
                    row=json.loads(line)
                    if isinstance(row, dict): yield path, n, row
                    else: yield path, n, {'__parse_error__': 'JSONL record must be a JSON object', '__raw__': line.rstrip('\n')}
                except Exception as e:
                    yield path, n, {'__parse_error__': str(e), '__raw__': line.rstrip('\n')}

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(DB_PATH)
    con.row_factory=sqlite3.Row
    return con

def item_id(value):
    """Normalize numeric SAP item numbers for joins only; source payloads stay untouched."""
    text=str(value or '')
    return str(int(text)) if text.isdigit() else text
