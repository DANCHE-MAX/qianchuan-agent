"""Local-only send barrier for a frozen daily report. Never sends or repairs data."""
import hashlib,json,zipfile,posixpath
from pathlib import Path
from decimal import Decimal
from datetime import date,timedelta,datetime,timezone
from xml.etree import ElementTree as ET
from . import data_completeness,plan_profit,daily_guide,two_day_daily,product_spend_daily,profit_daily

INPUTS=('result.json','guide/previous.json','guide/guide.json','two_day/collected.json','two_day/reports.json','two_day/stack.json','two_day/pies.json')
class PreflightBlocked(RuntimeError):pass

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def seal(bundle):
    """Bind exact renderer inputs before freezing the bundle; do not reseal retries."""
    result=dict(bundle);root=Path(bundle['directory'])
    if (root/'delivery.json').exists():raise ValueError('Cannot seal an attempted bundle')
    result['preflight_version']=1
    result['input_hashes']={name:digest(root/name) if (root/name).is_file() else None for name in INPUTS}
    result['artifact_order']=[str(Path(p['path']).resolve().relative_to(root.resolve())) for p in bundle['files']]
    return result

def number(value):
    if value is None:return None
    result=Decimal(str(value))
    if not result.is_finite():raise ValueError('nonfinite_amount')
    return result

def equal(a,b):
    if a is None or b is None:return a is None and b is None
    return abs(number(a)-number(b))<=Decimal('0.000001')

def inspect_report(report,account,day,errors,warnings,label):
    def require(ok,code):
        if not ok:errors.append(label+':'+code)
    require(str(report['account'])==account and report['date']==day,'account_or_date')
    require(not report.get('execution_enabled',False) and not report.get('feedback_required',False),'readonly_scope')
    from . import product_breakdown
    try:product_breakdown.validate(report,report.get('product_breakdowns',[]))
    except (ValueError,KeyError,TypeError):errors.append(label+':product_breakdown_conflict')
    rows=report['rows'];ids=[str(r['plan_id']) for r in rows]
    require(len(ids)==len(set(ids)),'duplicate_plans')
    known=[r for r in rows if r.get('profit') is not None]
    require(len(known)==report['calculable'] and len(rows)-len(known)==report['missing_profit'],'coverage_count')
    for i,row in enumerate(rows):
        if row.get('gmv_source')=='business_rule_zero_spend':
            require(plan_profit.zero_gmv_eligible(row,row.get('evidence'),day) and row.get('raw_gmv') is None
                    and equal(row.get('gmv'),0) and equal(row.get('spend'),0)
                    and row.get('gmv_rule_version')==plan_profit.ZERO_GMV_RULE,'invalid_business_zero_evidence')
            warnings.append(label+':business_rule_imputation')
        if report['policy'].get('model_version'):
            require(row.get('policy')==plan_profit.resolve_policy(report['policy'],row['plan_id']),'row_policy_binding')
        expected=plan_profit.calculate(row.get('gmv'),row.get('spend'),row.get('policy',report['policy']))
        for key,value in expected.items():require(equal(row.get(key),value),'row_'+str(i)+'_'+key)
        if row.get('profit') is None:warnings.append(label+':row_'+str(i)+':profit_pending')
    for key in ('gmv','spend','profit'):
        total=sum((number(r[key]) for r in known),Decimal(0)) if known else None
        require(equal(report['totals_calculable_only'][key],total),'total_'+key)
    for i,control in enumerate(report.get('checks',[])):
        if control.get('difference') is not None:require(number(control['difference'])==0,'api_difference_'+str(i))
        if control.get('aggregate') is None:
            warnings.append(label+':api_'+str(i)+':pending');continue
        key='gmv' if control['metric']=='total_pay_order_gmv_include_coupon_for_roi2' else 'spend'
        matching=[r for r in rows if (r.get('evidence') or {}).get('topic')==control['topic']]
        if any(r.get(key) is None for r in matching):warnings.append(label+':api_'+str(i)+':incomplete');continue
        total=sum((number(r[key]) for r in matching),Decimal(0))
        require(equal(total,control['aggregate']),'api_total_'+str(i))
    warnings.extend(label+':'+str(w) for w in report.get('warnings',[]))


def workbook_cells(path):
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(path) as z:
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            strings=[''.join(e.itertext()) for e in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si',ns)]
        relationships={r.get('Id'):r.get('Target') for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        sheets={}
        for sheet in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',ns):
            target=relationships[sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')]
            target=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
            cells={}
            for cell in ET.fromstring(z.read(target)).findall('.//s:c',ns):
                value=cell.findtext('s:v',namespaces=ns)
                if cell.get('t')=='s':value=strings[int(value)]
                elif cell.get('t')=='inlineStr':value=''.join(cell.find('s:is',ns).itertext())
                cells[cell.get('r')]=value
            sheets[sheet.get('name')]=cells
        return sheets


def inspect_excel(path,main,errors):
    sheets=workbook_cells(path);detail=sheets['全部计划测算'];summary=sheets['昨日计划汇总'];policy=sheets['模型口径与覆盖'];sheets['缺失与对账']
    def require(ok,code):
        if not ok:errors.append('excel:'+code)
    def numeric(cells,ref,value):
        got=cells.get(ref)
        if value is None:require(got=='待核验',ref+'_missing')
        else:
            try:require(equal(got,value),ref+'_amount')
            except Exception:require(False,ref+'_type')
    if main.get('product_breakdowns') or main.get('product_binding_pending'):
        extra=sheets.get('商品级补充明细',{});n=2
        for v in main.get('product_breakdowns',[]):
            require(extra.get('A'+str(n))==v['name'],'product_parent');n+=1
            for p in v['products']:
                require(extra.get('A'+str(n))==p['name'] and extra.get('B'+str(n))==main['date'],'product_name_date')
                numeric(extra,'C'+str(n),p['gmv']);numeric(extra,'D'+str(n),p['spend'])
                require('款号待核验' in extra.get('E'+str(n),''),'product_sku');n+=1
        for p in main.get('product_binding_pending',[]):
            require(extra.get('E'+str(n))==p['reason'],'binding_reason');n+=1
    require(main['date'] in detail.get('A2','') and main['date'] in summary.get('A2',''),'date')
    require('账户：'+str(main['account']) in policy.get('A13',''),'account')
    if main['policy'].get('model_version'):
        for i,row in enumerate(main['rows']):numeric(detail,'Q'+str(i+7),row['policy']['refund_rate'])
    for cell,key in [('B6','refund_rate'),('B7','purchase_multiplier'),('B8','fee_rate'),('B9','ad_rate')]:numeric(policy,cell,main['policy'][key])
    known=[]
    for i,row in enumerate(main['rows'],7):
        require(detail.get('B'+str(i))==str(row['plan_id']),'plan_id_'+str(i));require(detail.get('A'+str(i))==row['name'],'name_'+str(i))
        for col,key in [('D','gmv'),('E','spend'),('F','net'),('G','cost'),('H','fee'),('I','preprofit'),('J','profit'),('K','ad15'),('L','profit15'),('M','headroom'),('O','candidate')]:numeric(detail,col+str(i),row.get(key))
        require(detail.get('P'+str(i))=='待核验','today_budget_'+str(i))
        if row.get('gmv_source')=='business_rule_zero_spend':require('业务规则补值' in detail.get('C'+str(i),''),'business_zero_label_'+str(i))
        if row['profit'] is not None:known.append(row)
    for i,row in enumerate(known,7):
        require(summary.get('A'+str(i))==row['name'],'summary_name_'+str(i))
        for col,key in [('B','spend'),('C','profit'),('D','gmv')]:numeric(summary,col+str(i),row[key])
    for col,key in [('B','spend'),('C','profit'),('D','gmv')]:numeric(summary,col+str(len(known)+7),main['totals_calculable_only'][key])


def check(bundle,*,expected_account=None,expected_day=None):
    root=Path(bundle['directory']);errors=[];warnings=[];checked=[];coverage=None
    def require(ok,code):
        if not ok:errors.append(code)
    try:
        require(expected_account is None or str(bundle.get('account'))==str(expected_account),'requested_account')
        require(expected_day is None or bundle.get('date')==expected_day,'requested_date')
        require(bundle.get('preflight_version')==1,'unsealed_bundle')
        require(bundle.get('guide_version')==1,'missing_guide')
        require(bool(bundle.get('actors')) and len(bundle['actors'])==len(set(bundle['actors'])),'operator_once')
        require('<at' not in bundle.get('text','').lower() and bundle['date'] in bundle.get('text',''),'text_scope')
        paths=[str(Path(f['path']).resolve().relative_to(root.resolve())) for f in bundle['files']]
        expected=['guide/guide.png']+sorted(p for p in paths if p.startswith('profit-'+bundle['date']+'-') and p.endswith('.png'))+['profit-'+bundle['date']+'.xlsx','two_day/stack.png','two_day/pies.png']
        require(paths==expected and len(paths)>=5 and len(paths)==len(set(paths)),'attachment_order')
        require(paths==bundle.get('artifact_order'),'frozen_order')
        for item in bundle['files']:
            path=Path(item['path']);require(digest(path)==item['sha256'],'attachment_hash:'+path.name)
            require(item['kind']==('file' if path.suffix=='.xlsx' else 'image'),'attachment_type')
            if item['kind']=='image':require(path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'),'image_format')
        for name in INPUTS:require(digest(root/name)==bundle.get('input_hashes',{}).get(name),'input_hash:'+name)
        checked.append('all_attachments_and_input_hashes')
        data={name:json.loads((root/name).read_text()) for name in INPUTS}
        main=data['result.json'];day=bundle['date'];account=str(bundle['account']);before=(date.fromisoformat(day)-timedelta(days=1)).isoformat()
        inspect_report(main,account,day,errors,warnings,'main')
        prior=data['guide/previous.json']
        if prior:
            inspect_report(prior,account,before,errors,warnings,'previous')
            require(prior['policy']==main['policy'] or main['policy'].get('model_version')=='unified-readonly-v1','previous_policy_conflict')
            if prior['policy']!=main['policy']:warnings.append('policy_transition:previous_comparison_pending')
        else:warnings.append('previous_report_pending')
        coverage_days=data['two_day/collected.json']
        coverage_day=next((d for d in coverage_days if d.get('day')==day),None)
        coverage=data_completeness.build(main,coverage_day)
        if not coverage['total_plans']:warnings.append('coverage:no_plan_rows_pending')
        if coverage['product_mapping']['unlinked'] or coverage['product_mapping']['pending']:warnings.append('coverage:product_mapping_pending')
        expected_guide=daily_guide.build(main,prior,(date.fromisoformat(day)+timedelta(days=1)).isoformat(),coverage_day=coverage_day,include_coverage='coverage' in data['guide/guide.json'],coverage_version=data['guide/guide.json'].get('coverage',{}).get('version',2),decision_version=data['guide/guide.json'].get('decision_version',0))
        require(expected_guide==data['guide/guide.json'],'guide_content_conflict')
        checked.extend(['plan_calculation','totals_and_api_controls','guide_dates_and_actions'])
        days=data['two_day/collected.json'];reports=data['two_day/reports.json']
        require([d['day'] for d in days]==[before,day] and bundle.get('comparison_days')==[before,day],'comparison_dates')
        require(len(reports)==2,'comparison_reports_count')
        require(reports[-1]==main and reports[0]==prior,'comparison_report_source')
        for item,report in zip(days,reports):
            require(str(item.get('account'))==account,'comparison_account')
            require(not item.get('conflicts'),'source_conflicts')
            from . import refund_differences
            acknowledged=refund_differences.covered(item,report,refund_differences.records(root)) if report else []
            if any(k in item for k in ('spend_verified','profit_verified','daily_profit_by_plan','profit_basis')):
                projection=json.loads(json.dumps(item))
                for k in ('spend_verified','profit_verified','daily_profit_by_plan','profit_basis'):projection.pop(k,None)
                if report:refund_differences.annotate(projection,report,refund_differences.records(root))
                for k in ('spend_verified','profit_verified','daily_profit_by_plan','profit_basis'):
                    require(item.get(k)==projection.get(k),'scoped_refund_projection_'+k)
            if item.get('confirmed_refund_differences'):
                require(acknowledged==item['confirmed_refund_differences'] and not item['complete'],'refund_confirmation_invalid')
            require(not any(('不一致' in w or '政策不同' in w) and not (w==refund_differences.CONFLICT and acknowledged and not item['complete']) for w in item.get('warnings',[])),'comparison_conflict')
            if acknowledged:warnings.append('business_confirmed_refund_basis_difference:original_values_preserved')
            warnings.extend('comparison:'+str(w) for w in item.get('warnings',[]))
            if report:
                require(report['policy']==main['policy'] or (main['policy'].get('model_version')=='unified-readonly-v1' and item.get('policy_transition') is True and not item['complete']),'comparison_policy')
                if item.get('profit_policy') is not None:require(item['profit_policy']==report['policy'],'row_policy_source')
                require(item.get('report_missing')==report['missing_profit'],'comparison_missing_count')
                by_id={str(r['plan_id']):r for r in report['rows']}
                for row in item['rows']:
                    r=by_id.get(row['plan'],{})
                    managed='全店托管' in str(r.get('plan_name') or r.get('name',''))
                    require(bool(row.get('managed'))==managed,'managed_classification')
                copy=json.loads(json.dumps(item));two_day_daily.reconcile_main(copy,report)
                require(not (item['complete'] and not copy['complete']),'hourly_vs_daily')
            if not item['complete']:warnings.append('comparison_day_pending:'+item['day'])
        require(product_spend_daily.build(days)==data['two_day/stack.json'],'stack_content_conflict')
        pies=two_day_daily.pie_view(days,main['policy']);require(pies==data['two_day/pies.json'],'pies_content_conflict')
        for item,report in zip(pies['days'],reports):
            if item['complete'] and report:
                total=sum((number(item.get(k)) or Decimal(0) for k in ('positive_total','loss_total','managed_profit','unknown_profit')),Decimal(0))
                # The main report may cover live plans beyond these product-only charts.
                covered={row['plan'] for d in days if d['day']==item['day'] for row in d['rows']}
                known=[r for r in report['rows'] if r['plan_id'] in covered and r['profit'] is not None]
                require(equal(total,sum((number(r['profit']) for r in known),Decimal(0))),'profit_category_bridge')
        checked.append('comparison_values_categories_and_shares')
        inspect_excel(root/('profit-'+day+'.xlsx'),main,errors);checked.append('xlsx_cached_values_and_policy')
    except Exception as exc:
        # No exception payloads, credentials, names or identity values in console errors.
        errors.append('unreadable_or_invalid_input:'+type(exc).__name__)
    result={'version':1,'checked_at':datetime.now(timezone.utc).isoformat(),'date':bundle.get('date'),'allowed':not errors,
            'status':'BLOCKED' if errors else 'PASS_WITH_MISSING' if warnings else 'PASS','checks':checked,
            'errors':sorted(set(errors)),'warnings':sorted(set(warnings)),'coverage':coverage,
            'input_hashes':bundle.get('input_hashes',{}),'artifact_hashes':[{"name":Path(p['path']).name,"sha256":p.get('sha256')} for p in bundle.get('files',[])],
            'scope':'Input arithmetic, cross-artifact values, XLSX cached values, file hashes; no OCR or financial-final-profit certification.'}
    profit_daily.save(root/'preflight.json',result)
    lines=['日报发送前体检：'+result['status'],'数据日期：'+str(result['date']),'仅留本地，不新增群消息。']
    lines+=data_completeness.text_lines(coverage)
    lines+=['阻止发送：'+x for x in result['errors']]+['待核验：'+x for x in result['warnings']]
    (root/'preflight.txt').write_text('\n'.join(lines),encoding='utf-8')
    if errors:raise PreflightBlocked('Daily preflight blocked; inspect local preflight.json')
    return result
