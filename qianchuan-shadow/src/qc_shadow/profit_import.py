"""Read-only XLSX adapter. Whitelist fields; no recipient/address data persisted."""
import csv,hashlib,json,posixpath,zipfile
import xml.etree.ElementTree as ET
from datetime import datetime,timedelta
from pathlib import Path
from .profit import ProfitStore,calculate,canonical,number,cash,validate_profile

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
def read_xlsx(path):
    with zipfile.ZipFile(path) as z:
        shared=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            shared=[''.join(el.itertext()) for el in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si',NS)]
        rel={e.get('Id'):e.get('Target') for e in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        wb=ET.fromstring(z.read('xl/workbook.xml'));props=wb.find('s:workbookPr',NS)
        if props is not None and props.get('date1904') in ('1','true'):raise ValueError('date1904_not_supported')
        sheets={}
        for sheet in wb.find('s:sheets',NS):
            target=rel[sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')]
            target=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
            rows=[]
            for row in ET.fromstring(z.read(target)).findall('.//s:sheetData/s:row',NS):
                cells={}
                for c in row:
                    col=''.join(x for x in c.get('r','') if x.isalpha());v=c.find('s:v',NS);value=v.text if v is not None else None
                    if c.get('t')=='s' and value is not None:value=shared[int(value)]
                    if c.get('t')=='inlineStr':value=''.join(c.find('s:is',NS).itertext())
                    if value is not None:cells[col]=value
                if cells:rows.append((int(row.get('r')),cells))
            if not rows:continue
            header=rows[0][1]
            if len(set(header.values()))!=len(header):raise ValueError('duplicate_headers')
            sheets[sheet.get('name')]=[(n,{name:cells.get(col) for col,name in header.items()}) for n,cells in rows[1:]]
        return sheets

def timestamp(v):
    if not v or v=='-':return None
    try:return (datetime(1899,12,30)+timedelta(days=float(v))).isoformat(timespec='seconds')
    except ValueError:return datetime.fromisoformat(v).isoformat(timespec='seconds')

def normalize(path,p):
    validate_profile(p);sheets=read_xlsx(path);records=[]
    costrows=sheets.get('成本汇总',[]);source_costs={r['商品ID']:r for _,r in costrows}
    for pid,product in p['products'].items():
        style=product['style'];cost=source_costs.get(pid)
        if cost is None or number(cost['实际成本'])!=number(product['cost']):raise ValueError('cost_mapping_mismatch:'+pid)
        records.append(('costs',pid,{'product':pid,'cost':product['cost'],'profile_version':p['version'],'effective_date':None,'tax_basis':'user_confirmed_consistent','source':'成本汇总'}))
        ordername=style+'订单'
        if ordername not in sheets:raise ValueError('missing_order_sheet:'+style)
        for line,r in sheets[ordername]:
            for k in ('主订单编号','子订单编号','商品ID','商品数量','订单应付金额','平台实际承担优惠金额','达人实际承担优惠金额','售后状态'):
                if r.get(k) is None:raise ValueError('missing_field:'+ordername+':'+str(line)+':'+k)
            qty=number(r['商品数量'])
            if qty!=int(qty):raise ValueError('non_integer_quantity')
            oid=r['子订单编号'];origin=f'{ordername}!{line}';status=r['售后状态']
            records.append(('orders',oid,{'id':oid,'parent':r['主订单编号'],'product':r['商品ID'],'sku':(r.get('商家编码') or '').strip(),'quantity':int(qty),
                'paid':timestamp(r.get('支付完成时间')),'shipped':timestamp(r.get('发货时间')) is not None,'shipped_at':timestamp(r.get('发货时间')),
                'income':cash(sum(number(r[k]) for k in ('订单应付金额','平台实际承担优惠金额','达人实际承担优惠金额'))),
                'payable':r['订单应付金额'],'platform_subsidy':r['平台实际承担优惠金额'],'talent_subsidy':r['达人实际承担优惠金额'],'source':origin}))
            records.append(('refunds',oid,{'order':oid,'success':status=='退款成功','status':status,'basis':'order_status_proxy','refund_time':None,'source':origin}))
        names=[n for n in sheets if n.startswith(style) and '计划' in n]
        if len(names)!=1:raise ValueError('ambiguous_ad_sheet')
        for line,r in sheets[names[0]]:
            if r['日期']=='全部':continue
            day=timestamp(r['日期'])[:10];plan=r['ad_id']
            records.append(('ads',f'{pid}/{plan}/{day}',{'product':pid,'plan':plan,'account':p['account_id'],'day':day,'spend':str(number(r['整体消耗'])),'source':f'{names[0]}!{line}','mapping_basis':'user_confirmed_single_product_nonoverlap'}))
    for name,rate in p['fees'].items():records.append(('fees',name,{'rate':rate,'basis':'net_income','quality':'estimate','profile_version':p['version'],'start':p['start'],'end':p['end']}))
    return records

def run_profit(source,profile_path,db_path,out_dir,as_of,returns_path=None):
    p=json.loads(Path(profile_path).read_text());validate_profile(p)
    source=Path(source);rows=normalize(source,p);filehash=hashlib.sha256(source.read_bytes()).hexdigest()
    store=ProfitStore(db_path)
    try:
        bound=store.records('meta').get('account')
        if bound and bound['account_id']!=p['account_id']:raise ValueError('database_account_mismatch')
        rows.extend([('meta','account',{'account_id':p['account_id']}),('meta','profile',p),('meta','source',{'sha256':filehash,'filename':source.name,'snapshot_at':as_of,'data_cutoff':None})])
        receipt=store.ingest(rows,hashlib.sha256((filehash+canonical(p)).encode()).hexdigest(),as_of)
        if returns_path:
            confirmations=json.loads(Path(returns_path).read_text())
            if confirmations['account_id']!=p['account_id']:raise ValueError('returns_account_mismatch')
            current=store.records('orders')
            for r in confirmations['records']:
                order=current.get(r.get('order'))
                if order is None or type(r.get('quantity')) is not int or not 0<=r['quantity']<=order['quantity'] or (r['quantity'] and not order['shipped']):
                    raise ValueError('invalid_return_confirmation')
            store.ingest([('returns',r['order'],r) for r in confirmations['records']],hashlib.sha256(Path(returns_path).read_bytes()).hexdigest(),confirmations['as_of'])
        result=calculate(store,p);result['source_sha256']=filehash;key=store.save_run(result)
        out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
        # Run-addressed outputs prevent one snapshot from overwriting another.
        folder=out/key;folder.mkdir(exist_ok=True)
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        for name,values in [('利润汇总',result['products']),('订单计算',result['order_details']),('异常明细',result['exceptions'])]:
            columns=list(dict.fromkeys(k for r in values for k in r))
            with (folder/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader();writer.writerows(values)
        if p.get('ad_basis')=='gross_gmv_rate':
            from .profit_report import comparison_rows,comparison_report,reminder_draft
            (folder/'群内提醒草稿.md').write_text(reminder_draft(result))
            (folder/'广告与利润对比报告.md').write_text(comparison_report(result))
            comparison=comparison_rows(result)
            with (folder/'广告与利润对比.csv').open('w',encoding='utf-8-sig',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(comparison[0]));writer.writeheader();writer.writerows(comparison)
        for f in folder.iterdir():f.chmod(0o600)
        return {'run_id':key,'output':str(folder),'execution_allowed':False,'import':receipt,'products':result['products'],'changes':store.change_count()}
    finally:store.close()
