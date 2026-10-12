import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
const kind = process.argv[2];
const mouse = { acquired:0, disposed:0, owned:false };
globalThis.__copyLinksMouseFixture = mouse;
const hook = registerHooks({
  load(url, context, nextLoad) {
    const result = nextLoad(url, context);
    if (!url.endsWith('/src/tmux.ts')) return result;
    return { ...result, source: `export async function enableTmuxMouse() {
      const state = globalThis.__copyLinksMouseFixture;
      state.acquired++; state.owned=true;
      return { enabled:true, warnings:${kind === 'mouse-warning-ui' ? '["fixture warning"]' : '[]'}, async dispose() {
        state.disposed++; state.owned=false;
        ${kind === 'unsafe-secondary' ? 'throw new Error("secondary mouse release");' : ''}
      }};
    }` };
  },
});
const { default: extension } = await import('../index.ts');
const { installAdapter } = await import('../src/adapter.ts');
const { Markdown, TuiAltScreen, getOsc8LinkAtColumn } = await import('@earendil-works/pi-tui');
const surfaces = [[Markdown.prototype,'render'],[Markdown.prototype,'renderToken'],[Markdown.prototype,'renderInlineTokens'],[TuiAltScreen.prototype,'handleViewportInput']];
const baseline = surfaces.map(([owner,key])=>Object.getOwnPropertyDescriptor(owner,key));
const identity = text=>text;
const theme=Object.fromEntries(['heading','link','linkUrl','code','codeBlock','codeBlockBorder','quote','quoteBorder','hr','listBullet','bold','italic','strikethrough','underline'].map(key=>[key,identity]));
const terminal={columns:64,rows:20,kittyProtocolActive:false,start(){assert.fail('not a terminal-start fixture');},stop(){},async drainInput(){},write(){},moveBy(){},hideCursor(){},showCursor(){},clearLine(){},clearFromCursor(){},clearScreen(){},setTitle(){},setProgress(){},setProgramStatus(){}};
const nativeOpens=[];
const tui=new TuiAltScreen(terminal,false,undefined,{openUrl:url=>nativeOpens.push(url),copyOnSelect:false,copySelection:async()=>true});
if(kind==='notify') Object.defineProperty(tui,'mode',{value:'regular'});
let transformer, invalidations=0, renders=0, widgetRemovals=0, hadPrivateFrame=false, notifyFailed=false;
const source='```sh\nstartup source\tbytes  \n```';
const markdown=new Markdown(source,0,0,theme,undefined,{transform:(text,availableWidth)=>transformer(text,{messageType:'assistant',isStreaming:false,availableWidth})});
const paint=()=>{markdown.invalidate();tui.previousScreen=markdown.render(64);tui.previousScreenWidth=64;tui.previousScreenHeight=20;hadPrivateFrame ||= tui.previousScreen.some(line=>line.includes('pi-copy://'));};
Object.defineProperty(tui,'invalidate',{value(){
  invalidations++;
  if(kind==='invalidate'&&invalidations===1) throw new Error('primary final invalidate failure');
  if(kind==='unsafe-secondary'&&invalidations>1) throw new Error('secondary quiescence failure');
  paint();
}});
Object.defineProperty(tui,'requestRender',{value(){
  if(++renders===1&&(kind==='requestRender'||kind==='unsafe-secondary')) throw new Error('primary final requestRender failure');
}});
Object.defineProperty(tui,'renderNow',{value(){paint();}});
const events=new Map(),commands=new Map(),notices=[];
const pi={registerMarkdownTransformer(fn){transformer=fn;},on(name,fn){events.set(name,fn);},registerCommand(name,command){commands.set(name,command);}};
const ctx={mode:'tui',cwd:'/fixture',ui:{theme:{fg:(_color,text)=>text},
  notify(text,level){
    if(!notifyFailed&&((kind==='notify'&&level==='info')||(kind==='mouse-warning-ui'&&text.includes('fixture warning')))) {
      notifyFailed=true;throw new Error('primary UI notify failure');
    }
    notices.push({text,level});
  },
  setWidget(_key,factory){
    if(!factory){widgetRemovals++;if(kind==='unsafe-secondary'||kind==='widget')throw new Error('secondary widget removal '+widgetRemovals);return;}
    if(kind==='widget')throw new Error('primary widget acquisition failure');
    factory(tui);
  },
}};
extension(pi);
try {
  await events.get('session_start')({},ctx);
  assert.equal(mouse.owned,false,'mouse ownership retained after failed startup');
  assert.equal(mouse.disposed,kind==='widget'?0:1);
  assert.ok(widgetRemovals>=1,'widget removal not attempted');
  const expected=kind==='widget'?'primary widget acquisition failure':kind==='invalidate'?'primary final invalidate failure':kind==='requestRender'||kind==='unsafe-secondary'?'primary final requestRender failure':'primary UI notify failure';
  assert.equal(notices.length,1,'secondary cleanup feedback replaced/duplicated primary diagnostic');
  assert.match(notices[0].text,new RegExp(expected));
  assert.ok(notices[0].text.length<400);
  assert.equal(notices[0].level,'warning');
  if(kind==='widget') {
    await events.get('session_start')({},ctx);
    assert.equal(notices.length,1,'same primary failure warned twice because secondary cleanup changed');
    assert.equal(mouse.acquired,0);
  }
  const after=surfaces.map(([owner,key])=>Object.getOwnPropertyDescriptor(owner,key));
  assert.deepEqual(after.slice(0,3),baseline.slice(0,3));
  if(kind==='unsafe-secondary'){
    assert.equal(hadPrivateFrame,true);
    const guard=after[3].value[Symbol.for('pi-copy-links:input-guard')];
    assert.equal(guard.passive,true);assert.equal(guard.minimal,true);assert.equal(guard.baseMethod,baseline[3].value);
    const stale=tui.previousScreen.flatMap((line,y)=>Array.from({length:64},(_,x)=>({x,y,url:getOsc8LinkAtColumn(line,x)}))).find(point=>point.url?.startsWith('pi-copy://'));
    assert.ok(stale);
    tui.handleViewportInput(`\x1b[<0;${stale.x+1};${stale.y+1}M`);
    tui.handleViewportInput(`\x1b[<0;${stale.x+1};${stale.y+1}m`);
    assert.deepEqual(nativeOpens,[]);
    // A fresh render is native despite a stale composed frame/passive input guard.
    markdown.invalidate();assert.ok(markdown.render(64).every(line=>!line.includes('pi-copy://')));
  } else {
    assert.deepEqual(after,baseline,'successful quiescence must restore all original descriptors');
    markdown.invalidate();assert.ok(markdown.render(64).every(line=>!line.includes('pi-copy://')));
  }
  await commands.get('copy-links').handler('',ctx);
  assert.match(notices.at(-1).text,new RegExp(expected));
  assert.doesNotMatch(notices.at(-1).text,/实际兼容检查通过|已启用/);
  await events.get('session_shutdown')({},ctx);
  assert.equal(mouse.disposed,kind==='widget'?0:1,'retired mouse was disposed twice');
  console.log(JSON.stringify({kind,primary:notices[0].text,mouse,widgetRemovals,hadPrivateFrame,activeMarkdownHooks:0,privateTransformer:false,minimalPassiveGuard:kind==='unsafe-secondary',nativeOpens}));
} finally {
  // Only test-owned surfaces: absorb any expected passive guard once the fake
  // screen is verified clean, so the isolated process leaves no active hook.
  const cleanup=installAdapter({version:'fixture',active:()=>false,copy:async()=>{},cwd:()=>'/fixture',open:async()=>{},notify(){},button:identity,quiesce:()=>{paint();return tui.previousScreen.every(line=>!line.includes('pi-copy://'));}});
  cleanup.dispose();
  assert.deepEqual(surfaces.map(([owner,key])=>Object.getOwnPropertyDescriptor(owner,key)),baseline);
  hook.deregister();delete globalThis.__copyLinksMouseFixture;
}
