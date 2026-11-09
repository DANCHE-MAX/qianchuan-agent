// Isolated rendering test: fake DOM, fake data, no network or notifier.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
 constructor(){this.children=[];this.style={};this.value='';this.textContent='';this.hidden=false;}
 append(...items){this.children.push(...items);}
 replaceChildren(){this.children=[];}
}
const elements=new Map();
const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
get('metric').value='profit';
const data={end_day:'2026-01-07',note:'LOCAL_NOTE_ONLY',warnings:['LOCAL_WARNING_ONLY'],review_status:'已复核未发送',attachments:[],trends:[{date:'2026-01-07',profit:null,spend:null,gmv:null,groups:[],known:{profit:'10'},review_status:'已复核未发送'}],incidents:[],policy:null,comparison:[],shadow:[]};
let fail=false;
const context=vm.createContext({document:{getElementById:get,createElement:()=>new Element(),querySelectorAll:()=>[]},Option:class extends Element{},URLSearchParams,fetch:async()=>({ok:!fail,json:async()=>data})});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../dashboard/app.js'),'utf8'),context);
(async()=>{
 const html=fs.readFileSync(path.join(__dirname,'../dashboard/index.html'),'utf8');
 assert.equal((html.match(/<details class="section-help"/g)||[]).length,6);
 assert.equal((html.match(/<details[^>]*\bopen\b/g)||[]).length,0);
 assert.match(html,/退款率 \/ 取值依据/);assert.match(html,/不是当天实际退款率/);
 const plan=(name,goal,profit='10')=>({name,plan_id:name,goal,profit,spend:'2',quality:'数据齐全'});
 const group=(name,p)=>({id:name,name,plans:[p],profit:p.profit,spend:p.spend,quality:p.quality});
 data.trends[0].groups=[group('演示商品（历史商品名；款号待核验）',plan('商品计划','VIDEO_PROM_GOODS')),group('演示直播',plan('直播计划','LIVE_PROM_GOODS')),group('名称含直播但实际商品计划',plan('非直播投放','VIDEO_PROM_GOODS')),group('未知归属',plan('未知目标','UNKNOWN'))];
 data.comparison=[{name:'演示商品（历史商品名；款号待核验）',plan_id:'demo',historical_profit:'10',unified_profit:'9',difference:'-1',policy:{refund_rate:'.6',refund_band:'101_200'}}];
 data.product_breakdowns=[{name:'演示多商品',date:'2026-01-07',note:'仅解释计划；款号待核验。',missing:0,products:[{name:'演示明细',gmv:'0',spend:'.5'}]}];
 data.comparison.push({...data.comparison[0],name:'演示直播比较',plan_id:'直播计划'});
 const before=JSON.stringify(data);
 await vm.runInContext('load()',context);
 const text=e=>e.textContent+e.children.map(text).join('');
 assert.doesNotMatch(text(get('groups')),/演示直播|款号待核验/);
 assert.match(text(get('groups')),/演示商品/);assert.match(text(get('groups')),/名称含直播但实际商品计划/);assert.match(text(get('groups')),/未知归属/);
 assert.doesNotMatch(html,/product-breakdowns|计划内商品明细/);
 assert.equal(elements.has('product-breakdowns'),false,'No removed section accessed during rendering');
 assert.match(text(get('comparison')),/101–200元档/);assert.doesNotMatch(text(get('comparison')),/演示直播比较/);assert.doesNotMatch(html,/id="alerts"|id="incidents"|href="#alerts"/);assert.match(html,/利润测算对比/);
 assert.equal(JSON.stringify(data),before,'Rendering must not mutate source data');
 const full='演示商品（历史商品名；款号待核验）';assert.equal(get('groups').children[0].children[0].children[0].title,'演示商品');
 assert.match(fs.readFileSync(path.join(__dirname,'../dashboard/style.css'),'utf8'),/#answer[^}]*color:#111/);
 assert.match(text(get('metrics')),/10.00/);
 assert.equal(vm.runInContext("compactProductName('示例品牌条纹半拉链卫衣')",context),'条纹半拉链卫衣');
 assert.equal(vm.runInContext("compactProductName('红色半高领套装')",context),'红色半高领套装');
 data.answers={profit:'数据日期：2026-01-07（北京时间）。预估盈利靠前：1. 演示甲 10元；2. 演示乙 5元。\n粗略估计，2项待核验。'};await vm.runInContext("renderAnswer('profit')",context);assert.equal(get('answer').children.at(-1).textContent,'数据日期：2026-01-07（北京时间）');assert.match(get('answer').children[1].textContent,/1\. 演示甲/);assert.match(get('answer').children[2].textContent,/2\. 演示乙/);
 const originalTrends=data.trends;
 data.trends=[{date:'2026-01-01',groups:[{id:'a',profit:'5'}]},{date:'2026-01-02',groups:[{id:'b',profit:'99'}]},{date:'2026-01-03',review_status:'已复核未发送',groups:[{id:'a',profit:'-2'}]},{date:'2026-01-04',groups:[{id:'a',profit:'0',imputed:1}]},{date:'2026-01-05',groups:[{id:'a',profit:null}]}];
 const series=JSON.parse(vm.runInContext("JSON.stringify(productTrendPoints({id:'a'}))",context));
 assert.deepEqual(series.map(p=>p.value),[5,null,-2,0,null]);assert.equal(series[2].status,'已复核未发送');assert.equal(series[3].status,'含业务补值');data.trends=originalTrends;
 console.log('PASS: 6 closed help sections, explicit live-goal filter, display-only SKU removal, readable policy basis, immutable data and totals');
})().catch(e=>{console.error(e);process.exitCode=1});
