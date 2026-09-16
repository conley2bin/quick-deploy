import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const base=process.cwd();const core=process.env.PI_HOST_ROOT;if(!core)throw new Error('PI_HOST_ROOT is required');
const candidate=process.argv[2];if(!candidate)throw new Error('patched package root is required');
const imp=(p)=>import(pathToFileURL(p).href);
const {HostImageOwnershipAdapter}=await imp(base+'/src/host-adapter.ts');
const {ImageSession}=await imp(base+'/src/session.ts');
const {TerminalImages}=await imp(base+'/src/terminal.ts');
const {AssistantMessageComponent}=await imp(core+'/dist/modes/interactive/components/assistant-message.js');
const {ToolExecutionComponent}=await imp(core+'/dist/modes/interactive/components/tool-execution.js');
const {InteractiveMode}=await imp(core+'/dist/modes/interactive/interactive-mode.js');
const {AgentSession}=await imp(core+'/dist/core/agent-session.js');
const {SessionManager}=await imp(core+'/dist/core/session-manager.js');
const {SettingsManager}=await imp(core+'/dist/core/settings-manager.js');
const {loadExtensions}=await imp(core+'/dist/core/extensions/loader.js');
const {ExtensionRunner}=await imp(core+'/dist/core/extensions/runner.js');
const {createEventBus}=await imp(core+'/dist/core/event-bus.js');
const tui=await imp(core+'/node_modules/@earendil-works/pi-tui/dist/index.js');
const theme=await imp(core+'/dist/modes/interactive/theme/theme.js');
theme.initTheme('dark',false); process.env.TMUX='/fixture/tmux,1000,0';process.env.TERM='tmux-256color';delete process.env.PI_IMAGE_PROTOCOL; const detectedTmux=tui.detectCapabilities(()=>true);tui.setCapabilities(detectedTmux);delete process.env.TMUX;process.env.TERM='xterm-ghostty';tui.setCellDimensions({widthPx:10,heightPx:20});
const png=readFileSync(base+'/test/fixtures/color-block.png').toString('base64');
const assistant=(calls=[],text='')=>({role:'assistant',content:[...(text?[{type:'text',text}]:[]),...calls.map(id=>({type:'toolCall',id,name:'read',arguments:{path:'fixture.png'}}))],api:'fixture',provider:'none',model:'none',usage:{input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}},stopReason:calls.length?'toolUse':'stop',timestamp:0});
const result=(id)=>({role:'toolResult',toolCallId:id,toolName:'read',content:[{type:'text',text:'image'},{type:'image',data:png,mimeType:'image/png'}],isError:false,timestamp:0});
const imageCount=(lines)=>({native:lines.join('\n').split('\x1b_Ga=T,').length-1,customGlyphs:lines.join('\n').split('\u{10EEEE}').length-1,withheld:/Custom bitmap withheld/.test(lines.join(' '))});
const hasNative=(row)=>row.render(80).some(l=>l.includes('\x1b_G'));
const report={};
// A normal pending tool row is bound before it has any result.
{
 const message=assistant(['pending']); const a=new AssistantMessageComponent(message); const row=new ToolExecutionComponent('read','pending',{}, {showImages:true},undefined,{requestRender(){}},'/fixture');
 const terminal=new TerminalImages(()=>1,()=>({widthPx:10,heightPx:20}),{write:()=>true},{},false);
 const adapter=new HostImageOwnershipAdapter(new ImageSession(terminal),()=>{}, {version:'0.85.1',sessionEntryToContextMessages:e=>e.message?[e.message]:[]});
 adapter.setTui({children:[a,row],terminal:{columns:80}});
 const entries=[{type:'message',message}]; adapter.reconcile(entries,entries);
 row.updateResult(result('pending')); const before=hasNative(row);
 entries.push({type:'message',message:result('pending')},{type:'custom',customType:'pi-tmux-images.preview',data:{logicalId:'owned',origin:{key:'tool:pending',blockIndex:1}}});
 adapter.reconcile(entries,entries);const claimed=hasNative(row);adapter.dispose();
 report.pendingToggle={before,claimed,afterDispose:hasNative(row)};
}
// Use the real ExtensionRunner, AgentSession event handler/persistence, and InteractiveMode event/entry paths.
// Only runtime construction/provider discovery and physical terminal painting are replaced by local in-memory doubles.
class EventOnlySession extends AgentSession { _buildRuntime(){} }
const sessionManager=SessionManager.inMemory(base); const settingsManager=SettingsManager.inMemory();
const bus=createEventBus();const coordination=[];bus.on('pi-inline-images:read-preview-coordination',v=>coordination.push(v));
const loaded=await loadExtensions([base+'/index.ts',candidate+'/extensions/index.ts'],base,bus);assert.deepEqual(loaded.errors,[]);
const runner=new ExtensionRunner(loaded.extensions,loaded.runtime,base,sessionManager,{});runner.mode='tui';
const session=new EventOnlySession({agent:{state:{tools:[],messages:[]},subscribe(){return ()=>{};}},sessionManager,settingsManager,cwd:base});
session._extensionRunner=runner;
const mode=Object.create(InteractiveMode.prototype);Object.assign(mode,{runtimeHost:{session},isInitialized:true,pendingTools:new Map(),chatContainer:new tui.Container(),footer:{invalidate(){}},hideThinkingBlock:false,hiddenThinkingLabel:'Thinking...',outputPad:1,toolOutputExpanded:false,getMarkdownThemeWithSettings:()=>theme.getMarkdownTheme(),getMarkdownTransformers:()=>loaded.extensions.flatMap(e=>e.markdownTransformer?[e.markdownTransformer]:[]),getRegisteredToolDefinition:()=>undefined,maybeShowAssistantDiagnostics(){},maybeShowCacheMissNotice(){},updatePendingMessagesDisplay(){}});
let widget;let paints=0;let requests=0;let lines=[];let scheduled=false;
const fakeTui={children:[mode.chatContainer],terminal:{columns:80},invalidate(){for(const child of this.children)child.invalidate();},requestRender(){requests++;if(!scheduled){scheduled=true;setTimeout(()=>{scheduled=false;paint();},1);}}};
const frames=[]; function paint(){paints++;lines=mode.chatContainer.render(80);frames.push(imageCount(lines));widget?.render(80);}
mode.ui=fakeTui;
runner.uiContext={setWidget(_key,factory){widget=factory?.(fakeTui);fakeTui.children=[mode.chatContainer,...(widget?[widget]:[])];},notify(){}};
loaded.runtime.getAllTools=()=>[];
loaded.runtime.appendEntry=(type,data)=>{const id=sessionManager.appendCustomEntry(type,data);void mode.handleEvent({type:'entry_appended',entry:sessionManager.getEntry(id)});};
session.subscribe(event=>mode.handleEvent(event));
const settle=()=>new Promise(r=>setTimeout(r,50));
const event=async(e)=>{await session._handleAgentEvent(e);};
const snapshot=()=>({coordination:coordination.at(-1),images:imageCount(lines),paints,requests});
await runner.emit({type:'session_start',reason:'startup'});paint();await settle();

const sharp=(await imp(base+'/node_modules/sharp/dist/index.cjs')).default;
const formats=process.argv[3]?[process.argv[3]]:['png'];
const history=[];
for(const format of formats){
 const mime='image/'+format;
 const bytes=await sharp({create:{width:100,height:80,channels:4,background:{r:123,g:42,b:200,alpha:0.7}}}).toFormat(format).toBuffer();
 const name='rapid-'+format;const call=assistant([name]);
 await event({type:'message_start',message:assistant()});
 await event({type:'message_update',message:call,assistantMessageEvent:{type:'toolcall_end',contentIndex:0,toolCall:call.content[0],partial:call}});
 await event({type:'message_end',message:call});
 await event({type:'tool_execution_start',toolCallId:name,toolName:'read',args:{path:'fixture.'+format}});
 const res={...result(name),content:[{type:'text',text:'image'},{type:'image',mimeType:mime,data:bytes.toString('base64')}]};
 await event({type:'tool_execution_end',toolCallId:name,toolName:'read',result:res,isError:false});
 await event({type:'message_end',message:res});await settle();
 history.push({format,after50:snapshot()});await new Promise(r=>setTimeout(r,300));history.at(-1).after350=snapshot();
}
// Repeated no-tool assistant completions, no artificial inter-event timers.
for(let i=0;i<3;i++){
 const text='Repeated ordinary assistant text';
 await event({type:'message_start',message:assistant()});
 await event({type:'message_update',message:assistant([],text),assistantMessageEvent:{type:'text_delta',contentIndex:0,delta:text,partial:assistant([],text)}});
 await event({type:'message_end',message:assistant([],text)});
}await settle();
report.rapid={history,final:snapshot(),frames};
settingsManager.setShowImages(false);for(const row of mode.chatContainer.children)if(row instanceof ToolExecutionComponent)row.setShowImages(false);await settle();report.explicitOff={setting:settingsManager.getShowImages(),state:snapshot()};
const beforeCompaction=snapshot();const firstCompactionFrame=frames.length;
const firstKept=sessionManager.buildContextEntries().find(e=>e.type==='message' && e.message.role==='assistant' && e.message.content.some(c=>c.type==='toolCall' && c.id==='rapid-png'));
const compactId=sessionManager.appendCompaction('Fixture summary',firstKept.id,1000);
await runner.emit({type:'session_compact',compactionEntry:sessionManager.getEntry(compactId),fromExtension:true,reason:'manual',willRetry:false});
mode.clearStatusIndicator=()=>{};mode.compactionQueuedMessages=[];
await mode.handleEvent({type:'compaction_end',reason:'manual',result:{summary:'Fixture summary',firstKeptEntryId:firstKept.id,tokensBefore:1000},aborted:false,willRetry:false});await settle();report.compacted={before:beforeCompaction,after:snapshot(),frames:frames.slice(firstCompactionFrame)};

report.afterCompactionPreference=settingsManager.getShowImages();
// Reassert off, then navigate to the retained tool result through actual native branch handling.
for(const row of mode.chatContainer.children)if(row instanceof ToolExecutionComponent)row.setShowImages(false);await settle();
const resultEntry=sessionManager.getBranch().find(e=>e.type==='message' && e.message.role==='toolResult');
await session.navigateTree(resultEntry.id);mode.rebuildChatFromMessages();await settle();report.branchAfterOff={setting:settingsManager.getShowImages(),state:snapshot()};
settingsManager.setShowImages(true);for(const row of mode.chatContainer.children)if(row instanceof ToolExecutionComponent)row.setShowImages(true);await settle();report.branchAfterOn={setting:settingsManager.getShowImages(),state:snapshot()};
await runner.emit({type:'session_shutdown'});await settle();
console.log('PREFERENCE_JSON '+JSON.stringify(report));
