"""Reproducible JSONL profiling: python -m backend.profile"""
import collections, json
import datetime, re
from decimal import Decimal, InvalidOperation
from .common import DATA, REPORT, table_files, rows_for, item_id

def profile():
    result={'source':str(DATA),'tables':{},'relationships':[],'notes':['IDs are profiled as source strings; no numeric coercion is applied.','Joins use exact string equality except numeric fields ending in Item, where leading zeroes are stripped for matching only; source values remain unchanged.']}
    data={}
    for table, files in table_files().items():
        count=0; errors=[]; fields=collections.defaultdict(lambda:{'null':0,'types':collections.Counter(),'samples':[]});
        valid=[]
        for path,line,row in rows_for(table):
            count+=1
            if '__parse_error__' in row: errors.append({'file':path.name,'line':line,'error':row['__parse_error__']}); continue
            valid.append(row)
            for k,v in row.items():
                d=fields[k]; d['types'][type(v).__name__]+=1
                if v is None or v=='': d['null']+=1
                if len(d['samples'])<3 and v is not None: d['samples'].append(v)
        quality={}
        for col in fields:
            values=[r.get(col) for r in valid if r.get(col) not in (None,'')]
            low=col.lower()
            if any(w in low for w in ('amount','quantity','weight','price')):
                quality[col]={'kind':'numeric-like','invalid_values':sum(_number(v) is None for v in values),'nonempty_values':len(values)}
            if 'date' in low or low.endswith('datetime'):
                quality[col]={'kind':'date-like','invalid_values':sum(_date(v) is False for v in values),'nonempty_values':len(values)}
            if low.startswith('is') or 'iscancel' in low or 'isblocked' in low or 'isdeleted' in low:
                quality[col]={'kind':'boolean-like','observed_values':dict(collections.Counter(type(v).__name__ for v in values)),'non_boolean_values':sum(not isinstance(v,bool) for v in values)}
            if 'currency' in low:
                quality[col]={'kind':'currency-code-like','distinct_values':sorted({str(v) for v in values})[:100]}
        id_formats={}
        for col in fields:
            if re.search(r'(id|document|customer|product|plant|item|material|order|delivery|billing|partner)$',col,re.I):
                variants=collections.defaultdict(set)
                for r in valid:
                    v=r.get(col)
                    if isinstance(v,str) and v.isdigit(): variants[str(int(v))].add(v)
                groups=[sorted(v) for v in variants.values() if len(v)>1]
                if groups: id_formats[col]={'leading_zero_variant_groups':groups[:50],'group_count':len(groups)}
        result['tables'][table]={'files':[p.name for p in files],'records':count,'malformed':len(errors),'errors':errors[:100],
            'identifier_format_variants':id_formats,'field_quality':quality,
            'columns':{k:{'types':dict(v['types']),'null_percent':round(v['null']*100/max(1,count),2),'samples':v['samples']} for k,v in fields.items()}}
        data[table]=valid
    keys={
      'sales_order_headers':['salesOrder'],'sales_order_items':['salesOrder','salesOrderItem'],
      'sales_order_schedule_lines':['salesOrder','salesOrderItem','scheduleLine'],
      'outbound_delivery_headers':['deliveryDocument'],'outbound_delivery_items':['deliveryDocument','deliveryDocumentItem'],
      'billing_document_headers':['billingDocument'],'billing_document_items':['billingDocument','billingDocumentItem'],
      'billing_document_cancellations':['billingDocument'],
      'journal_entry_items_accounts_receivable':['companyCode','fiscalYear','accountingDocument','accountingDocumentItem'],
      'payments_accounts_receivable':['companyCode','fiscalYear','accountingDocument','accountingDocumentItem'],
      'business_partners':['businessPartner'],'business_partner_addresses':['businessPartner','addressId'],
      'customer_company_assignments':['customer','companyCode'],'customer_sales_area_assignments':['customer','salesOrganization','distributionChannel','organizationDivision'],
      'product_plants':['product','plant'],'product_storage_locations':['product','plant','storageLocation'],
      'product_descriptions':['product','language'],'products':['product'],'plants':['plant']}
    for table,fields_key in keys.items():
        rows=data.get(table,[]); vals=[tuple(str(r.get(k) or '') for k in fields_key) for r in rows]
        result['tables'].setdefault(table,{})['candidate_key']={'fields':fields_key,'duplicate_rows':len(vals)-len(set(vals)),'null_or_empty_rows':sum(any(not x for x in v) for v in vals)}
    for table in result['tables']:
        result['tables'][table].setdefault('candidate_key',{'fields':None,'duplicate_rows':None,'null_or_empty_rows':None,'status':'not assessed; composite key requires source-specific verification'})
    relations=[('sales_order_headers','salesOrder','sales_order_items','salesOrder'),('sales_order_items','salesOrder|salesOrderItem','sales_order_schedule_lines','salesOrder|salesOrderItem'),('sales_order_items','salesOrder|salesOrderItem','outbound_delivery_items','referenceSdDocument|referenceSdDocumentItem'),('outbound_delivery_headers','deliveryDocument','outbound_delivery_items','deliveryDocument'),('outbound_delivery_items','deliveryDocument|deliveryDocumentItem','billing_document_items','referenceSdDocument|referenceSdDocumentItem'),('billing_document_headers','billingDocument','billing_document_items','billingDocument'),('billing_document_headers','companyCode|fiscalYear|accountingDocument','journal_entry_items_accounts_receivable','companyCode|fiscalYear|accountingDocument'),('billing_document_headers','billingDocument','journal_entry_items_accounts_receivable','referenceDocument'),('journal_entry_items_accounts_receivable','companyCode|fiscalYear|accountingDocument|accountingDocumentItem','payments_accounts_receivable','companyCode|fiscalYear|accountingDocument|accountingDocumentItem'),('journal_entry_items_accounts_receivable','companyCode|fiscalYear|accountingDocument','journal_entry_items_accounts_receivable','companyCode|clearingDocFiscalYear|clearingAccountingDocument'),('business_partners','businessPartner','business_partner_addresses','businessPartner'),('business_partners','businessPartner','sales_order_headers','soldToParty'),('products','product','sales_order_items','material'),('products','product','product_plants','product'),('plants','plant','product_plants','plant')]
    for left,lk,right,rk in relations:
        lkf=lk.split('|'); rkf=rk.split('|'); lset={tuple(str(r.get(k) or '') for k in lkf) for r in data.get(left,[])}
        ref=[tuple(str(r.get(k) or '') for k in rkf) for r in data.get(right,[])]; matched=sum(v in lset for v in ref)
        norm=lambda vals, fields: tuple(item_id(v) if f.lower().endswith('item') else v for f,v in zip(fields,vals))
        nlset={norm(v,lkf) for v in lset}; nmatched=sum(norm(v,rkf) in nlset for v in ref)
        result['relationships'].append({'from_table':left,'from_fields':lkf,'to_table':right,'to_fields':rkf,'matched_rows_exact':matched,'matched_rows_item_zero_normalized':nmatched,'reference_rows':len(ref),'unmatched_rows_after_item_normalization':len(ref)-nmatched,'match_percent_after_item_normalization':round(nmatched*100/max(1,len(ref)),2),'status':'candidate; item-number zero stripping applies only to numeric item fields; original identifiers are preserved'})
    cancel=data.get('billing_document_cancellations',[])
    result['cancellations']={'rows':len(cancel),'billing_headers_marked_cancelled':sum(r.get('billingDocumentIsCancelled') is True for r in data.get('billing_document_headers',[])),'headers_with_cancelled_document_reference':sum(bool(r.get('cancelledBillingDocument')) for r in data.get('billing_document_headers',[]))}
    order_items={(r.get('salesOrder'),item_id(r.get('salesOrderItem'))) for r in data.get('sales_order_items',[])}
    delivery_items={(r.get('deliveryDocument'),item_id(r.get('deliveryDocumentItem'))) for r in data.get('outbound_delivery_items',[])}
    unresolved=ambiguous=0
    for row in data.get('billing_document_items',[]):
        ref=(row.get('referenceSdDocument'),item_id(row.get('referenceSdDocumentItem')))
        matches=int(ref in order_items)+int(ref in delivery_items)
        unresolved+=matches==0; ambiguous+=matches>1
    result['billing_preceding_references']={'total':len(data.get('billing_document_items',[])),'unresolved':unresolved,'ambiguous':ambiguous,'resolved':len(data.get('billing_document_items',[]))-unresolved-ambiguous}
    REPORT.parent.mkdir(parents=True,exist_ok=True); REPORT.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    return result

def _number(value):
    try: return 0 if Decimal(str(value)).is_finite() else None
    except (InvalidOperation,TypeError,ValueError): return None

def _date(value):
    if not isinstance(value,str): return False
    try: datetime.datetime.fromisoformat(value.replace('Z','+00:00')); return True
    except ValueError: return False

if __name__=='__main__':
    r=profile(); print(f"Profiled {len(r['tables'])} tables. Report: {REPORT}")
