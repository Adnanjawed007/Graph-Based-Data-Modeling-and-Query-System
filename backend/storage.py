"""SQLAlchemy storage for source-preserving relational staging tables and graph projection."""
from __future__ import annotations
import json, re
from sqlalchemy import create_engine, MetaData, Table, Column, Text, Integer, URL
from sqlalchemy.engine import Engine
from .common import DB_PATH

def engine() -> Engine:
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    return create_engine(URL.create('sqlite+pysqlite',database=str(DB_PATH)),future=True)

def safe_table(name:str)->str:
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',name): raise ValueError('Invalid logical table name')
    return 'source_'+name

def source_table(metadata:MetaData,name:str,fields:set[str])->Table:
    if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',f) for f in fields):
        raise ValueError(f'Unsupported source column name in {name}')
    return Table(safe_table(name),metadata,
        Column('__source_file',Text,primary_key=True),Column('__source_line',Integer,primary_key=True),
        *[Column(f,Text,nullable=True) for f in sorted(fields)],
        extend_existing=True)

def source_value(value):
    if value is None:return None
    if isinstance(value,bool):return 'true' if value else 'false'
    if isinstance(value,(dict,list)):return json.dumps(value,ensure_ascii=False,separators=(',',':'))
    return str(value)

def projection_tables(metadata:MetaData):
    entities=Table('canonical_entities',metadata,
        Column('node_id',Text,primary_key=True),Column('entity_type',Text,index=True,nullable=False),
        Column('business_identifier',Text,index=True,nullable=False),Column('metadata_json',Text,nullable=False),
        Column('source_table',Text,nullable=True))
    edges=Table('document_relationships',metadata,
        Column('edge_id',Text,primary_key=True),Column('source_node',Text,index=True,nullable=False),
        Column('target_node',Text,index=True,nullable=False),Column('relationship_type',Text,index=True,nullable=False),
        Column('source_table',Text,nullable=False),Column('validation_status',Text,nullable=False),Column('metadata_json',Text,nullable=False))
    validations=Table('relationship_validation_results',metadata,
        Column('validation_id',Integer,primary_key=True,autoincrement=True),Column('from_table',Text,nullable=False),
        Column('from_fields_json',Text,nullable=False),Column('to_table',Text,nullable=False),Column('to_fields_json',Text,nullable=False),
        Column('matched_rows_exact',Integer,nullable=False),Column('matched_rows_normalized',Integer,nullable=False),
        Column('reference_rows',Integer,nullable=False),Column('unmatched_rows',Integer,nullable=False),Column('match_percent',Text,nullable=False),Column('status',Text,nullable=False))
    return entities,edges,validations

def persist_validation_results(rows):
    md=MetaData(); _,_,table=projection_tables(md)
    with engine().begin() as conn:
        md.create_all(conn); conn.execute(table.delete())
        conn.execute(table.insert(),[{'from_table':r['from_table'],'from_fields_json':json.dumps(r['from_fields']),
          'to_table':r['to_table'],'to_fields_json':json.dumps(r['to_fields']),'matched_rows_exact':r['matched_rows_exact'],
          'matched_rows_normalized':r['matched_rows_item_zero_normalized'],'reference_rows':r['reference_rows'],
          'unmatched_rows':r['unmatched_rows_after_item_normalization'],'match_percent':str(r['match_percent_after_item_normalization']),
          'status':r['status']} for r in rows])

def persist_graph(graph):
    md=MetaData(); entities,edges,_=projection_tables(md)
    with engine().begin() as conn:
        md.create_all(conn)
        conn.execute(entities.delete());conn.execute(edges.delete())
        conn.execute(entities.insert(),[
          {'node_id':node,'entity_type':data['entity_type'],'business_identifier':str(data.get('identifier','')),
           'metadata_json':json.dumps(data.get('data',{}),ensure_ascii=False),'source_table':data.get('source_table')}
          for node,data in graph.nodes(data=True)])
        conn.execute(edges.insert(),[
          {'edge_id':f'{a}|{d.get("relationship",key)}|{b}|{i}','source_node':a,'target_node':b,
           'relationship_type':d.get('relationship',str(key)),'source_table':d.get('source_table','unknown'),
           'validation_status':d.get('validation','verified'),'metadata_json':json.dumps(d,ensure_ascii=False)}
          for i,(a,b,key,d) in enumerate(graph.edges(keys=True,data=True))])
    return graph.number_of_nodes(),graph.number_of_edges()
