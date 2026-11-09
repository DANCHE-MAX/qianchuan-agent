'use strict';
let state=null;const $=id=>document.getElementById(id);const fmt=v=>v===null||v===undefined?'待核验':Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
function displayName(text){return String(text).replace(/（历史商品名；款号待核验）/g,'').replace(/；款号待核验。/g,'。')}
// Presentation only: full names, source records and group identities remain unchanged.
let productLabels=new Map();
function compactProductName(name){
 const full=displayName(name);
 const series=['诗系自然','冠军庆典','岁月静好','非遗巫溪嫁花','玩具总动员','星球大战','史迪奇','S.W.SMILEY','WHIZLIMITED','ROUNDTWO','TOYSSAWMYTEARS','DAILY','迪士尼','超市'].find(x=>full.includes(x))||'';
 const clean=full.replace(/陈伟霆|CANOTWAIT|CNW|202[0-9]|系列|联名|[_ ]/g,'').replace(series,'');
 const categories=['套装','开衫','羽绒服','羽绒外套','马甲','棒球帽','牛仔裤','工装长裤','长裤','短裤','运动裤','卫衣','夹克','外套','衬衫','毛衣','针织衫','T恤','短袖'];
 const category=categories.find(x=>clean.includes(x));
 if(!category)return full;
 const features=['七星瓢虫','叉叉','SLOGAN','步行机','条纹','半拉链','排骨','灯芯绒','仿羊羔毛','仿毛呢','摇粒绒','抓绒','珠串织带','铆钉','竖纹','涂鸦','公仔','苹果','提花','插肩袖','拼色','撞色','红色','蓝色','灰色','黑色','白色','手绘','刺绣','印花','立领','翻领','连帽','纯棉','加绒','圆领','长袖','短袖'];
 let selected=features.filter(x=>clean.includes(x)&&!category.includes(x)).slice(0,2);
 if(category==='套装'&&clean.includes('红色'))selected=['红色',...(clean.includes('半高领')?['半高领']:[])];
 return series+selected.join('')+category;
}
function prepareProductLabels(){
 productLabels=new Map();const candidates=new Set();
 for(const d of state.trends||[])for(const g of d.groups||[])if(!g.name.includes('归属待核验')&&g.name!=='全店托管')candidates.add(g.name);
 for(const r of state.comparison||[])if(r.name.includes('历史商品名'))candidates.add(r.name);
 for(const v of state.product_breakdowns||[])for(const p of v.products||[])candidates.add(p.name);
 const buckets=new Map();for(const n of [...candidates].sort()){const short=compactProductName(n);if(!buckets.has(short))buckets.set(short,[]);buckets.get(short).push(n)}
 for(const [short,names] of buckets)for(const n of names){let label=short;if(new Set(names.map(displayName)).size>1){let hash=2166136261;for(const c of displayName(n))hash=Math.imul(hash^c.charCodeAt(0),16777619);label+=' · '+(hash>>>0).toString(36).slice(-4)}productLabels.set(n,label);productLabels.set(displayName(n),label)}
}
function productName(name){return productLabels.get(name)||displayName(name)}
function nameOption(name,id){const o=new Option(productName(name),id);o.title=displayName(name);return o}
function node(tag,text,cls){const e=document.createElement(tag);if(text!==undefined){e.textContent=productName(text);if(productLabels.has(text))e.title=displayName(text)};if(cls)e.className=cls;return e}function clear(id){$(id).replaceChildren();return $(id)}
async function request(path){const r=await fetch(path);if(!r.ok)throw Error('数据或参数待核验，请检查日期和模型依据');return r.json()}
async function load(){try{state=await request('/api/data'+($('day').value?'?date='+encodeURIComponent($('day').value):''));$('day').value=state.end_day;render()}catch(e){$('notice').hidden=false;$('notice').textContent=e.message}}
function isLivePlan(id){return (state.trends||[]).some(d=>(d.groups||[]).some(g=>(g.plans||[]).some(p=>String(p.plan_id)===String(id)&&p.goal==='LIVE_PROM_GOODS')))}
function render(){prepareProductLabels();const files=clear('attachments');for(const f of state.attachments||[]){const a=node('a',f.kind==='file'?'下载原Excel':f.name);a.href='/api/artifact?'+new URLSearchParams({date:state.end_day,index:f.index});a.target='_blank';a.rel='noopener';files.append(a)}const latest=state.trends.at(-1);$('notice').textContent='';$('notice').hidden=true;const m=clear('metrics');for(const [k,title]of [['profit',state.backfill_status?'补采预估利润（不含直播）':state.review_status?'复核预估利润（未发送）':'历史日报预估利润'],['spend','实际广告消耗'],['gmv','千川GMV']]){const e=node('div',undefined,'metric');e.append(node('span',title),node('strong',fmt(latest[k]??latest.known?.[k])),node('small',latest[k]===null?'仅已知部分 · 完整合计待核验':latest.quality));m.append(e)}
const entity=clear('entity');entity.append(new Option('全部范围',''));const seen=new Map();state.trends.forEach(d=>d.groups.forEach(g=>seen.set(g.id,g.name)));seen.forEach((name,id)=>entity.append(nameOption(name,id)));chart();groups();
$('policy-text').textContent=state.policy?'当前统一口径：采购倍率3.2，费用13.10%，按确认分档退款率；无档位用总体率并标注。运费不计。':'当前模型政策待核验';
$('baseline-heading').textContent=state.backfill_status?'补采测算（未发送）':state.review_status?'复核版（未发送）':'历史日报';const tbody=clear('comparison'),sel=clear('scenario-plan');sel.append(new Option('总体退款率情景',''));for(const r of state.comparison){if(isLivePlan(r.plan_id))continue;sel.append(nameOption(r.name,r.plan_id));if(r.historical_profit!==null&&Number(r.historical_profit)===0&&r.unified_profit!==null&&Number(r.unified_profit)===0)continue;const tr=node('tr'),s=state.shadow.find(x=>x.plan_id===r.plan_id);for(const v of [r.name,fmt(r.historical_profit),fmt(r.unified_profit),fmt(r.difference),s?fmt(s.profit):'待核验',(Number(r.policy.refund_rate)*100).toFixed(2)+'% · '+(r.policy.refund_fallback?'未匹配档位，采用总体率':({overall:'已确认总体率',gt300:'300元以上档', '201_300':'201–300元档','101_200':'101–200元档'}[r.policy.refund_band]||r.policy.refund_band))])tr.append(node('td',v));tbody.append(tr)}}
function chart(){const target=clear('chart'),key=$('metric').value,id=$('entity').value;const values=state.trends.map(d=>({date:d.date,policy:d.policy?.model_version||'历史政策',review:d.review_status,backfill:d.backfill_status,value:id?d.groups.find(g=>g.id===id)?.[key]??null:d[key]??d.known?.[key]??null}));const max=Math.max(1,...values.filter(x=>x.value!==null).map(x=>Math.abs(Number(x.value))));for(const r of values){const c=node('div',undefined,'bar-cell');c.append(node('span',fmt(r.value)));const space=node('div',undefined,'bar-space');if(r.value!==null){const bar=node('div',undefined,'bar'+(Number(r.value)<0?' negative':''));bar.style.height=(Math.abs(Number(r.value))/max*80)+'px';bar.style.bottom=Number(r.value)>=0?'90px':'auto';if(Number(r.value)<0){bar.style.top='90px';bar.style.borderRadius='0 0 5px 5px';}space.append(bar)}c.append(space,node('small',r.date.slice(5)+'\n'+(r.value===null?'待核验':r.backfill?'补采测算·不含直播':r.review?'已复核·未发送':r.policy==='历史政策'?'历史口径·已知部分':'统一口径·已知部分')));target.append(c)}}
function groups(){const target=clear('groups'),query=$('search').value.toLowerCase();for(const g of state.trends.at(-1).groups){if(g.plans.length&&g.plans.every(p=>p.goal==='LIVE_PROM_GOODS'))continue;if(!query&&!$('show-zero').checked&&g.profit!==null&&Number(g.profit)===0&&g.spend!==null&&Number(g.spend)===0)continue;if(query&&!JSON.stringify([g.name,productName(g.name),g.plans.map(p=>[p.name,p.plan_id])]).toLowerCase().includes(query))continue;const details=node('details'),summary=node('summary');summary.append(node('b',g.name),node('span','利润 '+fmt(g.profit),g.profit!==null&&Number(g.profit)<0?'bad':'good'),node('span','消耗 '+fmt(g.spend)),node('span',g.quality,'tag'+(g.quality!=='数据齐全'?' pending':'')));details.append(summary);const scroll=node('div',undefined,'scroll'),table=node('table');for(const p of g.plans){const tr=node('tr');for(const val of [p.name,p.plan_id,'利润 '+fmt(p.profit),'消耗 '+fmt(p.spend),p.quality])tr.append(node('td',val));table.append(tr)}scroll.append(table);details.append(scroll);target.append(details)}if(!target.children.length)target.append(node('p','暂无匹配数据，待核验。','muted'))}
$('load').onclick=load;$('metric').onchange=chart;$('entity').onchange=chart;$('search').oninput=groups;$('show-zero').onchange=groups;
$('simulate').onclick=async()=>{try{const args=new URLSearchParams({date:state.end_day,plan:$('scenario-plan').value,spend:$('extra').value});const base=await request('/api/scenario?'+args);let text='当前确认退款率下，需要新增GMV '+fmt(base.required_gmv)+' 元。';if($('refund').value!==''){args.set('refund',Number($('refund').value)/100);const alt=await request('/api/scenario?'+args);text+=' 假设退款率 '+$('refund').value+'% 时，需要 '+fmt(alt.required_gmv)+' 元。'}$('scenario-result').textContent=text+' 这是情景对照，不是统计置信区间。'+base.note}catch(e){$('scenario-result').textContent=e.message}};
function renderAnswer(key){
 const target=clear('answer');let text=displayName(state?.answers[key]||'待核验');
 const stamp=text.match(/数据日期：[0-9-]+（北京时间）[。]?/);text=text.replace(/数据日期：[0-9-]+（北京时间）[。]?/,'');
 for(const [full,short] of [...productLabels.entries()].sort((a,b)=>b[0].length-a[0].length))text=text.split(displayName(full)).join(short);
 text=text.replace(/(预估盈利靠前|预估亏损靠前|广告消耗靠前)：/,'$1：\n');
 for(const line of text.split(/[；\n]+/).map(x=>x.trim()).filter(Boolean))target.append(node('p',line,'answer-line'));
 target.append(node('p',stamp?stamp[0].replace(/。$/,''):'数据日期：'+(state?.end_day||'待核验')+'（北京时间）','answer-date'));
}
document.querySelectorAll('[data-question]').forEach(b=>b.onclick=()=>renderAnswer(b.dataset.question));load();
