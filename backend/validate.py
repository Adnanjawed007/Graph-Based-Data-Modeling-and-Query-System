"""Run database and reference validation: python -m backend.validate"""
import json,sqlite3
from .common import DB_PATH,REPORT
from .profile import profile
from .storage import safe_table,persist_validation_results

def validate():
    if not DB_PATH.exists(): raise SystemExit('Database is missing; run python -m backend.ingest first.')
    with sqlite3.connect(DB_PATH) as con:
        integrity=con.execute('PRAGMA integrity_check').fetchone()[0]
        source_counts=dict(con.execute('SELECT table_name,COUNT(*) FROM source_records GROUP BY table_name'))
        error_count=con.execute('SELECT COUNT(*) FROM ingestion_errors').fetchone()[0]
        relational=[]
        for table,n in source_counts.items():
            actual=con.execute(f'SELECT COUNT(*) FROM "{safe_table(table)}"').fetchone()[0]
            relational.append({'table':table,'source_records':n,'relational_rows':actual,'consistent':n==actual})
    profile_result=profile()
    persist_validation_results(profile_result['relationships'])
    result={'database':str(DB_PATH),'sqlite_integrity':integrity,'ingestion_errors':error_count,'tables':relational,
            'profile_relationships':profile_result['relationships']}
    result['ok']=integrity=='ok' and error_count==0 and all(t['consistent'] for t in relational)
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    out=REPORT.parent/'validation.json';out.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'ok':result['ok'],'sqlite_integrity':integrity,'logical_tables':len(relational),'ingestion_errors':error_count,'report':str(out)},indent=2))
    return result

if __name__=='__main__': validate()
