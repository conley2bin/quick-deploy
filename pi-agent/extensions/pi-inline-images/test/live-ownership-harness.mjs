import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const base=process.cwd();
const core=process.env.PI_HOST_ROOT;
if(!core)throw new Error('PI_HOST_ROOT is required');
const candidate=process.argv[2];
if(!candidate)throw new Error('usage: live-ownership-harness.mjs PATCHED_PACKAGE_ROOT');
delete process.env.TMUX;delete process.env.TMUX_PANE;process.env.TERM='xterm-ghostty';process.env.TERM_PROGRAM='ghostty';
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
theme.initTheme('dark',false); tui.setCapabilities({images:'kitty',trueColor:true,hyperlinks:true});tui.setCellDimensions({widthPx:10,heightPx:20});
const png=readFileSync(base+'/test/fixtures/color-block.png').toString('base64');
const assistant=(calls=[],text='')=>({role:'assistant',content:[...(text?[{type:'text',text}]:[]),...calls.map(id=>({type:'toolCall',id,name:'read',arguments:{path:'fixture.png'}}))],api:'fixture',provider:'none',model:'none',usage:{input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}},stopReason:calls.length?'toolUse':'stop',timestamp:0});
const result=(id)=>({role:'toolResult',toolCallId:id,toolName:'read',content:[{type:'text',text:'image'},{type:'image',data:png,mimeType:'image/png'}],isError:false,timestamp:0});
const imageCount=(lines)=>{const text=lines.join('\n');return{native:text.split('\x1b_Ga=T,').length-1,customGlyphs:text.split('\u{10EEEE}').length-1,withheld:/Custom bitmap withheld/.test(text),waiting:/waiting for a compatible visible viewer/.test(text),unavailable:/Image is unavailable|bridge unavailable/.test(text),notices:lines.filter(line=>line.includes('[image]')).map(line=>line.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g,''))}};
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
 report.pendingToggle={before,claimed,afterDispose:hasNative(row)};assert.deepEqual(report.pendingToggle,{before:true,claimed:false,afterDispose:true});
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
function paint(){paints++;lines=mode.chatContainer.render(80);widget?.render(80);}
mode.ui=fakeTui;
runner.uiContext={setWidget(_key,factory){widget=factory?.(fakeTui);fakeTui.children=[mode.chatContainer,...(widget?[widget]:[])];},notify(){}};
loaded.runtime.getAllTools=()=>[];
loaded.runtime.appendEntry=(type,data)=>{const id=sessionManager.appendCustomEntry(type,data);void mode.handleEvent({type:'entry_appended',entry:sessionManager.getEntry(id)});};
session.subscribe(event=>mode.handleEvent(event));
const settle=()=>new Promise(r=>setTimeout(r,50));
const event=async(e)=>{await session._handleAgentEvent(e);await settle();};
const snapshot=()=>({coordination:coordination.at(-1),images:imageCount(lines),paints,requests});
await runner.emit({type:'session_start',reason:'startup'});paint();await settle();
// First streamed read: the tree is already observed before message_end persists its assistant/tool result.
const call=assistant(['live-one']);
await event({type:'message_start',message:assistant()});
await event({type:'message_update',message:call,assistantMessageEvent:{type:'toolcall_end',contentIndex:0,toolCall:call.content[0],partial:call}});
await event({type:'message_end',message:call});report.liveAfterAssistant=snapshot();
await event({type:'tool_execution_start',toolCallId:'live-one',toolName:'read',args:{path:'fixture.png'}});
const res=result('live-one');await event({type:'tool_execution_end',toolCallId:'live-one',toolName:'read',result:res,isError:false});
await event({type:'message_end',message:res});report.liveReadFinal=snapshot();
// Force the same supported restore lifecycle as loading history, without a provider or model call.
mode.chatContainer.clear();mode.renderSessionEntries(sessionManager.buildContextEntries());
await runner.emit({type:'session_tree'});paint();await settle();report.restoredRead=snapshot();
const stableStart={paints,requests};await new Promise(r=>setTimeout(r,3100));report.stablePolling={before:stableStart,after:{paints,requests}};
for(const row of mode.chatContainer.children)if(row instanceof ToolExecutionComponent)row.setShowImages(false);
fakeTui.invalidate();paint();await settle();report.externalImagesOff=snapshot();
for(const row of mode.chatContainer.children)if(row instanceof ToolExecutionComponent)row.setShowImages(true);
fakeTui.invalidate();paint();await settle();report.externalImagesOn=snapshot();
// The next ordinary assistant response exists in the UI before it is persisted.
await event({type:'message_start',message:assistant([], 'Next response')});report.nextStreaming=snapshot();
await event({type:'message_update',message:assistant([], 'Next response grows'),assistantMessageEvent:{type:'text_delta',contentIndex:0,delta:' grows',partial:assistant([], 'Next response grows')}});report.nextStreamingUpdate=snapshot();
await event({type:'message_end',message:assistant([], 'Next response grows')});report.nextFinished=snapshot();
const user={role:'user',content:[{type:'text',text:'Attachment here'},{type:'image',mimeType:'image/png',data:png}],timestamp:0};
const rawUser=JSON.stringify(user);await event({type:'message_start',message:user});await event({type:'message_end',message:user});
const userPreview=sessionManager.getBranch().filter(e=>e.type==='custom' && e.customType==='pi-tmux-images.preview').at(-1);
const userRenderer=runner.getEntryRenderer('pi-tmux-images.preview');
report.userAttachment={origin:userPreview.data.origin,images:imageCount(userRenderer(userPreview,{expanded:false},theme.theme).render(80)),unchanged:JSON.stringify(user)===rawUser};
const callTwo=assistant(['live-two']);await event({type:'message_start',message:assistant()});await event({type:'message_update',message:callTwo,assistantMessageEvent:{type:'toolcall_end',contentIndex:0,toolCall:callTwo.content[0],partial:callTwo}});await event({type:'message_end',message:callTwo});await event({type:'tool_execution_start',toolCallId:'live-two',toolName:'read',args:{path:'fixture.png'}});const resTwo=result('live-two');await event({type:'tool_execution_end',toolCallId:'live-two',toolName:'read',result:resTwo,isError:false});await event({type:'message_end',message:resTwo});
mode.chatContainer.clear();mode.renderSessionEntries(sessionManager.buildContextEntries());await runner.emit({type:'session_tree'});paint();await settle();report.twoIdenticalRestored=snapshot();
await runner.emit({type:'session_shutdown'});await settle();
report.componentDispose={assistantWrappers:mode.chatContainer.children.filter(c=>c instanceof AssistantMessageComponent && c.render!==AssistantMessageComponent.prototype.render).length,toolWrappers:mode.chatContainer.children.filter(c=>c instanceof ToolExecutionComponent && c.setShowImages!==ToolExecutionComponent.prototype.setShowImages).length,widgetRemoved:!widget};
console.log('LIVE_JSON '+JSON.stringify(report));
