/* Focused cosmetic acceptance: actual upstream root cards, no production/profile access. */
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {mkdirSync,mkdtempSync,writeFileSync,chmodSync,rmSync} = require('node:fs');
const {join} = require('node:path');
const {tmpdir} = require('node:os');
const bin=process.env.AGENT_BROWSER_BIN||'agent-browser';
const chrome=process.env.CHROME_BIN||'/opt/google/chrome/chrome';
const url=process.env.OWNER_GROUPS_DEMO_URL||'http://127.0.0.1:8875/demo/';
assert(/^http:\/\/127\.0\.0\.1:\d+\/demo\/$/.test(url));
const output=process.env.OWNER_GROUPS_SCREENSHOTS||'/tmp/owner-rounded-star';
mkdirSync(output,{recursive:true});
const temp=mkdtempSync(join(tmpdir(),'owner-star-'));
const desktopChrome=join(temp,'chrome');
writeFileSync(desktopChrome,'#!/bin/sh\nexec '+JSON.stringify(chrome)+' "--blink-settings=primaryPointerType=4,availablePointerTypes=4,primaryHoverType=2,availableHoverTypes=2" "$@"\n');
chmodSync(desktopChrome,0o700);
let session='owner-star-desktop-'+process.pid;
const run=(...args)=>execFileSync(bin,['--session',session,...args],{encoding:'utf8',timeout:40000});
const evaluate=code=>{const r=JSON.parse(run('eval',code,'--json'));assert(r.success,r.error);return r.data.result;};
const wait=code=>run('wait','--fn',code);
const row=id=>`.session[data-session-id="demo-${id}"]`;
const control=state=>evaluate(`fetch('/demo/__fixture__/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(${JSON.stringify(state)})}).then(r=>r.json())`);
const refresh=()=>evaluate('CodoxearOwnerPanel.controller.refresh().then(()=>true)');
const shot=name=>run('screenshot',join(output,name+'.png'));
function openSidebar(){if(!evaluate('document.body.classList.contains("sidebar-open")'))run('click','#toggleSidebarBtn');}
function moveAway(){run('mouse','move','385','20');}
function settleNativeSelection(){
  // Selection changes the upstream render signature. Await its real next render
  // before a touch drag; upstream defers refresh only AFTER a swipe has opened.
  control({});
  const alias=evaluate(`fetch('/demo/api/sessions').then(r=>r.json()).then(d=>d.sessions[0].alias)`);
  wait(`[...document.querySelectorAll('#sessions .titleText')].some(n=>n.textContent===${JSON.stringify(alias)})`);
  refresh();
}
function geometry(label){
  const result=evaluate(`([...document.querySelectorAll('svg.owner-root-star')].map(s=>{
    const row=s.parentElement,r=row.getBoundingClientRect(),b=s.getBoundingClientRect();
    return {id:row.dataset.sessionId,active:row.classList.contains('active'),width:b.width,height:b.height,
      top:b.top-r.top,right:r.right-b.right,bottom:r.bottom-b.bottom,
      ownCard:row.matches('.session.owner-root')&&getComputedStyle(row).position==='relative'&&getComputedStyle(s).position==='absolute',
      pointer:getComputedStyle(s).pointerEvents,visibility:getComputedStyle(s).visibility,
      label:s.getAttribute('aria-label'),title:s.querySelector('title')?.textContent,
      ids:s.querySelectorAll('[id]').length,fill:s.querySelector('path').getAttribute('fill')};
  }))`);
  assert.deepEqual(result.map(x=>x.id),['demo-ops','demo-other']);
  assert(result.every(x=>x.width===20&&x.height===20&&x.top>=0&&x.top<=4&&x.right>=0&&x.right<=4&&x.bottom>=0&&x.ownCard&&x.pointer==='none'&&x.visibility==='visible'&&x.label==='最高负责人'&&x.title===x.label&&x.ids===0&&x.fill==='#ffdf00'));
  console.log('GEOMETRY '+label+' '+JSON.stringify(result));return result;
}
// Send trusted input/capture only to this isolated agent-browser's own page.
function cdp(commands){
  const code=`const [url,pageUrl,commands]=JSON.parse(process.argv[1]);const ws=new WebSocket(url),p=new Map();let id=0;
    const send=(method,params={},sessionId)=>new Promise((resolve,reject)=>{p.set(++id,{resolve,reject});ws.send(JSON.stringify({id,method,params,sessionId}));});
    ws.onmessage=e=>{const m=JSON.parse(e.data),q=p.get(m.id);if(q){p.delete(m.id);m.error?q.reject(Error(m.error.message)):q.resolve(m.result);}};
    ws.onopen=async()=>{try{const t=await send('Target.getTargets');const page=t.targetInfos.find(t=>t.type==='page'&&t.url.startsWith(pageUrl));if(!page)throw Error('Missing owned fixture');const s=await send('Target.attachToTarget',{targetId:page.targetId,flatten:true});const out=[];for(const c of commands)out.push(await send(c.method,c.params,s.sessionId));console.log(JSON.stringify(out));}catch(e){console.error(e);process.exitCode=1;}finally{ws.close();}};`;
  return JSON.parse(execFileSync(process.execPath,['-e',code,JSON.stringify([run('get','cdp-url').trim(),url,commands])],{encoding:'utf8',timeout:15000}));
}
function closeCard(name){
  const r=evaluate(`document.querySelector('${row('ops')}').getBoundingClientRect().toJSON()`);
  const [capture]=cdp([{method:'Page.captureScreenshot',params:{format:'png',clip:{x:r.x,y:r.y,width:r.width,height:r.height,scale:3}}}]);
  writeFileSync(join(output,name+'.png'),Buffer.from(capture.data,'base64'));
}
try{
  for(const mobile of [false,true]){
    session='owner-star-'+(mobile?'mobile-':'desktop-')+process.pid;
    run('--executable-path',mobile?chrome:desktopChrome,'open',url);
    control({reset:true});run('set','viewport',mobile?'390':'1440',mobile?'844':'1000');
    run('wait','#loginForm');run('fill','input[type=password]','demo');run('click','#loginForm button');wait('document.querySelectorAll(".owner-root-star").length===2');
    if(mobile)openSidebar();
    run('click','#settingsBtnSide');run('find','role','radio','click','--name','Slate','--exact');run('find','role','radio','click','--name','Light','--exact');run('find','role','button','click','--name','Close','--exact');
    if(mobile)openSidebar();
    run('click',row('office-opaque-123'));if(mobile)openSidebar();moveAway();
    assert(geometry((mobile?'mobile':'desktop')+' inactive').every(x=>!x.active));
    shot((mobile?'mobile':'desktop')+'-inactive-overview');closeCard((mobile?'mobile':'desktop')+'-inactive-root-close');
    run('click',row('ops'));if(mobile)openSidebar();moveAway();settleNativeSelection();
    assert(geometry((mobile?'mobile':'desktop')+' active')[0].active);
    shot((mobile?'mobile':'desktop')+'-active-overview');closeCard((mobile?'mobile':'desktop')+'-active-root-close');
    if(mobile){
      const r=evaluate(`(()=>{window.starSwipeTarget=document.querySelector('${row('ops')} .sessionContent');return starSwipeTarget.getBoundingClientRect().toJSON()})()`),x=r.right-24,y=r.y+r.height/2;
      const commands=[{method:'Input.dispatchTouchEvent',params:{type:'touchStart',touchPoints:[{x,y}]}}];
      for(let i=1;i<=6;i++)commands.push({method:'Input.dispatchTouchEvent',params:{type:'touchMove',touchPoints:[{x:x-160*i/6,y}]}});
      commands.push({method:'Input.dispatchTouchEvent',params:{type:'touchEnd',touchPoints:[]}});cdp(commands);
      const gesture=evaluate(`({connected:starSwipeTarget.isConnected,swipe:document.querySelector('${row('ops')} .sessionContent').dataset.swipeX})`);
      console.log('TOUCH '+JSON.stringify(gesture));assert(gesture.connected&&Number(gesture.swipe)<-50);
    }else run('hover',row('ops'));
    assert.equal(evaluate(`getComputedStyle(document.querySelector('${row('ops')} .owner-root-star')).visibility`),'hidden');
    run('click',row('ops')+' button[aria-label="Edit conversation"]');run('wait','#editViewer[open]');run('press','Escape');
    if(mobile)openSidebar();run('click',row('office-opaque-123'));if(mobile)openSidebar();moveAway();geometry((mobile?'mobile':'desktop')+' after native Edit');
    for(const variant of ['root-missing','unresolved']){
      control({variant});refresh();
      const stars=evaluate(`[...document.querySelectorAll('svg.owner-root-star')].map(s=>s.parentElement.dataset.sessionId)`);
      if(variant==='root-missing')assert(!stars.includes('demo-ops'));
      assert(!stars.includes('demo-orphan'));assert(stars.every(id=>['demo-ops','demo-other'].includes(id)));
    }
    console.log('PASS '+(mobile?'mobile':'desktop')+' root-only rounded SVG, own-card geometry active/inactive, controls unobscured and native Edit works.');
    control({variant:'normal'});refresh();run('close');
  }
  console.log('SCREENSHOTS '+output);
}finally{try{control({reset:true});}catch(_){}try{run('close');}catch(_){}rmSync(temp,{recursive:true,force:true});}
