from __future__ import annotations
import json, re, collections
import os
from decimal import Decimal, InvalidOperation
from datetime import date
from typing import Any
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from .common import connect, DB_PATH, REPORT, item_id, table_files
from .ingest import ingest
from .llm import plan_question, generate_answer
from .schemas import (HealthResponse, QueryValidationResponse, QueryResponse, DatasetStatsResponse,
                      GraphResponse, EntitySearchResponse, TopProductsResponse, QualityReportResponse)
from .narration import narrate_answer
import networkx as nx

app=FastAPI(title='Graph-Based Data Modeling and Query System',version='0.1.0',description='Read-only investigation API over SAP-style order-to-cash JSONL records.')
origins=[x.strip() for x in os.environ.get('FRONTEND_ORIGIN','http://localhost:5173').split(',') if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['*'])
DOMAIN_RE=re.compile(r'\b(orders?|deliveries|delivery|billing|invoices?|customers?|products?|payments?|journals?|receivables?|sales|incomplete|unbilled)\b',re.I)
_GRAPH_CACHE=None

def records(table:str):
    if not DB_PATH.exists(): return []
    with connect() as c:
        return [json.loads(r['payload']) for r in c.execute('SELECT payload FROM source_records WHERE table_name=?',(table,))]

def ensure_db():
    if not DB_PATH.exists():
        ingest(); return
    try:
        with connect() as con: populated=con.execute('SELECT 1 FROM source_records LIMIT 1').fetchone() is not None
    except Exception: populated=False
    if not populated and table_files(): ingest()

def graph():
    global _GRAPH_CACHE
    if _GRAPH_CACHE is not None: return _GRAPH_CACHE
    ensure_db(); g=nx.MultiDiGraph()
    specs=[('sales_order_headers','sales_order','salesOrder'),('sales_order_items','sales_order_item',None),('outbound_delivery_headers','delivery','deliveryDocument'),('outbound_delivery_items','delivery_item',None),('billing_document_headers','billing_document','billingDocument'),('billing_document_items','billing_item',None),('business_partners','customer','businessPartner'),('products','product','product'),('plants','plant','plant')]
    rows={t:records(t) for t,_,_ in specs}
    for table,typ,key in specs:
        for r in rows[table]:
            ident=r.get(key) if key else None
            if not ident and typ.endswith('_item'):
                parent='salesOrder' if typ=='sales_order_item' else ('deliveryDocument' if typ=='delivery_item' else 'billingDocument')
                item='salesOrderItem' if typ=='sales_order_item' else ('deliveryDocumentItem' if typ=='delivery_item' else 'billingDocumentItem')
                ident=f"{r.get(parent,'')}:{item_id(r.get(item))}"
            if ident: g.add_node(f'{typ}:{ident}',entity_type=typ,identifier=str(ident),source_table=table,**{'data':r})
    descriptions=collections.defaultdict(list)
    for row in records('product_descriptions'):
        descriptions[str(row.get('product'))].append(row)
    for product,items in descriptions.items():
        node=f'product:{product}'
        if node in g: g.nodes[node]['data']['descriptions']=items
    for r in records('sales_order_schedule_lines'):
        ident=f"{r.get('salesOrder')}:{item_id(r.get('salesOrderItem'))}:{r.get('scheduleLine')}"
        g.add_node(f'schedule_line:{ident}',entity_type='schedule_line',identifier=ident,source_table='sales_order_schedule_lines',data=r)
    for r in records('business_partner_addresses'):
        ident=f"{r.get('businessPartner')}:{r.get('addressId')}"
        g.add_node(f'partner_address:{ident}',entity_type='partner_address',identifier=ident,source_table='business_partner_addresses',data=r)
    for r in records('customer_company_assignments'):
        code=str(r.get('companyCode') or '')
        if code and f'company_code:{code}' not in g: g.add_node(f'company_code:{code}',entity_type='company_code',identifier=code,source_table='customer_company_assignments',data={'companyCode':code})
    for r in records('customer_sales_area_assignments'):
        ident=':'.join(str(r.get(k) or '') for k in ('salesOrganization','distributionChannel','organizationDivision'))
        if ident and f'sales_area:{ident}' not in g: g.add_node(f'sales_area:{ident}',entity_type='sales_area',identifier=ident,source_table='customer_sales_area_assignments',data={k:r.get(k) for k in ('salesOrganization','distributionChannel','organizationDivision')})
    journal_rows=records('journal_entry_items_accounts_receivable')
    journal_documents=set()
    for r in journal_rows:
        doc=f"journal_entry:{r.get('companyCode')}:{r.get('fiscalYear')}:{r.get('accountingDocument')}"
        ji=f"journal_entry_item:{r.get('companyCode')}:{r.get('fiscalYear')}:{r.get('accountingDocument')}:{item_id(r.get('accountingDocumentItem'))}"
        if doc not in g: g.add_node(doc,entity_type='journal_entry',identifier=str(r.get('accountingDocument')),source_table='journal_entry_items_accounts_receivable',data=r)
        g.add_node(ji,entity_type='journal_entry_item',identifier=ji.split(':',1)[1],source_table='journal_entry_items_accounts_receivable',data=r)
        g.add_edge(doc,ji,key='CONTAINS_ITEM',relationship='CONTAINS_ITEM',source_table='journal_entry_items_accounts_receivable',validation='verified')
        journal_documents.add((r.get('companyCode'),r.get('fiscalYear'),r.get('accountingDocument')))
    def link(a,b,rel,source,metadata=None,key=None):
        if a in g and b in g: g.add_edge(a,b,key=key or rel,relationship=rel,source_table=source,source_fields=(metadata or {}).get('source_fields',[]),reference_values=(metadata or {}).get('reference_values',{}),assignment=(metadata or {}).get('assignment'),validation='verified')
    for r in rows['sales_order_items']:
        so=f"sales_order:{r.get('salesOrder')}"; it=f"sales_order_item:{r.get('salesOrder')}:{item_id(r.get('salesOrderItem'))}"
        link(so,it,'CONTAINS_ITEM','sales_order_items',{'source_fields':['salesOrder'],'reference_values':{'salesOrder':r.get('salesOrder')}})
        link(it,f"product:{r.get('material')}",'HAS_PRODUCT','sales_order_items',{'source_fields':['material'],'reference_values':{'material':r.get('material')}})
    for r in records('sales_order_schedule_lines'):
        order_item=f"sales_order_item:{r.get('salesOrder')}:{item_id(r.get('salesOrderItem'))}"
        schedule=f"schedule_line:{r.get('salesOrder')}:{item_id(r.get('salesOrderItem'))}:{r.get('scheduleLine')}"
        link(order_item,schedule,'HAS_SCHEDULE_LINE','sales_order_schedule_lines',{'source_fields':['salesOrder','salesOrderItem'],'reference_values':{'salesOrder':r.get('salesOrder'),'salesOrderItem':r.get('salesOrderItem')}})
    for r in rows['outbound_delivery_items']:
        d=f"delivery:{r.get('deliveryDocument')}"; it=f"delivery_item:{r.get('deliveryDocument')}:{item_id(r.get('deliveryDocumentItem'))}"
        soitem=f"sales_order_item:{r.get('referenceSdDocument')}:{item_id(r.get('referenceSdDocumentItem'))}"
        link(d,it,'CONTAINS_ITEM','outbound_delivery_items',{'source_fields':['deliveryDocument'],'reference_values':{'deliveryDocument':r.get('deliveryDocument')}})
        link(it,soitem,'REFERENCES_ORDER_ITEM','outbound_delivery_items',{'source_fields':['referenceSdDocument','referenceSdDocumentItem'],'reference_values':{'referenceSdDocument':r.get('referenceSdDocument'),'referenceSdDocumentItem':r.get('referenceSdDocumentItem')}})
        link(it,f"plant:{r.get('plant')}",'LOCATED_AT_PLANT','outbound_delivery_items',{'source_fields':['plant'],'reference_values':{'plant':r.get('plant')}})
    for r in rows['billing_document_items']:
        b=f"billing_document:{r.get('billingDocument')}"; it=f"billing_item:{r.get('billingDocument')}:{item_id(r.get('billingDocumentItem'))}"
        ref=str(r.get('referenceSdDocument','')); item=str(r.get('referenceSdDocumentItem',''))
        link(b,it,'CONTAINS_ITEM','billing_document_items',{'source_fields':['billingDocument'],'reference_values':{'billingDocument':r.get('billingDocument')}})
        # Resolve the preceding document by existing verified item key.
        candidates=[f'{prefix}:{ref}:{item_id(item)}' for prefix in ('delivery_item','sales_order_item') if f'{prefix}:{ref}:{item_id(item)}' in g]
        if len(candidates)==1: link(it,candidates[0],'REFERENCES_PRECEDING_ITEM','billing_document_items',{'source_fields':['referenceSdDocument','referenceSdDocumentItem'],'reference_values':{'referenceSdDocument':ref,'referenceSdDocumentItem':item}})
    for r in rows['billing_document_headers']:
        link(f"billing_document:{r.get('billingDocument')}",f"journal_entry:{r.get('companyCode')}:{r.get('fiscalYear')}:{r.get('accountingDocument')}",'ACCOUNTED_BY','billing_document_headers',{'source_fields':['companyCode','fiscalYear','accountingDocument'],'reference_values':{k:r.get(k) for k in ('companyCode','fiscalYear','accountingDocument')}})
        if r.get('soldToParty'): link(f"billing_document:{r.get('billingDocument')}",f"customer:{r.get('soldToParty')}",'BELONGS_TO_CUSTOMER','billing_document_headers',{'source_fields':['soldToParty'],'reference_values':{'soldToParty':r.get('soldToParty')}})
    for r in journal_rows:
        source=f"journal_entry:{r.get('companyCode')}:{r.get('fiscalYear')}:{r.get('accountingDocument')}"
        target=f"billing_document:{r.get('referenceDocument')}"
        if r.get('referenceDocument'): link(source,target,'REFERENCES_BILLING','journal_entry_items_accounts_receivable',{'source_fields':['referenceDocument'],'reference_values':{'referenceDocument':r.get('referenceDocument')}})
        clearing=(r.get('companyCode'),r.get('clearingDocFiscalYear') or r.get('fiscalYear'),r.get('clearingAccountingDocument'))
        if r.get('clearingAccountingDocument') and clearing in journal_documents:
            dest=f"journal_entry:{clearing[0]}:{clearing[1]}:{clearing[2]}"
            link(source,dest,'CLEARED_BY','journal_entry_items_accounts_receivable',{'source_fields':['companyCode','clearingDocFiscalYear','clearingAccountingDocument'],'reference_values':{'companyCode':clearing[0],'fiscalYear':clearing[1],'accountingDocument':clearing[2]}})
    for r in records('payments_accounts_receivable'):
        item=f"journal_entry_item:{r.get('companyCode')}:{r.get('fiscalYear')}:{r.get('accountingDocument')}:{item_id(r.get('accountingDocumentItem'))}"
        if item in g:
            g.nodes[item]['data']['payments_accounts_receivable_source']=r
    for r in rows['sales_order_headers']:
        link(f"sales_order:{r.get('salesOrder')}",f"customer:{r.get('soldToParty')}",'BELONGS_TO_CUSTOMER','sales_order_headers',{'source_fields':['soldToParty'],'reference_values':{'soldToParty':r.get('soldToParty')}})
    for r in records('business_partner_addresses'):
        link(f"customer:{r.get('businessPartner')}",f"partner_address:{r.get('businessPartner')}:{r.get('addressId')}",'HAS_ADDRESS','business_partner_addresses',{'source_fields':['businessPartner','addressId'],'reference_values':{'businessPartner':r.get('businessPartner'),'addressId':r.get('addressId')}})
    for r in records('product_plants'):
        link(f"product:{r.get('product')}",f"plant:{r.get('plant')}",'LOCATED_AT_PLANT','product_plants',{'source_fields':['product','plant'],'reference_values':{'product':r.get('product'),'plant':r.get('plant')}})
    for r in records('product_storage_locations'):
        location=str(r.get('storageLocation') or '')
        link(f"product:{r.get('product')}",f"plant:{r.get('plant')}",'HAS_STORAGE_ASSIGNMENT','product_storage_locations',{'source_fields':['product','plant','storageLocation'],'reference_values':{'product':r.get('product'),'plant':r.get('plant'),'storageLocation':location},'assignment':r},f'HAS_STORAGE_ASSIGNMENT:{location}')
    for r in records('customer_company_assignments'):
        link(f"customer:{r.get('customer')}",f"company_code:{r.get('companyCode')}",'ASSIGNED_TO_COMPANY','customer_company_assignments',{'assignment':r})
    for r in records('customer_sales_area_assignments'):
        area=':'.join(str(r.get(k) or '') for k in ('salesOrganization','distributionChannel','organizationDivision'))
        link(f"customer:{r.get('customer')}",f"sales_area:{area}",'ASSIGNED_TO_SALES_AREA','customer_sales_area_assignments',{'assignment':r})
    _GRAPH_CACHE=g
    return g

def graph_evidence(node_ids,neighbor_cap=150,edge_cap=2000):
    g=graph(); selected={n for n in node_ids if n in g}; ids=set(selected)
    for node in list(selected):
        for neighbor in list(g.successors(node))+list(g.predecessors(node)):
            if len(ids)>=len(selected)+neighbor_cap: break
            ids.add(neighbor)
    nodes=[{'id':n,'type':g.nodes[n]['entity_type'],'identifier':g.nodes[n]['identifier'],'data':g.nodes[n]['data']} for n in ids]
    edges=[]
    for a,b,k,d in g.edges(keys=True,data=True):
        if (a in selected or b in selected) and a in ids and b in ids:
            edges.append({'source':a,'target':b,'type':d['relationship'],'id':f'{a}|{d["relationship"]}|{b}|{k}'})
            if len(edges)>=edge_cap: break
    return nodes,edges

@app.get('/api/health',response_model=HealthResponse)
def health(): return {'status':'ok','database_ready':DB_PATH.exists()}

@app.get('/api/dataset/stats',response_model=DatasetStatsResponse)
def stats():
    ensure_db()
    with connect() as c:
        return {'tables':[dict(r) for r in c.execute('SELECT table_name,COUNT(*) records FROM source_records GROUP BY table_name ORDER BY table_name')], 'ingestion_errors':c.execute('SELECT COUNT(*) FROM ingestion_errors').fetchone()[0]}

@app.get('/api/data-quality/report',response_model=QualityReportResponse)
def quality():
    ensure_db()
    with connect() as c:
        errors=[dict(r) for r in c.execute('SELECT table_name,source_file,source_line,error FROM ingestion_errors LIMIT 200')]
    profile=json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else None
    return {'ingestion_errors':errors,'profile':profile,**unresolved_billing_refs()}

def unresolved_billing_refs():
    so={(r.get('salesOrder'),item_id(r.get('salesOrderItem'))) for r in records('sales_order_items')}
    de={(r.get('deliveryDocument'),item_id(r.get('deliveryDocumentItem'))) for r in records('outbound_delivery_items')}
    result=[]; ambiguous=[]
    for r in records('billing_document_items'):
        ref=(r.get('referenceSdDocument'),item_id(r.get('referenceSdDocumentItem')))
        matches=int(ref in so)+int(ref in de)
        if matches==0: result.append({'billingDocument':r.get('billingDocument'),'reference':ref})
        if matches>1: ambiguous.append({'billingDocument':r.get('billingDocument'),'reference':ref,'candidate_types':['sales_order_item','delivery_item']})
    return {'unresolved_billing_references':result[:500],'ambiguous_billing_references':ambiguous[:500]}

@app.get('/api/graph',response_model=GraphResponse)
def get_graph(limit:int=Query(150,ge=1,le=1000),relationship_type:str|None=None):
    g=graph(); nodes=list(g.nodes(data=True))[:limit]; keep={n for n,_ in nodes}
    edges=[{'source':a,'target':b,'type':d['relationship'],'id':f'{a}|{d["relationship"]}|{b}|{k}'} for a,b,k,d in g.edges(keys=True,data=True) if a in keep and b in keep and (not relationship_type or d['relationship']==relationship_type)]
    return {'nodes':[{'id':n,'type':d['entity_type'],'identifier':d['identifier'],'data':d['data']} for n,d in nodes], 'edges':edges[:5000], 'total_nodes':g.number_of_nodes(),'total_edges':g.number_of_edges()}

@app.get('/api/graph/stats')
def graph_stats():
    g=graph(); undirected=g.to_undirected(); sizes=sorted((len(c) for c in nx.connected_components(undirected)),reverse=True)
    return {'nodes':g.number_of_nodes(),'edges':g.number_of_edges(),'isolated_nodes':sum(g.degree(n)==0 for n in g),'connected_components':len(sizes),'largest_component':sizes[0] if sizes else 0,'entity_types':dict(collections.Counter(d['entity_type'] for _,d in g.nodes(data=True))),'relationship_types':dict(collections.Counter(d['relationship'] for *_,d in g.edges(data=True)))}

@app.get('/api/graph/path')
def graph_path(source_node_id:str,target_node_id:str):
    g=graph()
    if source_node_id not in g or target_node_id not in g: raise HTTPException(404,'One or both entities were not found.')
    try: path=nx.shortest_path(g.to_undirected(),source_node_id,target_node_id)
    except nx.NetworkXNoPath: raise HTTPException(404,'No verified relationship path exists between these entities.') from None
    return {'nodes':[{'id':n,'type':g.nodes[n]['entity_type'],'identifier':g.nodes[n]['identifier'],'data':g.nodes[n]['data']} for n in path],'edges':[{'source':a,'target':b,'type':next(iter(g.get_edge_data(a,b).values()))['relationship']} for a,b in zip(path,path[1:])]}

@app.get('/api/graph/expand/{node_id:path}')
def expand(node_id:str,limit:int=Query(100,ge=1,le=500)):
    g=graph()
    if node_id not in g: raise HTTPException(404,'Entity not found')
    nbr=list(g.successors(node_id))+list(g.predecessors(node_id)); nbr=list(dict.fromkeys(nbr))[:limit]
    ids={node_id,*nbr}
    edges=[{'source':a,'target':b,'type':d['relationship'],'assignment':d.get('assignment')} for a,b,_,d in g.edges(keys=True,data=True) if a in ids and b in ids]
    return {'nodes':[{'id':n,'type':g.nodes[n]['entity_type'],'identifier':g.nodes[n]['identifier'],'data':g.nodes[n]['data']} for n in ids], 'edges':edges[:limit*10]}

@app.get('/api/entities/search',response_model=EntitySearchResponse)
def search(q:str=Query(...,min_length=1),limit:int=Query(20,ge=1,le=100)):
    q=q.casefold(); g=graph(); out=[]
    for n,d in g.nodes(data=True):
        if q in str(d['identifier']).casefold() or q in json.dumps(d['data'],ensure_ascii=False).casefold():
            out.append({'id':n,'type':d['entity_type'],'identifier':d['identifier'],'data':d['data']})
            if len(out)>=limit: break
    return {'results':out,'count':len(out)}

@app.get('/api/entities/{entity_type}/{entity_id}')
def entity(entity_type:str,entity_id:str):
    node_id=f'{entity_type}:{entity_id}'; g=graph()
    if node_id not in g: raise HTTPException(404,'Entity not found')
    return {'id':node_id,'type':entity_type,'identifier':entity_id,'data':g.nodes[node_id]['data'],'neighbors':[{'id':n,'relationship':d.get('relationship')} for _,n,d in g.out_edges(node_id,data=True)]}

@app.get('/api/graph/node/{node_id:path}')
def graph_node(node_id:str):
    g=graph()
    if node_id not in g: raise HTTPException(404,'Entity not found')
    return {'id':node_id,'metadata':g.nodes[node_id],'degree':g.degree(node_id),'relationships':[{'source':a,'target':b,**d} for a,b,d in g.edges(node_id,data=True)]}

@app.get('/api/analytics/top-products',response_model=TopProductsResponse)
def top_products(limit:int=Query(20,ge=1,le=100),rank_by:str=Query('billing_document_count',pattern='^(billing_document_count|net_amount|quantity)$'),date_from:str|None=None,date_to:str|None=None,customer_id:str|None=None,product_id:str|None=None):
    validate_date_filters(date_from,date_to)
    headers={r.get('billingDocument'):r for r in records('billing_document_headers')}
    products={r.get('product'):r for r in records('products')}; desc={r.get('product'):r for r in records('product_descriptions')}
    agg=collections.defaultdict(lambda:{'billing_documents':set(),'quantity':Decimal('0'),'net_amount':Decimal('0'),'currency':None,'invalid_amounts':0,'invalid_quantity_rows':0})
    for r in records('billing_document_items'):
        h=headers.get(r.get('billingDocument'),{})
        if h.get('billingDocumentIsCancelled') is True: continue
        if not in_date_range(h.get('billingDocumentDate'),date_from,date_to): continue
        if customer_id and h.get('soldToParty')!=customer_id: continue
        p=r.get('material')
        if product_id and p!=product_id: continue
        currency=r.get('transactionCurrency') or h.get('transactionCurrency') or 'UNKNOWN'
        a=agg[(p,currency)]; a['billing_documents'].add(r.get('billingDocument')); quantity=decimal_number(r.get('billingQuantity'))
        if quantity is None: a['invalid_quantity_rows']+=1
        else: a['quantity']+=quantity
        amount=decimal_number(r.get('netAmount'))
        if amount is None: a['invalid_amounts']+=1
        else: a['net_amount']+=amount
        a['currency']=currency
    result=[]
    metric=lambda value: len(value[1]['billing_documents']) if rank_by=='billing_document_count' else value[1][rank_by]
    for (p,currency),a in sorted(agg.items(),key=lambda x:(str(x[0][1]),-metric(x),str(x[0][0]))):
        result.append({'product':p,'description':desc.get(p,{}),'billing_documents':len(a['billing_documents']),'quantity':format(a['quantity'].normalize(),'f'),'net_amount':format(a['net_amount'].quantize(Decimal('0.01')),'f'),'invalid_amount_rows':a['invalid_amounts'],'invalid_quantity_rows':a['invalid_quantity_rows'],'currency':currency})
    definitions={'billing_document_count':'Distinct non-cancelled billing documents per product and currency.','quantity':'Billed quantity by product and currency; units may differ by product.','net_amount':'Billing item net amount ranked within currency; currencies are not converted.'}
    return {'results':result[:limit],'rank_by':rank_by,'definition':definitions[rank_by]}

def in_date_range(value,date_from=None,date_to=None):
    day=str(value or '')[:10]
    return bool(day) and (not date_from or day>=date_from) and (not date_to or day<=date_to)

def validate_date_filters(date_from,date_to):
    try:
        if date_from: date.fromisoformat(date_from)
        if date_to: date.fromisoformat(date_to)
    except ValueError: raise HTTPException(422,'Date filters must use YYYY-MM-DD.') from None
    if date_from and date_to and date_from>date_to: raise HTTPException(422,'date_from must not be after date_to.')

def billing_summary(group_by:str,metric:str,limit:int=20,date_from:str|None=None,date_to:str|None=None,customer_id:str|None=None,product_id:str|None=None):
    headers={r.get('billingDocument'):r for r in records('billing_document_headers')}
    grouped=collections.defaultdict(lambda:{'documents':set(),'quantity':Decimal('0'),'net_amount':Decimal('0'),'invalid_amounts':0,'invalid_quantity_rows':0})
    for item in records('billing_document_items'):
        h=headers.get(item.get('billingDocument'),{})
        if h.get('billingDocumentIsCancelled') is True or not in_date_range(h.get('billingDocumentDate'),date_from,date_to): continue
        if customer_id and h.get('soldToParty')!=customer_id: continue
        if product_id and item.get('material')!=product_id: continue
        dimension={'customer':h.get('soldToParty'),'product':item.get('material'),'month':str(h.get('billingDocumentDate') or '')[:7]}[group_by]
        currency=item.get('transactionCurrency') or h.get('transactionCurrency') or 'UNKNOWN'
        key=(dimension,currency); agg=grouped[key]; agg['documents'].add(item.get('billingDocument'))
        quantity=decimal_number(item.get('billingQuantity'))
        if quantity is None: agg['invalid_quantity_rows']+=1
        else: agg['quantity']+=quantity
        amount=decimal_number(item.get('netAmount'))
        if amount is None: agg['invalid_amounts']+=1
        else: agg['net_amount']+=amount
    rows=[]
    for (dimension,currency),agg in grouped.items():
        rows.append({'group':dimension,'currency':currency,'billing_documents':len(agg['documents']),'quantity':format(agg['quantity'].normalize(),'f'),'net_amount':format(agg['net_amount'].quantize(Decimal('0.01')),'f'),'invalid_amount_rows':agg['invalid_amounts'],'invalid_quantity_rows':agg['invalid_quantity_rows']})
    sort_field='billing_documents' if metric=='document_count' else metric
    def sort_metric(row):
        value=Decimal(str(row[sort_field])) if sort_field in ('net_amount','quantity') else Decimal(row[sort_field])
        return -value
    return {'results':sorted(rows,key=lambda r:(sort_metric(r),str(r['group']),r['currency']))[:limit],
            'group_by':group_by,'metric':metric,'definition':{'document_count':'Distinct non-cancelled billing document count.','quantity':'Billed quantity; units may differ by product.','net_amount':'Billing item net amount, separated by currency with no conversion.'}[metric]}

def customer_orders(customer_id:str,date_from=None,date_to=None,limit=20,delivery_status=None):
    rows=[]
    for r in records('sales_order_headers'):
        if r.get('soldToParty')!=customer_id or not in_date_range(r.get('creationDate'),date_from,date_to): continue
        if delivery_status and r.get('overallDeliveryStatus')!=delivery_status: continue
        rows.append({'salesOrder':r.get('salesOrder'),'soldToParty':r.get('soldToParty'),'creationDate':r.get('creationDate'),'totalNetAmount':r.get('totalNetAmount'),'currency':r.get('transactionCurrency'),'deliveryStatus':r.get('overallDeliveryStatus'),'node_id':f"sales_order:{r.get('salesOrder')}"})
    return {'customer_id':customer_id,'results':sorted(rows,key=lambda r:str(r['creationDate']),reverse=True)[:limit],'count':len(rows),'source_table':'sales_order_headers'}

def resolve_customer(plan):
    known={str(r.get('soldToParty')) for r in records('sales_order_headers')}|{str(r.get('customer') or r.get('businessPartner')) for r in records('business_partners')}
    if plan.customer_id: return (plan.customer_id,[]) if plan.customer_id in known else (None,[])
    if not plan.customer_query: return None,[]
    query=plan.customer_query.casefold(); ids=set()
    for r in records('business_partners'):
        name=' '.join(str(r.get(k) or '') for k in ('businessPartnerFullName','businessPartnerName','organizationBpName1')).casefold()
        if query in name: ids.add(str(r.get('customer') or r.get('businessPartner')))
    return (next(iter(ids)),[]) if len(ids)==1 else (None,sorted(ids))

def resolve_product(plan):
    known={str(r.get('product')) for r in records('products')}
    if plan.product_id: return (plan.product_id,[]) if plan.product_id in known else (None,[])
    if not plan.product_query: return None,[]
    query=plan.product_query.casefold(); ids=set()
    for r in records('product_descriptions'):
        if query in json.dumps(r,ensure_ascii=False).casefold() and r.get('product'): ids.add(str(r['product']))
    for r in records('products'):
        if query in str(r.get('productOldId') or '').casefold() and r.get('product'): ids.add(str(r['product']))
    return (next(iter(ids)),[]) if len(ids)==1 else (None,sorted(ids))

@app.get('/api/analytics/incomplete-flows')
def incomplete_flows(limit:int=Query(100,ge=1,le=500),date_from:str|None=None,date_to:str|None=None):
    validate_date_filters(date_from,date_to)
    orders=records('sales_order_headers'); delivery_items=records('outbound_delivery_items')
    delivery_dates={r.get('deliveryDocument'):r.get('creationDate') for r in records('outbound_delivery_headers')}
    headers={r.get('billingDocument'):r for r in records('billing_document_headers')}
    active_bill_refs={(r.get('referenceSdDocument'),item_id(r.get('referenceSdDocumentItem'))) for r in records('billing_document_items') if headers.get(r.get('billingDocument'),{}).get('billingDocumentIsCancelled') is not True}
    order_refs={r.get('referenceSdDocument') for r in delivery_items}
    no_delivery=[{'salesOrder':r.get('salesOrder'),'soldToParty':r.get('soldToParty'),'status':r.get('overallDeliveryStatus')} for r in orders if r.get('salesOrder') not in order_refs and in_date_range(r.get('creationDate'),date_from,date_to)]
    no_billing_items=[r for r in delivery_items if (r.get('deliveryDocument'),item_id(r.get('deliveryDocumentItem'))) not in active_bill_refs and in_date_range(delivery_dates.get(r.get('deliveryDocument')),date_from,date_to)]
    no_billing=sorted({r.get('deliveryDocument') for r in no_billing_items})
    return {'rules':['Sales order header has no referenced delivery item','Delivery item has no matching non-cancelled billing preceding-document item'],'orders_without_delivery':no_delivery[:limit],'deliveries_without_billing':[{'deliveryDocument':d} for d in no_billing[:limit]],'counts':{'orders_without_delivery':len(no_delivery),'deliveries_without_billing':len(no_billing)}}

def decimal_number(value):
    try:
        amount=Decimal(str(value or '0'))
        return amount if amount.is_finite() else None
    except (InvalidOperation,ValueError,TypeError): return None

@app.get('/api/documents/{document_type}/{document_id}/trace')
def trace(document_type:str,document_id:str):
    allowed={'sales_order':'sales_order','delivery':'delivery','billing_document':'billing_document'}
    if document_type not in allowed: raise HTTPException(400,'Unsupported document type')
    g=graph(); start=f'{allowed[document_type]}:{document_id}'
    if start not in g: raise HTTPException(404,'Document not found')
    # Stop at master-data hubs so a customer or popular product does not pull
    # unrelated orders into a document investigation.
    adj=g.to_undirected(); depths={start:0}; queue=[start]
    while queue:
        current=queue.pop(0)
        if depths[current]>=4 or g.nodes[current]['entity_type'] in {'customer','product','plant'}: continue
        for neighbor in adj.neighbors(current):
            if neighbor not in depths: depths[neighbor]=depths[current]+1; queue.append(neighbor)
    ids=set(depths)
    return {'root':start,'nodes':[{'id':n,'type':g.nodes[n]['entity_type'],'identifier':g.nodes[n]['identifier'],'data':g.nodes[n]['data']} for n in ids], 'edges':[{'source':a,'target':b,'type':d['relationship'],'id':f'{a}|{d["relationship"]}|{b}'} for a,b,d in g.edges(data=True) if a in ids and b in ids]}

class QueryRequest(BaseModel):
    question:str=Field(min_length=2,max_length=1000)

@app.post('/api/query/validate',response_model=QueryValidationResponse)
def validate_query(req:QueryRequest):
    supported=bool(DOMAIN_RE.search(req.question))
    return {'supported_domain':supported,'safe_intents':['document_trace','top_products','customer_orders','billing_summary','incomplete_flows'],'requires_llm':False,'message':None if supported else 'Outside order-to-cash scope'}

def query_response(answer,plan,evidence=None,warning=None,clarification=False,options=None):
    intent=plan['intent']; source_tables={
      'document_trace':{'sales_order':['sales_order_headers','sales_order_items','outbound_delivery_items','billing_document_headers','billing_document_items','journal_entry_items_accounts_receivable'],'delivery':['outbound_delivery_headers','outbound_delivery_items','billing_document_items'],'billing_document':['billing_document_headers','billing_document_items','journal_entry_items_accounts_receivable']}.get(plan.get('document_type'),[]),
      'top_products':['billing_document_headers','billing_document_items','product_descriptions'],
      'customer_orders':['sales_order_headers'],
      'billing_summary':['billing_document_headers','billing_document_items'],
      'incomplete_flows':['sales_order_headers','outbound_delivery_items','billing_document_headers','billing_document_items']}[intent]
    ev=evidence or {}; results=ev.get('results')
    answer=narrate_answer(answer,plan,ev)
    if evidence and os.environ.get('LLM_PROVIDER') and os.environ.get('LLM_API_KEY') and os.environ.get('LLM_MODEL'):
        try:
            answer=generate_answer(plan,ev,answer)
        except Exception:
            warning=warning or 'LLM answer generation failed; showing a grounded summary instead.'
    count=ev.get('count',len(results) if isinstance(results,list) else len(ev.get('nodes',[])))
    if intent=='incomplete_flows': count=sum(ev.get('counts',{}).values())
    refs=ev.get('referenced_node_ids') or [n.get('id') for n in ev.get('nodes',[]) if n.get('id')]
    definitions=[ev['definition']] if isinstance(ev.get('definition'),str) else []
    warnings=[warning] if warning else []
    return {'answer':answer,'plan':plan,'evidence':evidence,'warnings':warnings,'clarification_needed':clarification,'options':options,
            'execution_status':'clarification_needed' if clarification else 'completed','result_count':count,
            'source_tables':source_tables,'referenced_node_ids':refs,'metric_definitions':definitions}

@app.post('/api/query',response_model=QueryResponse)
def query(req:QueryRequest):
    q=req.question.strip()
    if not DOMAIN_RE.search(q):
        raise HTTPException(422,'Question is outside the supported order-to-cash data domain.')
    try: plan,warning=plan_question(q)
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    plan_data=plan.model_dump()
    if plan.intent=='document_trace':
        if not plan.document_type or not plan.document_id: raise HTTPException(422,'The query plan needs an unambiguous document type and identifier.')
        result=trace(plan.document_type,plan.document_id)
        return query_response(f"Found {len(result['nodes'])} connected records for {plan.document_type} {plan.document_id}.",plan_data,result,warning)
    if plan.intent=='top_products':
        customer_id,customer_matches=resolve_customer(plan)
        if (plan.customer_query or plan.customer_id) and not customer_id:
            return query_response('Please clarify which customer you mean.',plan_data,warning=warning,clarification=True,options=customer_matches)
        if customer_id: plan_data.update(customer_id=customer_id,customer_query=None)
        product_id,product_matches=resolve_product(plan)
        if plan.product_query and not product_id:
            return query_response('Please clarify which product you mean.',plan_data,warning=warning,clarification=True,options=product_matches)
        if plan.product_id and not product_id: return query_response(f"Product {plan.product_id} was not found.",plan_data,warning=warning,clarification=True,options=[])
        if product_id: plan_data.update(product_id=product_id,product_query=None)
        result=top_products(limit=plan.limit,rank_by=plan.rank_by,date_from=plan.date_from,date_to=plan.date_to,customer_id=customer_id,product_id=product_id)
        result['referenced_node_ids']=[f"product:{r['product']}" for r in result['results']]
        result['nodes'],result['edges']=graph_evidence(result['referenced_node_ids'][:50])
        return query_response(f"Ranked products by {result['definition']}",plan_data,result,warning)
    if plan.intent=='customer_orders':
        customer_id,matches=resolve_customer(plan)
        if not customer_id:
            return query_response('Please provide a customer ID or a unique customer name.',plan_data,warning=warning,clarification=True,options=matches)
        plan_data.update(customer_id=customer_id,customer_query=None)
        result=customer_orders(customer_id,plan.date_from,plan.date_to,plan.limit,plan.delivery_status)
        result['referenced_node_ids']=[r['node_id'] for r in result['results']]
        result['nodes'],result['edges']=graph_evidence(result['referenced_node_ids'][:50])
        return query_response(f"Found {result['count']} sales orders for customer {customer_id}.",plan_data,result,warning)
    if plan.intent=='billing_summary':
        if not plan.group_by: return query_response('How should I group the billing summary: customer, product, or month?',plan_data,warning=warning,clarification=True,options=['customer','product','month'])
        customer_id,matches=resolve_customer(plan)
        if (plan.customer_query or plan.customer_id) and not customer_id: return query_response('Please clarify which customer you mean.',plan_data,warning=warning,clarification=True,options=matches)
        if customer_id: plan_data.update(customer_id=customer_id,customer_query=None)
        product_id,product_matches=resolve_product(plan)
        if plan.product_query and not product_id: return query_response('Please clarify which product you mean.',plan_data,warning=warning,clarification=True,options=product_matches)
        if plan.product_id and not product_id: return query_response(f"Product {plan.product_id} was not found.",plan_data,warning=warning,clarification=True,options=[])
        if product_id: plan_data.update(product_id=product_id,product_query=None)
        result=billing_summary(plan.group_by,plan.metric or 'net_amount',plan.limit,plan.date_from,plan.date_to,customer_id,product_id)
        if plan.group_by in ('customer','product'):
            node_type='customer' if plan.group_by=='customer' else 'product'
            result['referenced_node_ids']=[f"{node_type}:{r['group']}" for r in result['results'] if r.get('group')]
            result['nodes'],result['edges']=graph_evidence(result['referenced_node_ids'][:50])
        return query_response(f"Billing summary grouped by {plan.group_by}, using {result['definition']}",plan_data,result,warning)
    result=incomplete_flows(limit=plan.limit,date_from=plan.date_from,date_to=plan.date_to)
    node_ids=[f"sales_order:{r['salesOrder']}" for r in result['orders_without_delivery'] if r.get('salesOrder')]
    node_ids += [f"delivery:{r['deliveryDocument']}" for r in result['deliveries_without_billing'] if r.get('deliveryDocument')]
    result['referenced_node_ids']=node_ids
    result['nodes'],result['edges']=graph_evidence(node_ids[:50])
    return query_response('Computed incomplete flows using explicit document-reference rules.',plan_data,result,warning)
