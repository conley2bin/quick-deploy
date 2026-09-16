import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { installGraphicsBridge, IMAGE_BRIDGE_REPLY, IMAGE_BRIDGE_REQUEST, IMAGE_BRIDGE_VERSION, type GraphicsOwnerHandle } from "../src/bridge.ts";
import { TerminalImages } from "../src/terminal.ts";
import type { TransportSink } from "../src/transport.ts";
import type { ViewerState } from "../src/viewers.ts";
class Sink extends EventEmitter implements TransportSink { writes: Buffer[]=[]; write(v: Buffer) { this.writes.push(v); return true; } }
class Bus { h=new Map<string, Set<(x: unknown)=>void>>(); emit(k:string,x:unknown){for(const f of this.h.get(k)??[])f(x)} on(k:string,f:(x:unknown)=>void){const s=this.h.get(k)??new Set();s.add(f);this.h.set(k,s);return()=>s.delete(f)} }
const image=(id:string)=>({source:id,hash:id,width:1,height:1,png:Buffer.from(id)});
const viewer: ViewerState={ready:true,epoch:"v",reason:""};
test("read owner is bounded independently and release never deletes inline", async()=>{
 const sink=new Sink(); let id=1; const terminal=new TerminalImages(()=>id++,()=>({widthPx:1,heightPx:1}),sink,{TERM_PROGRAM:"ghostty"},true,{transportLimits:{minIntervalMs:0}}); terminal.setViewerManaged(true); await terminal.setViewer(viewer);
 const bus=new Bus(); const stop=installGraphicsBridge(bus as never,terminal); let read!:GraphicsOwnerHandle; let inline!:GraphicsOwnerHandle; bus.on(IMAGE_BRIDGE_REPLY,(x)=>{const r=x as {handle:GraphicsOwnerHandle}; if(r.handle.owner==="read")read=r.handle; else inline=r.handle}); bus.emit(IMAGE_BRIDGE_REQUEST,{version:IMAGE_BRIDGE_VERSION,owner:"read",requestId:"r"});bus.emit(IMAGE_BRIDGE_REQUEST,{version:IMAGE_BRIDGE_VERSION,owner:"inline",requestId:"i"});
 await inline.prepare("fixed",image("inline")); for(let n=0;n<16;n++)await read.prepare(String(n),image(`r${n}`)); await assert.rejects(read.prepare("overflow",image("x")),/read image capacity reached/u); await read.release("0"); await read.prepare("16",image("r16")); assert.equal(inline.render("fixed",10).length>0,true); await read.reset(); assert.equal(inline.render("fixed",10).length>0,true); stop(); await terminal.clear(true);
});
