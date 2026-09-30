// Process-local SDK mutations only; no installed files or live host are changed.
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
const kind = process.argv[2];
const imageMode = process.argv[3] === 'none' ? null : process.argv[3];
let transformed = 0;
const hook = registerHooks({
  load(url, context, nextLoad) {
    const result = nextLoad(url, context);
    let source = String(result.source);
    if (['undefined', 'null', 'delayed'].includes(kind) && url.endsWith('/pi-tui/dist/tui.js')) {
      const literal = '    renderedOverlayLayouts = [];';
      assert.equal(source.split(literal).length, 2);
      source = source.replace(literal, `    renderedOverlayLayouts = ${kind === 'delayed' ? '"pending first frame"' : kind};`);
      transformed++;
      return { ...result, source };
    }
    if (kind === 'width-drift' && url.endsWith('/pi-tui/dist/tui-alt-screen.js')) {
      const literal = '        this.addInputListener((data) => this.handleViewportInput(data));';
      assert.equal(source.split(literal).length, 2);
      source = source.replace(literal, literal + '\n        Object.defineProperty(this, "previousScreenWidth", { configurable: true, get() { return "wrong painted width"; }, set() {} });');
      transformed++;
      return { ...result, source };
    }
    if (kind === 'tab-cache-drift' && url.endsWith('/pi-tui/dist/components/markdown.js')) {
      const literal = '    setText(text) {';
      assert.equal(source.split(literal).length, 2);
      // Broken normalized-source caching: Tab-only edits disappear, whereas the
      // old co-changed href edit would have bypassed this simulated defect.
      source = source.replace(literal, literal + '\n        if (text.includes("   echo") && !text.includes("b=3")) return;');
      transformed++;
      return { ...result, source };
    }
    return result;
  },
});
const { checkCompatibility, installAdapter } = await import('../src/adapter.ts');
const { Markdown, TuiAltScreen, getOsc8LinkAtColumn, stripTerminalSequences, getCapabilities, setCapabilities } = await import('@earendil-works/pi-tui');
setCapabilities({ ...getCapabilities(), images: imageMode });
const capabilities = getCapabilities();
const capabilityBytes = JSON.stringify(capabilities);
const clipboardSymbol = Symbol.for('pi-copy-links:clipboard-queue');
const queueBefore = Object.getOwnPropertyDescriptor(globalThis, clipboardSymbol);
const surfaces = [[Markdown.prototype,'render'],[Markdown.prototype,'renderToken'],[Markdown.prototype,'renderInlineTokens'],[TuiAltScreen.prototype,'handleViewportInput']];
const before = surfaces.map(([owner,key]) => Object.getOwnPropertyDescriptor(owner,key));
const pending = checkCompatibility('unknown-conforming-cache-lifecycle');
for (let turn = 0; turn < 120; turn++) {
  await Promise.resolve();
  assert.equal(getCapabilities(), capabilities, 'capability identity changed during awaited work');
  assert.equal(JSON.stringify(getCapabilities()), capabilityBytes, 'capability value changed during awaited work');
  assert.deepEqual(Object.getOwnPropertyDescriptor(globalThis, clipboardSymbol), queueBefore);
  assert.deepEqual(surfaces.map(([owner,key]) => Object.getOwnPropertyDescriptor(owner,key)), before);
}
const check = await pending;
assert.equal(getCapabilities(), capabilities);
assert.deepEqual(Object.getOwnPropertyDescriptor(globalThis, clipboardSymbol), queueBefore);
if (kind === 'width-drift' || kind === 'tab-cache-drift') {
  assert.equal(check.compatible, false);
  assert.match(check.cause, kind === 'width-drift' ? /复制未保留/ : /Tab→空格.*旧数据/);
  console.log(JSON.stringify({ kind, imageMode, transformed, check, capabilitiesUnchangedThroughout: true }));
  hook.deregister();
} else {
  assert.equal(check.compatible, true, check.cause);
  const identity = text => text;
  const theme = Object.fromEntries(['heading','link','linkUrl','code','codeBlock','codeBlockBorder','quote','quoteBorder','hr','listBullet','bold','italic','strikethrough','underline'].map(key => [key,identity]));
  const terminal = {
    columns:64, rows:20, kittyProtocolActive:false, input() {},
    start(input) { this.input=input; }, stop() {}, async drainInput() {}, write() {}, moveBy() {},
    hideCursor() {}, showCursor() {}, clearLine() {}, clearFromCursor() {}, clearScreen() {}, setTitle() {}, setProgress() {},
  };
  let tui;
  const copies=[], opens=[], nativeOpens=[], errors=[];
  const adapter = installAdapter({
    version:'unknown-conforming-cache-lifecycle', active:()=>true,
    copy:async text=>{ copies.push(text); }, cwd:()=>'/fixture', open:async href=>{ opens.push(href); },
    button:identity, notify:(text,error)=>{ if(error) errors.push(text); }, requestRender() {},
    quiesce:()=>{ tui.invalidate(); tui.renderNow(true); return tui.previousScreen.every(line=>!line.includes('pi-copy://')); },
  });
  const markdown = new Markdown('audit plain\n\n```make\ntarget:\n\techo 中文  \n```\n\n[WEB](https://example.com/?a=1&amp;b=2)',1,0,theme,undefined,{
    transform:(source,availableWidth)=>adapter.transform(source,{messageType:'assistant',isStreaming:false,availableWidth}),
  });
  tui = new TuiAltScreen(terminal,false,undefined,{openUrl:url=>nativeOpens.push(url),copyOnSelect:false,copySelection:async()=>true});
  const initialCache = tui.renderedOverlayLayouts;
  const point = label => {
    for (const [y,line] of tui.previousScreen.entries()) {
      if (!stripTerminalSequences(line).includes(label)) continue;
      for (let x=0;x<terminal.columns;x++) if(getOsc8LinkAtColumn(line,x)?.startsWith('pi-copy://')) return {x,y};
    }
    assert.fail('missing '+label);
  };
  const send = (p,bits=0,edge='M')=>terminal.input(`\x1b[<${bits};${p.x+1};${p.y+1}${edge}`);
  const click = (p,bits=0)=>{send(p,bits);send(p,bits,'m');};
  try {
    // Full native lifecycle is allowed only in this isolated test process. The
    // startup probe above never starts a terminal or mutates capabilities.
    tui.addChild(markdown); tui.start(); tui.renderNow(true);
    assert.ok(Array.isArray(tui.renderedOverlayLayouts));
    click(point('[复制]')); await new Promise(resolve=>setImmediate(resolve));
    click(point('WEB'),16); await new Promise(resolve=>setImmediate(resolve));
    assert.deepEqual(copies,['target:\n\techo 中文  ']);
    assert.deepEqual(opens,['https://example.com/?a=1&b=2']);
    const y = tui.previousScreen.findIndex(line=>stripTerminalSequences(line).includes('audit plain'));
    send({x:2,y});send({x:6,y},32);send({x:6,y},0,'m');assert.equal(tui.hasActiveSelection(),true);
    terminal.input('\x1b[O');
    const events=[];
    const overlay={render:()=>['overlay'],invalidate(){},handleMouse(event){events.push(event.type);return{handled:true,capture:event.type==='press'};}};
    const handle=tui.showOverlay(overlay,{width:8,height:1,anchor:'top-left'});tui.renderNow(true);
    const layout=tui.renderedOverlayLayouts.find(item=>item.entry.component===overlay);assert.ok(layout);
    click({x:layout.col,y:layout.row});assert.deepEqual(events,['press','release','click']);
    handle.hide();tui.renderNow(true);
    assert.deepEqual(nativeOpens,[]);assert.deepEqual(errors,[]);
    const dispose=adapter.dispose();assert.deepEqual(dispose,{quiesced:true,passive:false});
    console.log(JSON.stringify({kind,imageMode,transformed,check,initialCache:String(initialCache),actualNativeCopies:copies,semanticOpen:opens,selection:true,overlay:events,dispose,capabilitiesUnchangedThroughout:true}));
  } finally { adapter.dispose();tui.stop();hook.deregister(); }
}
