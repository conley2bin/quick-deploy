import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { installGraphicsBridge, IMAGE_BRIDGE_REPLY, IMAGE_BRIDGE_REQUEST, IMAGE_BRIDGE_VERSION, type GraphicsOwnerHandle } from "../src/bridge.ts";
import { TerminalImages } from "../src/terminal.ts";
import type { TransportSink } from "../src/transport.ts";
import type { ViewerState } from "../src/viewers.ts";
class Sink extends EventEmitter implements TransportSink {
 writes: Buffer[]=[]; returns: boolean[]=[]; writableNeedDrain=false;
 write(v: Buffer) { this.writes.push(v); const accepted=this.returns.shift()??true; if(!accepted)this.writableNeedDrain=true; return accepted; }
 drain(){this.writableNeedDrain=false;this.emit("drain")}
}
class Bus { h=new Map<string, Set<(x: unknown)=>void>>(); emit(k:string,x:unknown){for(const f of this.h.get(k)??[])f(x)} on(k:string,f:(x:unknown)=>void){const s=this.h.get(k)??new Set();s.add(f);this.h.set(k,s);return()=>s.delete(f)} }
const image=(id:string)=>({source:id,hash:id,width:1,height:1,png:Buffer.from(id)});
const viewer: ViewerState={ready:true,epoch:"v",reason:"",attached:["v"],receivers:["v"]};
test("read owner is bounded independently and release never deletes inline", async()=>{
 const sink=new Sink(); let id=1; const terminal=new TerminalImages(()=>id++,()=>({widthPx:1,heightPx:1}),sink,{TERM_PROGRAM:"ghostty"},true,{transportLimits:{minIntervalMs:0}}); terminal.setViewerManaged(true); await terminal.setViewer(viewer);
 const bus=new Bus(); const stop=installGraphicsBridge(bus as never,terminal); let read!:GraphicsOwnerHandle; let inline!:GraphicsOwnerHandle; bus.on(IMAGE_BRIDGE_REPLY,(x)=>{const r=x as {handle:GraphicsOwnerHandle}; if(r.handle.owner==="read")read=r.handle; else inline=r.handle}); bus.emit(IMAGE_BRIDGE_REQUEST,{version:IMAGE_BRIDGE_VERSION,owner:"read",requestId:"r"});bus.emit(IMAGE_BRIDGE_REQUEST,{version:IMAGE_BRIDGE_VERSION,owner:"inline",requestId:"i"});
 await inline.prepare("fixed",image("inline")); for(let n=0;n<16;n++)await read.prepare(String(n),image(`r${n}`)); await assert.rejects(read.prepare("overflow",image("x")),/read image capacity reached/u); await read.release("0"); await read.prepare("16",image("r16")); assert.equal(inline.render("fixed",10).length>0,true); await read.reset(); assert.equal(inline.render("fixed",10).length>0,true); assert.equal(terminal.residentBytes("read"),0); assert.ok(terminal.residentBytes("inline")>0); stop(); await terminal.clear(true);
});

test("inline and read enforce independent resident byte quotas", async()=>{
 const sink=new Sink(); let id=50; const terminal=new TerminalImages(()=>id++,()=>({widthPx:1,heightPx:1}),sink,{TERM_PROGRAM:"ghostty"},true,{maxResidentPngBytes:5,maxReadResidentPngBytes:5,transportLimits:{minIntervalMs:0}}); terminal.setViewerManaged(true); await terminal.setViewer(viewer);
 await terminal.prepare("inline:one",image("1234")); await terminal.prepare("read:one",image("abcd"));
 assert.equal(terminal.residentBytes("inline"),4); assert.equal(terminal.residentBytes("read"),4);
 await assert.rejects(terminal.prepare("inline:two",image("xx")),/resident PNG budget reached \(5 bytes\)/u);
 await assert.rejects(terminal.prepare("read:two",image("yy")),/read resident PNG budget reached \(5 bytes\)/u);
 await terminal.clear(true);
});

test("owner reset marks every resource before awaiting drain so recovery cannot upload later resources", async()=>{
 const sink=new Sink(); sink.returns.push(false); let id=80; const terminal=new TerminalImages(()=>id++,()=>({widthPx:1,heightPx:1}),sink,{TERM_PROGRAM:"ghostty"},true,{transportLimits:{minIntervalMs:0,drainTimeoutMs:1_000}}); terminal.setViewerManaged(true);
 const bus=new Bus(); const stop=installGraphicsBridge(bus as never,terminal); let read!:GraphicsOwnerHandle; bus.on(IMAGE_BRIDGE_REPLY,(x)=>{const r=x as {handle:GraphicsOwnerHandle}; if(r.handle.owner==="read")read=r.handle}); bus.emit(IMAGE_BRIDGE_REQUEST,{version:IMAGE_BRIDGE_VERSION,owner:"read",requestId:"r"});
 for(let n=0;n<3;n++)await read.prepare(String(n),image(`reset-${n}`));
 const viewing=terminal.setViewer(viewer); for(let turn=0;turn<8&&sink.writes.length<1;turn++)await Promise.resolve(); assert.equal(sink.writes.length,1);
 const resetting=read.reset(); sink.drain(); await Promise.all([viewing,resetting]);
 const uploads=sink.writes.filter((value)=>value.includes(Buffer.from("a=t,"))).length;
 const deletes=sink.writes.filter((value)=>value.includes(Buffer.from("a=d,d=I"))).length;
 assert.equal(uploads,1,"later owner resources cannot upload after reset is requested");
 assert.equal(deletes,1,"only the already transmitted terminal resource requires deletion");
 assert.equal(terminal.count("read"),0); stop(); await terminal.clear(true);
});

test("read reset cancels only read work behind inline backpressure", async()=>{
 const sink=new Sink(); sink.returns.push(false); let id=100; const terminal=new TerminalImages(()=>id++,()=>({widthPx:1,heightPx:1}),sink,{TERM_PROGRAM:"ghostty"},true,{transportLimits:{minIntervalMs:0,drainTimeoutMs:1_000}}); terminal.setViewerManaged(true); await terminal.setViewer(viewer);
 const bus=new Bus(); const stop=installGraphicsBridge(bus as never,terminal); let read!:GraphicsOwnerHandle; let inline!:GraphicsOwnerHandle; bus.on(IMAGE_BRIDGE_REPLY,(x)=>{const r=x as {handle:GraphicsOwnerHandle}; if(r.handle.owner==="read")read=r.handle; else inline=r.handle}); bus.emit(IMAGE_BRIDGE_REQUEST,{version:IMAGE_BRIDGE_VERSION,owner:"read",requestId:"r"});bus.emit(IMAGE_BRIDGE_REQUEST,{version:IMAGE_BRIDGE_VERSION,owner:"inline",requestId:"i"});
 const inlinePreparing=inline.prepare("blocked",image("inline-blocked")); for(let turn=0;turn<4;turn++)await Promise.resolve(); assert.equal(sink.writes.length,1);
 const readPreparing=read.prepare("queued",image("read-queued")); for(let turn=0;turn<4;turn++)await Promise.resolve(); assert.equal(terminal.pendingJobs(),1);
 await read.reset(); await readPreparing; assert.equal(terminal.count("read"),0); assert.equal(terminal.count("inline"),1); assert.equal(sink.writes.length,1,"read reset cannot bypass or erase inline drain debt");
 sink.drain(); await inlinePreparing; assert.ok(inline.render("blocked",10).length>0); stop(); await terminal.clear(true);
});
