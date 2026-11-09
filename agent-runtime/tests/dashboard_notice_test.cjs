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
 await vm.runInContext('load()',context);
 assert.equal(get('notice').hidden,true);assert.equal(get('notice').textContent,'');
 assert.equal(data.note,'LOCAL_NOTE_ONLY');assert.equal(data.warnings.length,1);
 const text=e=>e.textContent+e.children.map(text).join('');
 assert.match(text(get('metrics')),/待核验/);assert.match(text(get('chart')),/已复核·未发送/);
 fail=true;await vm.runInContext('load()',context);
 assert.equal(get('notice').hidden,false);assert.match(get('notice').textContent,/待核验/);
 fail=false;await vm.runInContext('load()',context);assert.equal(get('notice').hidden,true);
 console.log('PASS: long notice hidden, states retained, errors visible, recovery clears notice');
})().catch(e=>{console.error(e);process.exitCode=1});
