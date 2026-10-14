"""Load every JSONL part to SQLite while preserving source records and diagnostics."""
import json, sqlite3
from .common import DB_PATH, connect, table_files, rows_for
from .storage import engine, source_table, source_value, projection_tables
from sqlalchemy import MetaData

def ingest():
    con=connect(); cur=con.cursor()
    cur.executescript('''CREATE TABLE IF NOT EXISTS source_records(id INTEGER PRIMARY KEY, table_name TEXT NOT NULL, source_file TEXT NOT NULL, source_line INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(table_name,source_file,source_line)); CREATE INDEX IF NOT EXISTS ix_source_table ON source_records(table_name); CREATE TABLE IF NOT EXISTS ingestion_errors(table_name TEXT,source_file TEXT,source_line INTEGER,error TEXT,raw TEXT);''')
    cur.execute('DELETE FROM source_records'); cur.execute('DELETE FROM ingestion_errors'); con.commit(); counts={}
    sql_engine=engine()
    for table in table_files():
        n=0; payloads=[]; fields=set()
        for path,line,row in rows_for(table):
            if '__parse_error__' in row:
                cur.execute('INSERT INTO ingestion_errors VALUES(?,?,?,?,?)',(table,path.name,line,row['__parse_error__'],row['__raw__'])); continue
            cur.execute('INSERT INTO source_records(table_name,source_file,source_line,payload) VALUES(?,?,?,?)',(table,path.name,line,json.dumps(row,ensure_ascii=False)))
            n+=1; fields.update(row); payloads.append({'__source_file':path.name,'__source_line':line,**{k:source_value(v) for k,v in row.items()}})
        con.commit()
        md=MetaData(); relational=source_table(md,table,fields)
        relational.drop(sql_engine,checkfirst=True); relational.create(sql_engine)
        if payloads:
            with sql_engine.begin() as sqlcon:
                for start in range(0,len(payloads),1000): sqlcon.execute(relational.insert(),payloads[start:start+1000])
        counts[table]=n
    con.close()
    md=MetaData(); projection_tables(md); md.create_all(sql_engine)
    sql_engine.dispose(); return counts

if __name__=='__main__':
    c=ingest(); print(f'Ingested {sum(c.values())} records into {DB_PATH}; {len(c)} tables.')
