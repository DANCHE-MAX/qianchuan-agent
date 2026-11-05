"""Admission evidence for snapshot-difference analysis, without fabricating zero rows."""
from datetime import datetime, timedelta
from decimal import Decimal
import json
import re
from . import hourly as h, qianchuan as q
from .card_view import card, text_element

METRICS=('ad_spend','gmv','order_count','backend_net','comprehensive_cost')
TOPICS={'plan':q.TOPIC,'material':'SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO'}
SCOPES={'plan':'乘方商品计划累计报表','material':'商品视频素材累计报表'}
REASONS={'samples':'不足3个有效快照','stale':'最新快照已过期','metadata':'账户、范围、日期或时间信息无效',
         'gap':'半小时快照不连续或间隔不在25—40分钟内','identity':'计划或素材ID重复、无效',
         'presence':'新增、缺失或重新出现的对象不足3次连续观察','missing':'指标字段缺失',
         'invalid':'金额或订单数无效','revision':'出现负增量，疑似退款或归因回补',
         'collect_failed':'本轮采集失败，暂停本轮异常分析'}
VERSION=26


def stamp(value):
    at=datetime.fromisoformat(value)
    if at.tzinfo is None:raise ValueError()
    return at.astimezone(h.CHINA)


def number(row,key):
    if key not in row:return None,'missing'
    try:
        value=Decimal(str(row[key]))
        if not value.is_finite() or value<0 or (key=='order_count' and value!=value.to_integral_value()):raise ValueError()
        return value,None
    except (ValueError,ArithmeticError,TypeError):return None,'invalid'


def validate(snapshot,account,kind,now):
    try:
        fetched=stamp(snapshot['fetched_at']);cutoff=stamp(snapshot['request_end']);bucket=stamp(snapshot['bucket'])
        if (str(snapshot['account'])!=str(account) or snapshot['kind']!=kind or snapshot['topic']!=TOPICS[kind]
                or snapshot['scope']!=SCOPES[kind] or snapshot['unit']!='CNY'
                or snapshot['day']!=now.date().isoformat() or cutoff.date()!=now.date()
                or bucket!=cutoff.replace(minute=cutoff.minute//30*30,second=0,microsecond=0)
                or cutoff>now+timedelta(minutes=5) or fetched>now+timedelta(minutes=5)
                or fetched<cutoff-timedelta(minutes=5) or not isinstance(snapshot['rows'],list)):
            return None,'metadata'
        rows={}
        for row in snapshot['rows']:
            pid=str(row['id'])
            if not pid.isascii() or not pid.isdigit() or pid in rows:return None,'identity'
            if not isinstance(row.get('name'),str) or not row['name'].strip():return None,'identity'
            rows[pid]=row
        return {'rows':rows,'cutoff':cutoff,'bucket':bucket},None
    except (KeyError,ValueError,TypeError,AttributeError):return None,'metadata'


def stale_minutes(root):
    from .health import options
    return options(root)['snapshot_stale_minutes']


def assess(series,account,kind,now,stale_minutes=45):
    if now.tzinfo is None:raise ValueError('分析时间须带时区')
    now=now.astimezone(h.CHINA)
    result={'state':'blocked','reasons':[],'entities':{},'counts':{'ready':0,'limited':0},
            'cumulative_valid':False,'checked_at':now.isoformat(),'kind':kind,'account':str(account)}
    def blocked(reason):result['reasons']=[reason];return result
    if not series:return blocked('samples')
    latest,error=validate(series[-1],account,kind,now)
    if error:return blocked(error)
    result['cumulative_valid']=all(number(row,key)[1] is None for row in latest['rows'].values() for key in METRICS)
    result['activity']=any((number(row,key)[0] or 0)>0 for row in latest['rows'].values() for key in ('ad_spend','gmv','order_count'))
    result['cutoff']=latest['cutoff'].isoformat()
    if (now-latest['cutoff']).total_seconds()>stale_minutes*60:return blocked('stale')
    # No records is a neutral observation, never evidence that there is no risk.
    if all(isinstance(s,dict) and isinstance(s.get('rows'),list) and not s['rows'] for s in series[-3:]):
        result['state']='empty';return result
    if len(series)<3:result['state']='waiting';return blocked('samples')
    values=[]
    for snapshot in series[-3:]:
        parsed,error=validate(snapshot,account,kind,now)
        if error:return blocked(error)
        values.append(parsed)
    for a,b in zip(values,values[1:]):
        seconds=(b['cutoff']-a['cutoff']).total_seconds()
        if b['bucket']-a['bucket']!=timedelta(minutes=30) or not 25*60<=seconds<=40*60:return blocked('gap')
    durations=[Decimal(str((b['cutoff']-a['cutoff']).total_seconds()))/3600 for a,b in zip(values,values[1:])]
    reasons=set()
    for pid in set().union(*(set(value['rows']) for value in values)):
        rows=[value['rows'].get(pid) for value in values]
        entity={'id':pid,'name':next(row['name'] for row in reversed(rows) if row is not None),
                'metrics':{},'previous':{},'current':{},'hours':durations}
        for key in METRICS:
            if any(row is None for row in rows):reason='presence'
            else:
                checked=[number(row,key) for row in rows]
                reason=next((error for _,error in checked if error),None)
                if reason is None:
                    previous=checked[1][0]-checked[0][0];current=checked[2][0]-checked[1][0]
                    if previous<0 or current<0:reason='revision'
                    else:entity['previous'][key]=previous;entity['current'][key]=current
            entity['metrics'][key]=reason or 'ready'
            if reason:reasons.add(reason)
        complete=all(entity['metrics'][key]=='ready' for key in METRICS)
        result['counts']['ready' if complete else 'limited']+=1
        result['entities'][pid]=entity
    result['state']='partial' if reasons else 'ready'
    result['reasons']=sorted(reasons)
    result['start']=series[-2]['request_end'];result['end']=series[-1]['request_end']
    return result


def usable(entity,*keys):return all(entity['metrics'].get(key)=='ready' for key in keys)


def interval(older,newer):
    """Two-point observation only; it cannot certify acceleration across two intervals."""
    try:
        now=stamp(newer['fetched_at']);account=newer['account'];kind=newer['kind']
        a,ea=validate(older,account,kind,now);b,eb=validate(newer,account,kind,now)
        if ea or eb:return None
        seconds=(b['cutoff']-a['cutoff']).total_seconds()
        if b['bucket']-a['bucket']!=timedelta(minutes=30) or not 25*60<=seconds<=40*60:return None
        rows={}
        for pid in a['rows'].keys() & b['rows'].keys():
            delta={};issues={}
            for key in METRICS:
                before,be=number(a['rows'][pid],key);after,ae=number(b['rows'][pid],key)
                reason=be or ae or ('revision' if after-before<0 else None)
                delta[key]=None if reason else after-before
                if reason:issues[key]=reason
            rows[pid]={'id':pid,'name':b['rows'][pid]['name'],**delta,
                       'elapsed_hours':Decimal(str(seconds))/3600,'revised':bool(issues),'unavailable_metrics':issues}
        return rows
    except (KeyError,ValueError,TypeError):return None


def describe(result):
    title={'blocked':'暂无法判断','waiting':'样本积累中，暂无法判断','partial':'部分指标暂无法判断',
           'ready':'本轮规则分析条件已满足','empty':'本轮接口返回无报表记录，不能据此认定无风险'}[result['state']]
    reasons='；'.join(REASONS.get(reason,reason) for reason in result['reasons'])
    note = '具备分析条件不代表没有异常。' if result['state']=='ready' else '未满足条件不代表没有异常。'
    return title+('：'+reasons if reasons else '')+'。累计数据与区间趋势分别核对；'+note


def finding_text(finding):
    money=lambda value:'未核验' if value is None else str(value)+'元'
    orders='未核验' if finding['orders'] is None else finding['orders']
    return '%s（%s）：%s；区间消耗%s、成交%s、订单%s，整体支付ROI %s。'%(finding['name'],finding['id'],
        '；'.join(finding['reasons']),money(finding['spend']),money(finding['gmv']),orders,finding['payment_roi'] or '暂无')


def safe_answer(text,info):
    admission=info.get('异常分析准入') or {}
    if admission.get('state')=='ready' or not admission:return text
    checked=text
    for phrase in ('不代表没有异常','不能认定没有异常','不能说明没有异常','无法确认没有异常'):
        checked=checked.replace(phrase,'')
    if not re.search(r'(?:没有|未发现|不存在|无)(?:任何)?(?:异常|风险)|一切正常',checked):return text
    totals=info.get('整体',{})
    fmt=lambda value:'暂无' if value is None else format(Decimal(str(value)),',.2f')
    return '截至快照%s，累计成交%s元、消耗%s元，整体支付ROI %s。对应区间分析条件不足，暂不能判断是否异常。'%(
        admission.get('cutoff') or info.get('最新采集时间','暂无'),fmt(totals.get('gmv')),
        fmt(totals.get('ad_spend')),fmt(totals.get('payment_roi')))


def record(store,result,now,*,notify_observation=True):
    """One notice per category/reason transition, not per plan/count or minute."""
    account,kind=result['account'],result['kind']
    fingerprint=result['state']+':'+','.join(sorted(set(result['reasons'])))
    with store.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS analysis_history(account TEXT,kind TEXT,point TEXT,fingerprint TEXT,stamp TEXT,payload TEXT,PRIMARY KEY(account,kind,point,fingerprint))')
        point=result.get('cutoff') or now.replace(minute=now.minute//30*30,second=0,microsecond=0).isoformat()
        db.execute('INSERT OR IGNORE INTO analysis_history VALUES(?,?,?,?,?,?)',
                   (account,kind,point,fingerprint,now.isoformat(),json.dumps(result,ensure_ascii=False,default=str)))
        if not notify_observation:
            # Retire queued routine notices only; never replay them after policy changes.
            routine_phrases=('不足3个有效快照','新增、缺失或重新出现的对象不足3次连续观察','本轮规则分析条件已满足','本轮接口返回无报表记录')
            serious_phrases=tuple(REASONS[k] for k in ('collect_failed','stale','metadata','gap','identity','missing','invalid','revision'))
            for key,payload in db.execute("SELECT id,payload FROM events WHERE account=? AND sent=0 AND id LIKE ?",(account,'analysis-status:'+account+':'+kind+':%')).fetchall():
                if any(t in payload for t in routine_phrases) and not any(t in payload for t in serious_phrases):
                    db.execute('UPDATE events SET sent=2 WHERE id=? AND sent=0',(key,))
        db.execute('CREATE TABLE IF NOT EXISTS analysis_states(account TEXT,kind TEXT,fingerprint TEXT,sequence INTEGER,payload TEXT,PRIMARY KEY(account,kind))')
        old=db.execute('SELECT fingerprint,sequence FROM analysis_states WHERE account=? AND kind=?',(account,kind)).fetchone()
        sequence=old[1] if old else 0
        if old and old[0]==fingerprint:
            db.execute('UPDATE analysis_states SET payload=? WHERE account=? AND kind=?',
                       (json.dumps(public(result),ensure_ascii=False,default=str),account,kind));return
        sequence+=1
        db.execute('INSERT OR REPLACE INTO analysis_states VALUES(?,?,?,?,?)',
                   (account,kind,fingerprint,sequence,json.dumps(public(result),ensure_ascii=False,default=str)))
        # First good/empty observation is silent. Ready after a limitation means evaluable, not safe.
        announce=(result['state'] not in ('ready','empty') and not (old is None and result['state']=='waiting' and not result.get('activity'))
                  or (old and result['state']=='ready' and not old[0].startswith('empty:')))
        if not notify_observation:
            announce=bool(set(result['reasons']) & {'collect_failed','stale','metadata','gap','identity','missing','invalid','revision'})
        if announce:
            key='analysis-status:'+account+':'+kind+':'+str(sequence)
            rendered=card('投流分析｜'+('计划' if kind=='plan' else '素材')+'数据检查',[
                text_element('账户%s｜%s'%(account,now.isoformat())),text_element(describe(result)),
                text_element('本轮对象可比性尚未评估；满足采样条件后再统计。' if result['state'] in ('blocked','waiting') else
                             '完整可比对象%d个，受限对象%d个；不补造缺失记录或把新计划历史当作0。'%
                             (result['counts']['ready'],result['counts']['limited']))])
            db.execute('INSERT OR IGNORE INTO events(id,account,stamp,payload) VALUES(?,?,?,?)',
                       (key,account,now.isoformat(),json.dumps(rendered,ensure_ascii=False)))
        # Queued explanations for a previous state must not replay after it changes.
        db.execute("UPDATE events SET sent=2 WHERE account=? AND sent=0 AND id LIKE ? AND id<>?",
                   (account,'analysis-status:'+account+':'+kind+':%','analysis-status:'+account+':'+kind+':'+str(sequence)))


def public(result):
    return {key:value for key,value in result.items() if key!='entities'}


def failed(account,kind,now):
    return {'account':str(account),'kind':kind,'state':'blocked','reasons':['collect_failed'],
            'counts':{'ready':0,'limited':0},'entities':{},'cumulative_valid':False,'checked_at':now.isoformat()}


def prepare_store(store,account):
    with store.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS analysis_proofs(id TEXT PRIMARY KEY,account TEXT,kind TEXT,entity TEXT,metrics TEXT)')
        db.execute("UPDATE events SET sent=2 WHERE account=? AND sent=0 AND id LIKE 'anomaly:%'",(account,))


def bind(store,key,account,finding):
    required=set()
    for reason in finding['reasons']:
        required.update(('gmv','order_count') if reason=='成交金额及订单数快速增加，需关注起量' else ('ad_spend','gmv'))
    with store.db() as db:
        db.execute('INSERT OR IGNORE INTO analysis_proofs VALUES(?,?,?,?,?)',
                   (key,account,finding['kind'],finding['id'],json.dumps(sorted(required))))


def allow_pending(store,key,account,admissions):
    with store.db() as db:
        proof=db.execute('SELECT kind,entity,metrics FROM analysis_proofs WHERE id=? AND account=?',(key,account)).fetchone()
    if not proof:return False
    assessment=admissions.get(proof[0])
    if not assessment or assessment['state'] not in ('ready','partial'):return False
    entity=assessment['entities'].get(proof[1])
    return bool(entity and usable(entity,*json.loads(proof[2])))
