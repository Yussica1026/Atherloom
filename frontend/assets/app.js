const $ = (s) => document.querySelector(s);
const state = { providers: [], personas: [], worldbooks: [], mcp_servers: [], mcp_audit: [], conversations: [], memories: [], journals: [], board_messages: [], dreams: [], favorites: [], attachments: [], version_selection: {}, settings: { auto_title_mode: "local", tool_permissions: {} }, current: null, provider: null, persona: null, messages: [], generating: new Set(), generation_controllers: new Map(), message_cache: new Map(), navigation: 0 };
const typingSession={startedAt:0,lastAt:0,keystrokes:0,hadText:false};
function typingAbandonedKey(){return `atherloom:typing-abandoned:${state.current||"new"}`;}
function updateTypingPresence(){const target=$("#typingPresence"),enabled=state.settings.typing_presence_enabled!==false;if(!target)return;if(currentBusy()){target.textContent=`${activePersonaName()} 正在输入…`;target.hidden=false;return;}if(enabled&&$("#prompt")?.value.trim()){target.textContent=`正在向 ${activePersonaName()} 共享输入状态（不会发送未完成正文）`;target.hidden=false;return;}target.hidden=true;}
function consumeTypingContext(){if(state.settings.typing_presence_enabled===false)return "";const now=Date.now(),duration=typingSession.startedAt?Math.max(1,Math.round((now-typingSession.startedAt)/1000)):0,paused=typingSession.lastAt?Math.max(0,Math.round((now-typingSession.lastAt)/1000)):0,keystrokes=typingSession.keystrokes,abandoned=localStorage.getItem(typingAbandonedKey())==="1";localStorage.removeItem(typingAbandonedKey());typingSession.startedAt=typingSession.lastAt=typingSession.keystrokes=0;typingSession.hadText=false;return `用户输入约 ${duration} 秒，键盘活动 ${keystrokes} 次，发送前停顿约 ${paused} 秒${abandoned?"；此前曾输入后清空一次":""}。`;}
const gameState = { catalog: [], current: null, fishing: null, claw: null, slots: null, starMerge: null, maze: null, dungeon: null, starMergeMode: "self", waters: {} };
const gameRoomPending = [];
const ddzRanks=["3","4","5","6","7","8","9","10","J","Q","K","A","2"],ddzSuits=["♠","♥","♣","♦"];
function freshCardRoom(){const deck=ddzSuits.flatMap(suit=>ddzRanks.map(rank=>`${suit}${rank}`)).concat(["小王","大王"]);for(let i=deck.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[deck[i],deck[j]]=[deck[j],deck[i]];}return {mode:"poker",deck,hand:deck.slice(0,17),bottom:deck.slice(51),played:[],turn:0,landlord:null};}
const cardRoomState=freshCardRoom();
const roleplayState = { stories: [], current: null, busy: false, phase: "", backstageEpoch: 0 };
const roleplayPresets={ancient:"古风。请设定朝代氛围、门阀或江湖关系、礼法约束与一件尚未兑现的旧约。",modern:"现代都市。请设定真实生活场景、人物既有关系与一次意外重逢。",mystery:"悬疑。请设定封闭或受限场景、一条可验证线索、隐藏动机与正在逼近的期限。",fantasy:"幻想世界。请设定独特规则、旅途目标、代价与角色命运的交点。",custom:""};
function dismissLaunchScreen(){const screen=$("#launchScreen");if(!screen||screen.classList.contains("dismissed"))return;screen.classList.add("dismissed");setTimeout(()=>screen.remove(),320);}
if($("#launchScreen")){const refresh=document.documentElement.dataset.launchMode==="refresh",delay=matchMedia("(prefers-reduced-motion: reduce)").matches?180:refresh?430:1250;$("#launchScreen").onclick=dismissLaunchScreen;setTimeout(dismissLaunchScreen,delay);}

async function api(path, options = {}) {
  const { timeout, ...fetchOptions } = options;
  const controller = timeout ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeout) : null;
  let response;
  try { response = await fetch(path, { headers: { "Content-Type": "application/json", ...(fetchOptions.headers || {}) }, ...fetchOptions, signal: fetchOptions.signal || controller?.signal }); }
  catch (error) { if (error.name === "AbortError") throw new Error("请求超时：上游在规定时间内没有响应，请检查服务状态、模型名称与网络后重试"); throw new Error(`网络请求未发出：${error.message||"浏览器无法访问本地服务或上游接口"}`); }
  finally { if (timer) clearTimeout(timer); }
  if (!response.ok) { const detail=(await response.json().catch(() => ({}))).detail; throw new Error(formatHttpError(response.status,detail)); }
  return response.json();
}

function escapeHtml(value) { const div = document.createElement("div"); div.textContent = value; return div.innerHTML; }
function renderMarkdown(value) {
  const codeBlocks=[];
  let text=String(value||"").replace(/```(?:[\w-]+)?\n?([\s\S]*?)```/g,(_,code)=>`\u0000BLOCK${codeBlocks.push(`<pre><code>${escapeHtml(code.replace(/\n$/, ""))}</code></pre>`)-1}\u0000`);
  text=escapeHtml(text).replace(/`([^`\n]+)`/g,"<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g,"<strong>$1</strong>").replace(/__([^_\n]+)__/g,"<strong>$1</strong>").replace(/~~([^~\n]+)~~/g,"<del>$1</del>").replace(/(^|[^*])\*([^*\n]+)\*/g,"$1<em>$2</em>").replace(/(^|[^_])_([^_\n]+)_/g,"$1<em>$2</em>");
  const lines=text.split("\n"),html=[];let list=null;
  const closeList=()=>{if(list){html.push(`</${list}>`);list=null;}};
  for(const line of lines){const block=line.match(/^\u0000BLOCK(\d+)\u0000$/);if(block){closeList();html.push(codeBlocks[Number(block[1])]);continue;}const heading=line.match(/^(#{1,4})\s+(.+)$/);if(heading){closeList();const level=heading[1].length;html.push(`<h${level}>${heading[2]}</h${level}>`);continue;}const item=line.match(/^\s*([-*+] |\d+\. )(.+)$/);if(item){const type=/\d/.test(item[1])?"ol":"ul";if(list!==type){closeList();list=type;html.push(`<${type}>`);}html.push(`<li>${item[2]}</li>`);continue;}closeList();if(/^\s*---+\s*$/.test(line)){html.push("<hr>");continue;}if(line.startsWith("&gt; ")){html.push(`<blockquote>${line.slice(5)}</blockquote>`);continue;}if(line.trim())html.push(`<p>${line}</p>`);else html.push("");}closeList();return html.join("\n");
}
function activeProvider() { const conversation=state.conversations.find(item=>item.id===state.current),boundId=conversation?.provider_id||activePersona()?.config?.provider_id||state.provider;return state.providers.find(p=>p.id===boundId&&p.enabled!==false&&p.enabled!==0); }
function activePersona() { const conversation=state.conversations.find(item=>item.id===state.current),personaId=state.persona||conversation?.persona_id;return state.personas.find(p => p.id === personaId); }
function activePersonaName() { return activePersona()?.name?.trim() || "当前人格"; }
function memoryPersonaKey(){return state.persona||"__default__";}
function memoryListKey(){return $("#memoryOwnerFilter")?.value==="__unassigned__"?"__unassigned__":memoryPersonaKey();}
function currentBusy(){return !!state.current&&state.generating.has(state.current);}
function generationDot(conversationId){return state.generating.has(conversationId)?`<i class="generation-dot" aria-label="正在生成"></i>`:"";}
function renderCurrentTitle(){const conversation=state.conversations.find(item=>item.id===state.current),title=conversation?.title||"新对话";$("#titleButton").innerHTML=`<span>${escapeHtml(title)}</span>${generationDot(state.current)}<span aria-hidden="true">⌄</span>`;}
function draftKey(conversationId){return `atherloom:draft:${conversationId}`;}
function saveCurrentDraft(){if(!state.current)return;const value=$("#prompt").value;if(value)localStorage.setItem(draftKey(state.current),value);else localStorage.removeItem(draftKey(state.current));}
function updateComposerState(){const button=$("#send"),busy=currentBusy();button.disabled=!busy&&!$("#prompt").value.trim()&&!state.attachments.length;button.classList.toggle("stop",busy);button.textContent=busy?"■":"↑";button.setAttribute("aria-label",busy?"停止生成":"发送");button.title=busy?"停止生成":"发送";updateTypingPresence();}
function restoreCurrentDraft(){const input=$("#prompt"),value=state.current?localStorage.getItem(draftKey(state.current))||"":"";input.value=value;input.style.height="auto";input.style.height=value?`${Math.min(input.scrollHeight,180)}px`:"auto";updateComposerState();renderContextUsage();}
function stopCurrentGeneration(){state.generation_controllers.get(state.current)?.abort();}
function worldbookSelectionKey(conversationId){return `atherloom:worldbooks:${conversationId}`;}
function selectedWorldbookIds(){try{return state.current?JSON.parse(localStorage.getItem(worldbookSelectionKey(state.current))||"[]"):[];}catch{return [];}}
function saveSelectedWorldbooks(ids){if(!state.current)return;localStorage.setItem(worldbookSelectionKey(state.current),JSON.stringify(ids));renderInjectionTray();}
function renderInjectionTray(){const tray=$("#injectionTray"),selected=selectedWorldbookIds().map(id=>state.worldbooks.find(book=>book.id===id)).filter(Boolean);tray.hidden=!selected.length;tray.innerHTML=selected.map(book=>`<span>${escapeHtml(book.name)}<button type="button" data-remove-worldbook="${book.id}" aria-label="取消注入 ${escapeHtml(book.name)}">×</button></span>`).join("");tray.querySelectorAll("[data-remove-worldbook]").forEach(button=>button.onclick=()=>saveSelectedWorldbooks(selectedWorldbookIds().filter(id=>id!==button.dataset.removeWorldbook)));}
function localTimeContext(now = new Date()) { return now.toLocaleString("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "long", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZoneName: "short" }); }
function renderTimeGreeting(now = new Date()) {
  const hour = now.getHours();
  const name = state.settings.display_name?.trim();
  const address = name ? `，${name}` : "";
  const greeting = hour < 5 ? `夜深了${address}，想聊些什么？` : hour < 11 ? `早上好${address}，今天想聊些什么？` : hour < 14 ? `中午好${address}，想聊些什么？` : hour < 18 ? `下午好${address}，想聊些什么？` : hour < 23 ? `晚上好${address}，想聊些什么？` : `夜深了${address}，想聊些什么？`;
  if ($("#welcomeTitle")) $("#welcomeTitle").textContent = greeting;
  return greeting;
}

function showFetchedModels(models, form = $("#providerForm")) {
  const select = $("#providerModelSelect");
  const current = form.elements.model.value;
  select.innerHTML = `<option value="">选择已拉取的模型（${models.length}）</option>` + models.map(model => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`).join("");
  select.hidden = models.length === 0;
  select.value = models.includes(current) ? current : "";
  return select;
}

let bookObjectUrl,currentBook=null;
const bookLocalKey=kind=>currentBook?`${currentBook.key}:${kind}`:"";
const readBookLocal=kind=>{try{return JSON.parse(localStorage.getItem(bookLocalKey(kind))||"[]");}catch{return [];}};
const writeBookLocal=(kind,value)=>localStorage.setItem(bookLocalKey(kind),JSON.stringify(value));
function setBookControls(enabled){
  for(const id of ["addBookmark","addAnnotation","askBookAi"])$(id.startsWith("#")?id:`#${id}`).disabled=!enabled;
}
function currentBookPosition(){
  const reader=$("#bookReader"),pre=reader.querySelector("pre"),maximum=Math.max(1,reader.scrollHeight-reader.clientHeight),ratio=Math.max(0,Math.min(1,reader.scrollTop/maximum)),offset=Math.round((currentBook?.text?.length||0)*ratio);
  return {ratio,offset,excerpt:pre?.textContent.slice(Math.max(0,offset-100),offset+220).trim()||""};
}
function selectedBookText(){
  const selection=getSelection(),pre=$("#bookReader pre");
  if(!selection||selection.isCollapsed||!pre||!pre.contains(selection.anchorNode)||!pre.contains(selection.focusNode))return null;
  const quote=selection.toString().trim().slice(0,2000);if(!quote)return null;
  const range=document.createRange();range.selectNodeContents(pre);range.setEnd(selection.anchorNode,selection.anchorOffset);
  return {quote,offset:range.toString().length};
}
function showReadingTab(tab){
  document.querySelectorAll("[data-reading-tab]").forEach(button=>button.classList.toggle("active",button.dataset.readingTab===tab));
  $("#bookmarksPane").hidden=tab!=="bookmarks";$("#annotationsPane").hidden=tab!=="annotations";$("#bookAiForm").hidden=tab!=="ai";
}
function jumpToBookOffset(offset){
  if(!currentBook?.text)return;const reader=$("#bookReader"),maximum=Math.max(0,reader.scrollHeight-reader.clientHeight);reader.scrollTop=maximum*Math.max(0,Math.min(1,Number(offset||0)/Math.max(1,currentBook.text.length)));
}
function renderBookNotes(){
  const bookmarks=currentBook?readBookLocal("bookmarks"):[],annotations=currentBook?readBookLocal("annotations"):[];
  $("#bookmarkList").innerHTML=bookmarks.map(item=>`<article class="reading-note"><blockquote>${escapeHtml(item.excerpt||"此处书签")}</blockquote><small>${new Date(item.created_at).toLocaleString()}</small><div class="reading-note-actions"><button type="button" data-bookmark-go="${item.id}">跳转</button><button type="button" data-bookmark-delete="${item.id}">删除</button></div></article>`).join("")||`<p class="muted">还没有书签。</p>`;
  $("#annotationList").innerHTML=annotations.map(item=>`<article class="reading-note"><blockquote>“${escapeHtml(item.quote)}”</blockquote>${item.note?`<p>${escapeHtml(item.note)}</p>`:""}<small>${new Date(item.created_at).toLocaleString()}</small><div class="reading-note-actions"><button type="button" data-annotation-go="${item.id}">跳转</button><button type="button" data-annotation-delete="${item.id}">删除</button></div></article>`).join("")||`<p class="muted">选择一段文字后添加批注。</p>`;
  document.querySelectorAll("[data-bookmark-go]").forEach(button=>button.onclick=()=>jumpToBookOffset(bookmarks.find(item=>item.id===button.dataset.bookmarkGo)?.offset));
  document.querySelectorAll("[data-annotation-go]").forEach(button=>button.onclick=()=>jumpToBookOffset(annotations.find(item=>item.id===button.dataset.annotationGo)?.offset));
  document.querySelectorAll("[data-bookmark-delete]").forEach(button=>button.onclick=()=>{writeBookLocal("bookmarks",bookmarks.filter(item=>item.id!==button.dataset.bookmarkDelete));renderBookNotes();});
  document.querySelectorAll("[data-annotation-delete]").forEach(button=>button.onclick=()=>{writeBookLocal("annotations",annotations.filter(item=>item.id!==button.dataset.annotationDelete));renderBookNotes();});
}
const bookEncodingLabels={utf8:"UTF-8",gb18030:"GB18030 / GBK",big5:"Big5",utf16le:"UTF-16 LE",utf16be:"UTF-16 BE"};
function bookTextScore(text){const replacements=(text.match(/\uFFFD/g)||[]).length,controls=(text.match(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g)||[]).length,mojibake=(text.match(/[ÃÂåäæçéèïð]/g)||[]).length+(text.match(/(?:锟斤拷|烫烫烫|屯屯屯)/g)||[]).length*8,readable=(text.match(/[\u3400-\u9FFF，。！？；：“”‘’（）《》]/g)||[]).length;return replacements*100+controls*25+mojibake*4-Math.min(readable,500)*.02;}
function decodeBookBytes(buffer){const bytes=new Uint8Array(buffer),bom=bytes.length>=2?(bytes[0]<<8)|bytes[1]:0,preferred=bom===0xFFFE?["utf-16le"]:bom===0xFEFF?["utf-16be"]:bytes[0]===0xEF&&bytes[1]===0xBB&&bytes[2]===0xBF?["utf-8"]:((bytes.filter((value,index)=>index%2===0&&value===0).length>bytes.length/8)?["utf-16be"]:(bytes.filter((value,index)=>index%2===1&&value===0).length>bytes.length/8)?["utf-16le"]:[]);try{if(!preferred.length||preferred[0]==="utf-8"){const text=new TextDecoder("utf-8",{fatal:true}).decode(bytes).replace(/^\uFEFF/,"");return {text,encoding:"utf-8",score:bookTextScore(text)};}}catch{}const candidates=[...new Set([...preferred,"gb18030","big5","utf-8"])],decoded=[];for(const encoding of candidates){try{const text=new TextDecoder(encoding,{fatal:encoding==="utf-8"}).decode(bytes).replace(/^\uFEFF/,"");decoded.push({text,encoding,score:bookTextScore(text)-(preferred[0]===encoding?20:0)});}catch{}}if(!decoded.length)throw new Error("无法识别这本书的文字编码");return decoded.sort((a,b)=>a.score-b.score)[0];}
function detectBookFormat(buffer,file){const bytes=new Uint8Array(buffer),head=new TextDecoder("latin1").decode(bytes.slice(0,80)),name=String(file?.name||"");if(head.startsWith("%PDF-")||file?.type==="application/pdf"||/\.pdf$/i.test(name))return "pdf";if(bytes[0]===0x50&&bytes[1]===0x4B&&bytes[2]===0x03&&bytes[3]===0x04)return "zip";if(head.includes("BOOKMOBI")||/\.(mobi|azw3?)$/i.test(name))return "mobi";return "text";}
function validateBookText(text){const sample=String(text||"").slice(0,12000),visible=sample.match(/[^\s\u0000-\u001F]/g)?.length||0,letters=sample.match(/[\p{L}\p{Script=Han}]/gu)?.length||0,numbers=sample.match(/\d/g)?.length||0;if(!sample.trim())throw new Error("文件里没有可读取的正文");if(bookTextScore(sample)>Math.max(80,sample.length*.08)||visible>200&&letters/visible<.08&&numbers/visible>.45)throw new Error("检测到的不是正常正文，可能是 PDF/EPUB/MOBI 等电子书内部数据；请确认文件格式，或先导出为 TXT/Markdown");}
async function openLocalBook(file) {
  if (!file) return;
  const reader = $("#bookReader");
  const status = $("#bookStatus");
  const key = `atherloom:book:${file.name}:${file.size}`;
  currentBook=null;setBookControls(false);renderBookNotes();
  status.textContent = `${file.name} · 正在打开…`;
  await new Promise(resolve => requestAnimationFrame(resolve));
  try {
    const limit = 2 * 1024 * 1024;
    const buffer=await file.slice(0,limit).arrayBuffer(),format=detectBookFormat(buffer,file),isPdf=format==="pdf";
    if (bookObjectUrl) { URL.revokeObjectURL(bookObjectUrl); bookObjectUrl = undefined; }
    reader.onscroll = null;
    if(format==="zip"||format==="mobi")throw new Error(format==="zip"?"检测到 EPUB/ZIP 电子书；当前版本不能直接读取，请先导出为 TXT/Markdown":"检测到 MOBI/AZW 电子书；当前版本不能直接读取，请先导出为 TXT/Markdown");
    if (isPdf) {
      bookObjectUrl = URL.createObjectURL(file);
      reader.innerHTML = `<iframe title="${escapeHtml(file.name)}" src="${bookObjectUrl}#page=${Number(localStorage.getItem(key) || 1)}"></iframe>`;
      status.textContent = `${file.name} · 本地 PDF；书签、批注与 AI 共读暂支持 TXT/Markdown`;
      return;
    }
    const decoded = decodeBookBytes(buffer);
    const text = decoded.text;
    validateBookText(text);
    const pre = document.createElement("pre");
    pre.textContent = text;
    reader.replaceChildren(pre);
    currentBook={title:file.name,key,text};loadBookAiChat();
    setBookControls(true);renderBookNotes();
    reader.scrollTop = Number(localStorage.getItem(key) || 0);
    reader.onscroll = () => localStorage.setItem(key, String(reader.scrollTop));
    status.textContent = file.size > limit ? `${file.name} · 已打开前 2 MB，避免设备卡顿` : `${file.name} · 本地文件`;
  } catch (error) {
    reader.innerHTML = `<div class="game-empty"><span>!</span><h3>这本书没有打开</h3><p>${escapeHtml(error.message || "无法读取本地文件")}</p></div>`;
    status.textContent = `${file.name} · 打开失败`;
  }
}

function renderHistory() {
  const group = (label, items) => items.length ? `<div class="history-group"><div class="history-label">${label}</div>${items.map(c => `<div class="history-row ${c.id === state.current ? "active" : ""}"><button class="history-item" data-id="${c.id}"><span>${escapeHtml(c.title)}</span>${generationDot(c.id)}</button><div class="history-actions"><button data-history-action="star" data-id="${c.id}" title="星标">${c.starred ? "★" : "☆"}</button><button data-history-action="pin" data-id="${c.id}" title="置顶">${c.pinned ? "●" : "○"}</button><button data-history-action="archive" data-id="${c.id}" title="${c.archived ? "取消归档" : "归档"}">⌑</button><button data-history-action="delete" data-id="${c.id}" title="删除">×</button></div></div>`).join("")}</div>` : "";
  const scoped=state.conversations.filter(c=>(c.persona_id||null)===(state.persona||null));
  const active = scoped.filter(c => !c.archived);
  const pinned = active.filter(c => c.pinned);
  const starred = active.filter(c => c.starred && !c.pinned);
  const recent = active.filter(c => !c.pinned && !c.starred);
  const archived = scoped.filter(c => c.archived);
  $("#history").innerHTML = group("置顶", pinned) + group("星标", starred) + group("最近", recent) + group("已归档", archived) || `<p class="muted" style="padding:8px 11px">还没有对话</p>`;
  document.querySelectorAll(".history-item").forEach(button => button.onclick = () => { setSidebar(false); openConversation(button.dataset.id); });
  document.querySelectorAll("[data-history-action]").forEach(button => button.onclick = event => {event.stopPropagation();updateHistoryState(button.dataset.id, button.dataset.historyAction).catch(error=>alert(`对话操作失败：${error.message}`));});
  renderSidebarPersonas();
}

function sortedPersonas(){return [...state.personas].sort((a,b)=>Number(!!b.config?.pinned)-Number(!!a.config?.pinned)||a.name.localeCompare(b.name,"zh-CN"));}
function renderSidebarPersonas(){
  const list=$("#sidebarPersonas");if(!list)return;
  list.innerHTML=sortedPersonas().map(persona=>`<div class="sidebar-persona ${persona.id===state.persona?"active":""}" data-select-persona="${persona.id}"><span class="sidebar-persona-avatar">${escapeHtml([...persona.name][0]||"人")}</span><span class="sidebar-persona-name">${escapeHtml(persona.name)}${persona.config?.pinned?`<span class="sidebar-persona-pin">置顶</span>`:""}</span><button class="sidebar-persona-edit" data-sidebar-edit-persona="${persona.id}" type="button" aria-label="编辑 ${escapeHtml(persona.name)}">✎</button></div>`).join("")||`<p class="muted" style="padding:7px 9px">还没有人格</p>`;
  list.querySelectorAll("[data-select-persona]").forEach(row=>row.onclick=event=>{if(event.target.closest("[data-sidebar-edit-persona]"))return;selectPersona(row.dataset.selectPersona);});
  list.querySelectorAll("[data-sidebar-edit-persona]").forEach(button=>button.onclick=event=>{event.stopPropagation();openPersonaEditor(button.dataset.sidebarEditPersona);setSidebar(false);});
}

async function selectPersona(id){
  saveCurrentDraft();state.persona=id||null;
  if(state.persona)localStorage.setItem("atherloom:last-persona",state.persona);else localStorage.removeItem("atherloom:last-persona");
  state.memories=await api(`/api/memories?persona_key=${encodeURIComponent(memoryPersonaKey())}`);
  const scoped=state.conversations.filter(item=>(item.persona_id||null)===(state.persona||null));
  const persona=activePersona(),startup=startupConversationPlan(persona,scoped);
  if(startup.mode==="new")await newConversation();else if(startup.conversationId)await openConversation(startup.conversationId);else await newConversation();
  renderSettings();renderPickers();renderHistory();setSidebar(false);
}

function openPersonaEditor(id){
  const persona=state.personas.find(item=>item.id===id);if(!persona)return;
  openSettings("personas");const form=$("#personaForm");form.dataset.editing=persona.id;form.elements.name.value=persona.name;form.elements.prompt.value=persona.prompt;fillPersonaConfig(form,persona.config||{});
  $("#savePersona").textContent="保存修改";$("#cancelPersonaEdit").hidden=false;
  document.querySelectorAll("[data-persona-tab]").forEach(item=>item.classList.toggle("active",item.dataset.personaTab==="basic"));
  document.querySelectorAll("[data-persona-pane]").forEach(pane=>pane.classList.toggle("active",pane.dataset.personaPane==="basic"));
  requestAnimationFrame(()=>form.scrollIntoView({behavior:"smooth",block:"start"}));
}

async function updateHistoryState(id, action, {skipConfirm=false}={}) {
  const conversation = state.conversations.find(c => c.id === id); if (!conversation) return;
  if(action==="delete"){
    if(!skipConfirm&&!confirm(`删除对话“${conversation.title}”？这会删除其中的消息，其他人格和其他对话不受影响。`))return;
    const previous=[...state.conversations],wasCurrent=state.current===id;
    $("#conversationPopover").hidden=true;
    state.conversations=state.conversations.filter(item=>item.id!==id);state.message_cache.delete(id);
    if(wasCurrent){state.current=null;state.messages=[];renderCurrentTitle();renderMessages();}
    renderHistory();
    try{await api(`/api/conversations/${id}`,{method:"DELETE"});const fresh=await api("/api/bootstrap");state.conversations=(fresh.conversations||[]).filter(item=>item.id!==id);}
    catch(error){state.conversations=previous;if(wasCurrent)state.current=id;renderHistory();throw error;}
    const currentWasRemoved=wasCurrent||!state.current||!state.conversations.some(item=>item.id===state.current);
    if(currentWasRemoved){state.current=null;state.messages=[];state.version_selection={};renderCurrentTitle();renderMessages();const next=state.conversations.find(item=>(item.persona_id||null)===(state.persona||null));if(next)await openConversation(next.id);else await newConversation();}
    renderHistory();return;
  }
  const key = action === "pin" ? "pinned" : action === "star" ? "starred" : "archived";
  const saved = await api(`/api/conversations/${id}/state`, { method: "PATCH", body: JSON.stringify({ [key]: !conversation[key] }) });
  Object.assign(conversation, saved); renderHistory();
}

function renderProfile() {
  const name = state.settings.display_name?.trim();
  $("#profileName").textContent = name || "设置用户名";
  $("#profileAvatar").textContent = name ? [...name][0] : "·";
  $("#displayName").value = name || "";
}

function applyAppearance() {
  const scale = Number(state.settings.font_scale || 100);
  document.documentElement.style.setProperty("--font-scale", scale / 100);
  document.documentElement.dataset.density = state.settings.message_density || "comfortable";
  document.documentElement.dataset.codeTheme = state.settings.code_theme || "auto";
}

function renderAttachments(){const tray=$("#attachmentTray");tray.hidden=!state.attachments.length;tray.innerHTML=state.attachments.map((item,index)=>`<span>${item.kind==="image"?"▧":"▤"} ${escapeHtml(item.name)}<button type="button" data-remove-attachment="${index}">×</button></span>`).join("");document.querySelectorAll("[data-remove-attachment]").forEach(button=>button.onclick=()=>{state.attachments.splice(Number(button.dataset.removeAttachment),1);renderAttachments();});}
const readFile=(file,mode)=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(reader.error);mode==="text"?reader.readAsText(file):reader.readAsDataURL(file);});
async function prepareImageAttachment(file){const original=await readFile(file,"data");if(file.type==="image/gif"||file.size<=1400*1024)return {data:original,mime:file.type||"image/jpeg",size:file.size};const image=await new Promise((resolve,reject)=>{const item=new Image();item.onload=()=>resolve(item);item.onerror=()=>reject(new Error("图片无法读取"));item.src=original;}),limit=1800,scale=Math.min(1,limit/Math.max(image.naturalWidth,image.naturalHeight)),canvas=document.createElement("canvas");canvas.width=Math.max(1,Math.round(image.naturalWidth*scale));canvas.height=Math.max(1,Math.round(image.naturalHeight*scale));canvas.getContext("2d").drawImage(image,0,0,canvas.width,canvas.height);const data=canvas.toDataURL("image/jpeg",.84);return {data,mime:"image/jpeg",size:Math.ceil(data.length*3/4)};}
async function addAttachments(files){for(const file of [...files]){if(file.size>12*1024*1024){alert(`${file.name} 超过 12 MB，暂不添加`);continue;}try{const image=file.type.startsWith("image/"),text=file.type.startsWith("text/")||/\.(md|txt|json|csv|js|ts|py|html|css)$/i.test(file.name),prepared=image?await prepareImageAttachment(file):null;state.attachments.push({id:crypto.randomUUID?.()||`${Date.now()}-${Math.random()}`,name:file.name,mime:prepared?.mime||file.type||"application/octet-stream",kind:image?"image":text?"text":file.type==="application/pdf"?"pdf":"file",data:prepared?.data||(file.type==="application/pdf"?await readFile(file,"data"):undefined),text:text?(await readFile(file,"text")).slice(0,120000):undefined,size:prepared?.size||file.size});}catch(error){alert(`${file.name} 添加失败：${error.message}`);}}renderAttachments();updateComposerState();}

function personaQuery() { return state.persona ? `?persona_id=${encodeURIComponent(state.persona)}` : ""; }

function renderGameCards() {
  const catalog=gameState.catalog.filter(game=>game.id!=="card_room");
  $("#gameCards").innerHTML = catalog.map(game => `<button class="game-card ${game.id === gameState.current ? "active" : ""}" data-game-id="${game.id}"><span class="game-card-icon">${game.icon}</span><span><strong>${escapeHtml(game.name)}</strong><small>${escapeHtml(game.description)}</small></span></button>`).join("");
  document.querySelectorAll("[data-game-id]").forEach(button => button.onclick = () => openGame(button.dataset.gameId));
}

window.AtherloomNativePdfReady=()=>{
  const reader=$("#bookReader"),status=$("#bookStatus");
  try{const result=JSON.parse(window.AtherloomNative.takePdfResult());if(!result.ok)throw new Error(result.error||"PDF 解析失败");validateBookText(result.text);const key=`atherloom:book:${result.name}:${result.text.length}`,pre=document.createElement("pre");pre.textContent=result.text;reader.replaceChildren(pre);currentBook={title:result.name,key,text:result.text};loadBookAiChat();setBookControls(true);renderBookNotes();reader.scrollTop=Number(localStorage.getItem(key)||0);reader.onscroll=()=>localStorage.setItem(key,String(reader.scrollTop));status.textContent=`${result.name} · 已解析 ${result.pages} 页文字${result.truncated?"（正文过长，已安全截取）":""}`;}catch(error){currentBook=null;setBookControls(false);reader.innerHTML=`<div class="game-empty"><span>!</span><h3>PDF 没有打开</h3><p>${escapeHtml(error.message)}</p></div>`;status.textContent=`PDF 解析失败：${error.message}`;}
};

function formatHttpError(status,detail=""){
  const explanations={400:"请求格式或参数不符合接口要求",401:"API Key 无效、过期或没有提供",402:"账户余额、额度或付费状态不足",403:"当前 Key 没有访问该模型或接口的权限",404:"请求的资源、模型或接口不存在",408:"上游等待请求超时",409:"当前数据状态与操作冲突",413:"发送的文件或上下文超过接口允许大小",422:"请求内容校验未通过",429:"请求过于频繁，或账户已达到速率/额度限制",500:"上游服务内部错误",502:"本地服务收到无效的上游响应",503:"上游服务暂时不可用",504:"上游服务响应超时"};
  const reason=explanations[Number(status)]||"接口返回了未成功状态";return `HTTP ${status}：${reason}${detail?`；${String(detail).slice(0,800)}`:""}`;
}

function renderFishing() {
  const current = gameState.fishing; if (!current) return;
  const water = gameState.waters[current.water];
  $("#fishCoins").textContent = current.coins; $("#fishBait").textContent = current.bait; $("#fishTurn").textContent = current.turn;
  $("#fishingPlace").textContent = `${water?.name || "未知水域"} · 第 ${current.turn + 1} 个回合`;
  $("#fishCatch").innerHTML = Object.entries(current.catch).map(([name, count]) => `<span><b>${escapeHtml(name)}</b><em>× ${count}</em></span>`).join("") || `<small>鱼篓还是空的。</small>`;
  $("#fishJournal").innerHTML = [...current.journal].reverse().slice(0, 8).map(item => `<span>${escapeHtml(item)}</span>`).join("") || `<small>水面安静，等待第一竿。</small>`;
  $("#waterTabs").innerHTML = Object.entries(gameState.waters).map(([id, item]) => `<button class="${id === current.water ? "active" : ""}" data-water="${id}">${escapeHtml(item.name)}${current.unlocked.includes(id) ? "" : ` · ${item.unlock} 云贝`}</button>`).join("");
  document.querySelectorAll("[data-water]").forEach(button => button.onclick = () => playGame("travel", 1, button.dataset.water));
  renderGameRoom();
}

const gameNames={quiet_fishing:"云汀钓记",claw_machine:"抓娃娃机",cloud_slots:"云纹老虎机",star_merge:"星潮合成",mist_maze:"雾径迷宫",ember_dungeon:"余烬地牢"};
function currentGameSave(){return gameState.current==="quiet_fishing"?gameState.fishing:gameState.current==="claw_machine"?gameState.claw:gameState.current==="cloud_slots"?gameState.slots:gameState.current==="star_merge"?gameState.starMerge:gameState.current==="mist_maze"?gameState.maze:gameState.current==="ember_dungeon"?gameState.dungeon:null;}
function storeGameState(gameId,value){if(gameId==="quiet_fishing")gameState.fishing=value;else if(gameId==="claw_machine")gameState.claw=value;else if(gameId==="cloud_slots")gameState.slots=value;else if(gameId==="star_merge")gameState.starMerge=value;else if(gameId==="mist_maze")gameState.maze=value;else if(gameId==="ember_dungeon")gameState.dungeon=value;}
function renderCurrentGame(gameId=gameState.current){if(gameId==="quiet_fishing")renderFishing();else if(gameId==="claw_machine")renderClaw();else if(gameId==="cloud_slots")renderSlots();else if(gameId==="star_merge")renderStarMerge();else if(gameId==="mist_maze")renderMaze();else if(gameId==="ember_dungeon")renderDungeon();}
function renderGameRoom(){const current=currentGameSave();if(!current||!gameState.current)return;const name=activePersonaName(),messages=current.room_messages||[],pending=gameRoomPending.filter(item=>item.gameId===gameState.current);$("#gameRoom").hidden=false;$("#gameRoomTitle").textContent=`和 ${name} 边玩边聊`;$("#gameRoomThoughtName").textContent=`${name}的心里话`;$("#gameRoomThought").textContent=current.last_thought||"接过这一回合后，想法会留在这里。";$("#gameRoomContext").textContent=`正在一起玩「${gameNames[gameState.current]}」· 游玩与聊天可同时进行`;$("#gameRoomMessages").innerHTML=[...messages,...pending.flatMap(item=>[{role:"user",content:item.content},{role:"event",content:item.error||`${name} 一边玩，一边在回复这句话…`}])].map(item=>`<div class="game-room-message ${item.role}">${escapeHtml(item.content)}</div>`).join("")||`<div class="game-room-empty">你们还没说话。先玩一步，或者叫一声他的名字。</div>`;$("#gameRoomMessages").scrollTop=$("#gameRoomMessages").scrollHeight;}
function renderCardRoom(){const s=cardRoomState;$(".seat-top").innerHTML=`${escapeHtml(activePersonaName())}<small>当前人格 · ${s.landlord==="ai"?"地主":"农民"}</small>`;$(".seat-left").innerHTML="等待另一位人格<small>未入座</small>";$(".seat-right").innerHTML="等待另一位人格<small>未入座</small>";$("#cardRoomHand").innerHTML=s.hand.map((card,i)=>`<button class="playing-card ${i===s.turn%Math.max(1,s.hand.length)?"hint":""}" data-card-index="${i}">${card}</button>`).join("");$("#cardRoomPlayed").innerHTML=s.played.map(card=>`<span>${card}</span>`).join("")||"<small>底牌 3 张 · 等待叫地主</small>";$("#cardRoomStatus").textContent=s.turn%2===0?"轮到你出牌":"当前人格正在思考";$("#cardRoomTurn").textContent=s.turn%2===0?"你的回合":"等待下一轮";$("#cardRoomThought").textContent=s.turn%2?`${activePersonaName()}：我正在看这一轮的牌。`:`${activePersonaName()}：先叫地主，还是先观察牌面？`;document.querySelectorAll("[data-card-index]").forEach(button=>button.onclick=()=>{if(s.turn%2)return;const card=s.hand.splice(Number(button.dataset.cardIndex),1)[0];s.played.push(card);s.turn++;setTimeout(()=>{if(s.turn%2&&s.hand.length){s.played.push(s.hand.shift());s.turn++;renderCardRoom();}},500);renderCardRoom();});}
function renderClaw(){const current=gameState.claw;if(!current)return;$("#clawCoins").textContent=current.coins;$("#clawTurns").textContent=current.turn;$("#clawHead").style.left=`${current.position*20+10}%`;$("#clawPrizes").innerHTML=current.prizes.map((name,index)=>`<span class="${index===current.position?"targeted":""}">◇<small>${escapeHtml(name)}</small></span>`).join("");$("#clawInventory").innerHTML=Object.entries(current.inventory).map(([name,count])=>`<span><b>${escapeHtml(name)}</b><em>× ${count}</em></span>`).join("")||"<small>还没有抓到娃娃。</small>";$("#clawJournal").innerHTML=[...current.journal].reverse().slice(0,8).map(item=>`<span>${escapeHtml(item)}</span>`).join("")||"<small>机器正在等待第一爪。</small>";const today=new Date().toLocaleDateString("sv-SE");$("#clawCheckin").disabled=current.last_checkin===today;$("#clawCheckin").textContent=current.last_checkin===today?"今天已签到":"每日签到 · +50";$("#clawSellAll").disabled=!Object.keys(current.inventory||{}).length;renderGameRoom();}
function renderSlots(){const current=gameState.slots;if(!current)return;[$("#slotOne"),$("#slotTwo"),$("#slotThree")].forEach((node,index)=>node.textContent=current.reels[index]);$("#slotCoins").textContent=current.coins;$("#slotTurns").textContent=current.turn;$("#slotJournal").innerHTML=[...current.journal].reverse().slice(0,8).map(item=>`<span>${escapeHtml(item)}</span>`).join("")||"<small>拉下摇杆开始。</small>";renderGameRoom();}
function renderStarMerge(){const current=gameState.starMerge;if(!current)return;const self=gameState.starMergeMode==="self",personaName=activePersonaName();$("#starMergeScore").textContent=current.score;$("#starMergeBest").textContent=current.best;$("#starMergeTurns").textContent=current.turn;$("#starMergeStatus").textContent=current.status==="won"?"已经合成 2048":current.status==="over"?"本局结束":"进行中";$("#starMergeBoard").innerHTML=current.board.map(value=>`<span class="star-tile" data-value="${value||0}">${value||""}</span>`).join("");$("#starMergeJournal").innerHTML=[...(current.journal||[])].reverse().slice(0,8).map(item=>`<span>${escapeHtml(item)}</span>`).join("")||"<small>移动一次，星潮就会开始留下记录。</small>";$("#starGoalName").textContent=`${personaName}的目标`;$("#starModeSelf").classList.toggle("active",self);$("#starModeAi").classList.toggle("active",!self);$("#starDirectionPad").hidden=!self;$("#starTurnOwner").textContent=self?"现在由你掌舵":`现在交给 ${personaName}`;$("#undoStarMerge").disabled=!(current.history||[]).length;if(gameState.current==="star_merge")$("#aiGameControls").hidden=self;renderGameRoom();}
function renderMaze(){const current=gameState.maze;if(!current)return;const [pr,pc]=current.player,[gr,gc]=current.goal,grid=current.grid||[];$("#mazeLevel").textContent=current.level||1;$("#mazeTurns").textContent=current.turn;$("#mazeStatus").textContent=`第 ${current.level||1} 关 · 寻找出口`;$("#mazeGoalName").textContent=`${activePersonaName()}的目标`;$("#mazeBoard").style.gridTemplateColumns=`repeat(${grid.length||9},1fr)`;$("#mazeBoard").innerHTML=grid.flatMap((row,r)=>[...row].map((cell,c)=>`<span class="maze-cell ${cell==="#"?"wall":""} ${r===pr&&c===pc?"player":""} ${r===gr&&c===gc?"goal":""}">${r===pr&&c===pc?"●":r===gr&&c===gc?"✦":""}</span>`)).join("");$("#mazeJournal").innerHTML=[...(current.journal||[])].reverse().slice(0,8).map(item=>`<span>${escapeHtml(item)}</span>`).join("")||"<small>雾正等着第一步。</small>";renderGameRoom();}
function renderDungeon(){const current=gameState.dungeon;if(!current)return;const enemy=current.enemy;$("#dungeonFloor").textContent=current.floor;$("#dungeonHp").textContent=`${current.hp} / ${current.max_hp}`;$("#dungeonPotions").textContent=current.potions;$("#dungeonWins").textContent=current.wins;$("#dungeonGoalName").textContent=`${activePersonaName()}的目标`;$("#dungeonSceneLabel").textContent=current.status==="over"?"旅程暂时结束":enemy?`第 ${current.floor} 层 · 遭遇敌人`:`第 ${current.floor} 层 · 前方尚且安静`;$("#dungeonEnemy").textContent=current.status==="over"?"火光熄灭了":enemy?enemy.name:"余烬仍在呼吸";$("#dungeonEnemyHp").textContent=enemy?`敌人体力 ${Math.max(0,enemy.hp)} / ${enemy.max_hp}`:current.status==="over"?"重新出发后还能再来。":"可以继续探索，或在安全处休整。";document.querySelectorAll("[data-dungeon-action]").forEach(button=>{const action=button.dataset.dungeonAction;button.disabled=current.status==="over"||(enemy?!["attack","guard"].includes(action):action==="attack"||action==="guard"||(action==="rest"&&(!current.potions||current.hp>=current.max_hp)));});$("#dungeonJournal").innerHTML=[...(current.journal||[])].reverse().slice(0,8).map(item=>`<span>${escapeHtml(item)}</span>`).join("")||"<small>第一簇余烬还没有被惊动。</small>";renderGameRoom();}
function setStarMergeMode(mode){gameState.starMergeMode=mode==="ai"?"ai":"self";renderStarMerge();if(gameState.starMergeMode==="self")$("#starMergeBoard").focus();}

async function openGame(gameId) {
  gameState.current = gameId;localStorage.setItem("atherloom:last-game",gameId); renderGameCards();
  $("#gameEmpty").hidden=true;$("#cardRoomStage").hidden=gameId!=="card_room";$("#fishingStage").hidden=gameId!=="quiet_fishing";$("#clawStage").hidden=gameId!=="claw_machine";$("#slotsStage").hidden=gameId!=="cloud_slots";$("#starMergeStage").hidden=gameId!=="star_merge";$("#mazeStage").hidden=gameId!=="mist_maze";$("#dungeonStage").hidden=gameId!=="ember_dungeon";
  $("#aiGameControls").hidden=!Object.keys(gameNames).includes(gameId);$("#gameRoom").hidden=!Object.keys(gameNames).includes(gameId);
  $("#aiGameTitle").textContent=`交给 ${activePersonaName()}`;
  $("#aiGameStatus").textContent=gameId==="star_merge"?"当前人格会读取完整棋盘，只能选择上下左右；每一步都由 Atherloom 验证。":"当前人格会读取局面，只能执行可用动作；单次预算最多 30 云贝。";
  if(gameId==="card_room"){renderCardRoom();return;}
  if(!Object.keys(gameNames).includes(gameId)){$("#gameEmpty").hidden=false;const game=gameState.catalog.find(item=>item.id===gameId);$("#gameEmpty").innerHTML=`<span>${game.icon}</span><h3>${escapeHtml(game.name)}</h3><p>${escapeHtml(game.description)}</p>`;return;}
  const payload = await api(`/api/games/${gameId}/state${personaQuery()}`);
  storeGameState(gameId,payload.state);if(gameId==="quiet_fishing")gameState.waters=payload.waters;renderCurrentGame(gameId);
}

async function playGame(action, amount = 1, target = "") {
  try {
    const payload = await api(`/api/games/quiet_fishing/action${personaQuery()}`, { method: "POST", body: JSON.stringify({ action, amount, target }) });
    gameState.fishing = payload.state; renderFishing();
  } catch (error) { alert(error.message); }
}
async function playMiniGame(gameId,action,amount=1){try{const payload=await api(`/api/games/${gameId}/action${personaQuery()}`,{method:"POST",body:JSON.stringify({action,amount})});storeGameState(gameId,payload.state);renderCurrentGame(gameId);}catch(error){alert(error.message);}}
let aiGameRun=0;
async function aiPlayGame(mode){const provider=activeProvider()||state.providers[0],name=activePersonaName();if(!provider){$("#aiGameStatus").textContent="还没有可用线路；游戏会留在这里，请先到设置里添加线路。";return;}state.provider||=provider.id;const run=++aiGameRun,buttons=[...document.querySelectorAll("[data-ai-game-turns]")],stop=$("#stopAiGame"),gameId=gameState.current,autonomous=mode==="auto",turns=autonomous?9:Number(mode);buttons.forEach(button=>button.disabled=true);stop.hidden=false;let completed=0,spent=0,lastComment="";try{for(let turn=0;turn<turns&&run===aiGameRun;turn++){const remaining=30-spent;if(remaining<=0&&!["star_merge","mist_maze","ember_dungeon"].includes(gameId))break;$("#aiGameStatus").textContent=autonomous?`${name} 正在决定第 ${turn+1} 回合，还可以随时停止…`:`${name} 正在进行第 ${turn+1}/${turns} 回合…`;const payload=await api(`/api/games/${gameId}/ai-turn`,{method:"POST",body:JSON.stringify({provider_id:provider.id,persona_id:state.persona,turns:1,autonomous,max_spend:remaining}),timeout:45000});if(run!==aiGameRun)break;spent+=payload.spent||0;if(payload.decisions.length){completed++;lastComment=payload.decisions.at(-1)?.comment||lastComment;}storeGameState(gameId,payload.state);renderCurrentGame(gameId);$("#aiGameStatus").textContent=`${name} 已完成 ${completed}${autonomous?"/最多 9":`/${turns}`} 回合。${lastComment?`心里话：${lastComment}`:"正在看看下一步…"}`;if(!payload.decisions.length||(autonomous&&payload.continue_playing===false))break;}if(run===aiGameRun)$("#aiGameStatus").textContent=completed?`${name}${autonomous?"自己选择并":""}完成 ${completed} 回合${spent?`，花费 ${spent} 云贝`:""}。心里话：${lastComment||"专心操作中"}`:`${name} 因预算或局面限制没有执行动作。`;}catch(error){if(run===aiGameRun)$("#aiGameStatus").textContent=`${completed?`已完成 ${completed} 回合；`:""}${name} 游玩失败：${error.message}`;}finally{if(run===aiGameRun){buttons.forEach(button=>button.disabled=false);stop.hidden=true;}}}

function parseGameRequest(content){
  const text=String(content||"").replace(/\s+/g,""),requested=/(?:你|请|帮我|能不能|可以|可不可以).{0,12}(?:玩|去玩|来玩|试试|钓|抓|转|探路|打怪|冒险)|(?:玩|去玩|来玩|试试).{0,8}(?:游戏|小游戏|钓鱼|抓娃娃|老虎机|迷宫|地牢)/.test(text);
  if(!requested)return null;
  const gameId=/(?:迷宫|雾径|探路)/.test(text)?"mist_maze":/(?:地牢|打怪|冒险|余烬)/.test(text)?"ember_dungeon":/(?:2048|星潮|合成游戏|数字合成)/.test(text)?"star_merge":/(?:抓娃娃|娃娃机|下爪)/.test(text)?"claw_machine":/(?:老虎机|拉杆|转盘|摇奖)/.test(text)?"cloud_slots":/(?:钓鱼|抛竿|钓一竿|鱼塘)/.test(text)?"quiet_fishing":gameState.current||"quiet_fishing";
  const autonomous=/(?:自己决定|随便玩|想玩多久|自主)/.test(text),turns=/(?:9|九)(?:次|步|回合|竿|局)/.test(text)?9:/(?:6|六)(?:次|步|回合|竿|局)/.test(text)?6:/(?:3|三|几)(?:次|步|回合|竿|局)/.test(text)?3:1;
  return {gameId,turns:autonomous?9:turns,autonomous};
}
async function prepareChatGameContext(content){
  const request=parseGameRequest(content),text=String(content||""),mentioned=/(?:迷宫|雾径|探路)/.test(text)?"mist_maze":/(?:地牢|打怪|冒险|余烬)/.test(text)?"ember_dungeon":/(?:2048|星潮|合成游戏|数字合成)/.test(text)?"star_merge":/(?:抓娃娃|娃娃机|下爪)/.test(text)?"claw_machine":/(?:老虎机|拉杆|转盘|摇奖)/.test(text)?"cloud_slots":/(?:钓鱼|抛竿|鱼塘)/.test(text)?"quiet_fishing":null;
  if(!request){const gameId=mentioned||gameState.current||localStorage.getItem("atherloom:last-game");if(!gameId)return /游戏库|小游戏/.test(text)?"Atherloom 内置游戏库目前包含：云汀钓记、抓娃娃机、云纹老虎机、星潮合成、雾径迷宫和余烬地牢。它们支持用户亲自操作、交给当前人格和独立共玩对话。":"";try{const payload=await api(`/api/games/${gameId}/state${personaQuery()}`),current=payload.state,recent=(current.journal||[]).slice(-5).join("；")||"还没有动作",visible=Object.fromEntries(Object.entries(current).filter(([key])=>!["history","room_messages"].includes(key)));return `用户最近正在 Atherloom 内置游戏「${gameNames[gameId]}」中和当前人格一起玩。当前应用存档：${JSON.stringify(visible)}。最近动作：${recent}。这是应用内真实状态，不是需要联网搜索的外部游戏。`;}catch{return "";}}
  const provider=activeProvider();if(!provider)return "";
  const {gameId,turns,autonomous}=request,name=activePersonaName();
  try{
    const payload=await api(`/api/games/${gameId}/ai-turn`,{method:"POST",body:JSON.stringify({provider_id:provider.id,persona_id:state.persona,turns,autonomous,max_spend:30}),timeout:Math.max(50000,turns*40000)});
    storeGameState(gameId,payload.state);if(gameState.current===gameId)renderCurrentGame(gameId);
    const details=payload.decisions.flatMap(item=>[...(item.events||[]),item.comment?`心里话：${item.comment}`:""]).filter(Boolean);
    const stateSummary=gameId==="quiet_fishing"?`当前鱼篓：${Object.entries(payload.state.catch||{}).map(([fish,count])=>`${fish}×${count}`).join("、")||"空"}；鱼饵 ${payload.state.bait}，云贝 ${payload.state.coins}`:gameId==="claw_machine"?`当前收藏：${Object.entries(payload.state.inventory||{}).map(([prize,count])=>`${prize}×${count}`).join("、")||"空"}；云贝 ${payload.state.coins}`:gameId==="cloud_slots"?`当前转轮：${(payload.state.reels||[]).join(" · ")}；云贝 ${payload.state.coins}`:gameId==="star_merge"?`当前得分 ${payload.state.score}，最高星块 ${payload.state.best}，状态 ${payload.state.status}`:gameId==="mist_maze"?`当前位置 ${payload.state.player.join(",")}，已经走 ${payload.state.turn} 步，状态 ${payload.state.status}`:`当前第 ${payload.state.floor} 层，体力 ${payload.state.hp}/${payload.state.max_hp}，${payload.state.enemy?`正在迎战 ${payload.state.enemy.name}`:"暂时安全"}`;
    return `${name} 已通过 Atherloom 游戏工具真实游玩「${gameNames[gameId]}」${payload.decisions.length} 个回合。${details.join("；")}。${stateSummary}。这是已执行结果，不是想象或角色扮演。`;
  }catch(error){return `${name} 已调用「${gameNames[gameId]}」游戏工具，但执行失败：${error.message}。请如实告诉用户失败原因，不要假装玩过。`;}
}

async function openGameLibrary() {
  $("#gameLibrary").hidden = false;
  if (!gameState.catalog.length) gameState.catalog = await api("/api/games");
  renderGameCards(); if (!gameState.current) openGame("quiet_fishing");
}

function visibleMessageVersions() {
  const output = [], handled = new Set();
  for (const message of state.messages) {
    if (message.role !== "assistant" || !message.parent_message_id) { output.push(message); continue; }
    if (handled.has(message.parent_message_id)) continue;
    handled.add(message.parent_message_id);
    const versions = state.messages.filter(item => item.role === "assistant" && item.parent_message_id === message.parent_message_id);
    const requested = state.version_selection[message.parent_message_id];
    const index = Math.max(0, Math.min(Number.isInteger(requested) ? requested : versions.length - 1, versions.length - 1));
    output.push(Object.assign(versions[index], { _version_index: index, _version_count: versions.length }));
  }
  return output;
}

function assistantContentParts(content){
  const source=String(content||""),match=source.match(/<questions>([\s\S]*?)<\/questions>/i);let questions=[];
  if(match){try{const parsed=JSON.parse(match[1]);if(Array.isArray(parsed))questions=parsed.slice(0,4).map(item=>({question:String(item?.question||"").trim(),options:Array.isArray(item?.options)?item.options.map(value=>String(value).trim()).filter(Boolean).slice(0,5):[]})).filter(item=>item.question&&item.options.length>=2);}catch{} }
  return {text:match?source.replace(match[0],"").trim():source,questions};
}
function renderQuestionCards(questions){return questions.length?`<section class="question-deck" aria-label="助手提问"><div class="question-deck-title">想听听你的选择</div>${questions.map((item,index)=>`<div class="question-card"><strong><span>${index+1}</span>${escapeHtml(item.question)}</strong><div>${item.options.map(option=>`<button type="button" data-question-option="${encodeURIComponent(option)}" data-question-title="${encodeURIComponent(item.question)}">${escapeHtml(option)}</button>`).join("")}</div></div>`).join("")}</section>`:"";}
function renderAssistantContent(content){const parts=assistantContentParts(content);return renderMarkdown(parts.text)+renderQuestionCards(parts.questions);}

function renderMessages({stickToBottom=true}={}) {
  $("#welcome").hidden = state.messages.length > 0;
  $("#messages").innerHTML = visibleMessageVersions().map(m => { const index=state.messages.indexOf(m); return `<article class="message ${m.role}" data-index="${index}">
    <div class="message-body">${m.memory_sources?.length ? `<div class="memory-sources">本轮使用记忆：${m.memory_sources.map(source => `<span>${escapeHtml(source.title)}</span>`).join("")}</div>` : ""}${m.reasoning ? `<details class="thinking"><summary>思考过程（点开查看）</summary><div>${escapeHtml(m.reasoning)}</div></details>` : ""}<div class="bubble">${m.pending && !m.content ? `<span class="response-waiting"><i></i>正在生成</span>` : m.role === "assistant" && !m.streaming ? renderAssistantContent(m.content) : escapeHtml(m.content)}</div></div>
    ${m.pending ? "" : `<div class="message-actions"><button data-action="copy">复制</button>${m.id ? `<button data-action="favorite">${state.favorites.some(f => f.source_message_id === m.id && f.owners?.includes("user")) ? "★ 已珍藏" : "☆ 珍藏"}</button>` : ""}<button data-action="edit">修改</button>${m.role === "user" || m.parent_message_id || m.retry_content ? `<button data-action="regenerate">重新 Roll</button>` : ""}${m.id ? `<button data-action="more" aria-label="更多消息操作">•••</button>` : ""}</div>`}
    ${m.role === "assistant" && m._version_count > 1 ? `<div class="version-switcher"><button data-action="version-prev" ${m._version_index === 0 ? "disabled" : ""}>‹</button><span>${m._version_index + 1} / ${m._version_count}</span><button data-action="version-next" ${m._version_index === m._version_count - 1 ? "disabled" : ""}>›</button></div>` : ""}
    ${m.role === "assistant" && m.model ? `<div class="message-meta">${escapeHtml(m.model)}</div>` : ""}</article>`; }).join("");
  document.querySelectorAll(".message [data-action]").forEach(button => button.onclick = () => handleMessageAction(button.closest(".message"), button.dataset.action));
  document.querySelectorAll("[data-question-option]").forEach(button=>button.onclick=()=>{const input=$("#prompt"),line=`关于「${decodeURIComponent(button.dataset.questionTitle)}」，我的选择是：${decodeURIComponent(button.dataset.questionOption)}`;input.value=input.value.trim()?`${input.value.trim()}\n${line}`:line;button.closest(".question-card").querySelectorAll("button").forEach(item=>item.classList.toggle("selected",item===button));input.dispatchEvent(new Event("input"));input.focus();});
  document.querySelectorAll(".message.assistant").forEach(article=>{const message=state.messages[Number(article.dataset.index)],meta=article.querySelector(".message-meta");if(message?.model&&meta&&message.usage?.total_tokens!=null)meta.textContent=`${message.model} · ${Number(message.usage.total_tokens).toLocaleString()} 全部 tokens`;});
  if(stickToBottom)$("#chatScroll").scrollTop = $("#chatScroll").scrollHeight;
  renderContextUsage();
}
function estimateTokens(text){const chinese=(text.match(/[\u3400-\u9fff]/g)||[]).length,other=text.replace(/[\u3400-\u9fff]/g,"").length;return chinese+Math.ceil(other/4);}
function renderContextUsage(){const history=state.messages.reduce((total,message)=>total+estimateTokens(message.content||"")+estimateTokens(message.reasoning||""),0),draft=estimateTokens($("#prompt")?.value||"");if($("#contextUsage"))$("#contextUsage").textContent=`估算上下文 ≈ ${(history+draft).toLocaleString()} tokens`;}

async function handleMessageAction(article, action) {
  const message = state.messages[Number(article.dataset.index)];
  if (action === "copy") return navigator.clipboard.writeText(message.content);
  if (action === "favorite") {
    const existing=state.favorites.find(item=>item.source_message_id===message.id&&item.owners?.includes("user"));
    if(existing) await api(`/api/favorites/${message.id}?owner=user`,{method:"DELETE"}); else await api(`/api/favorites/${message.id}`,{method:"POST",body:JSON.stringify({owner:"user"})});
    state.favorites=await api("/api/favorites");renderMessages();return;
  }
  if (action === "version-prev" || action === "version-next") { const current=message._version_index||0,next=current+(action==="version-next"?1:-1),versions=state.messages.filter(item=>item.role==="assistant"&&item.parent_message_id===message.parent_message_id),selected=versions[next];state.version_selection[message.parent_message_id]=next;renderMessages();if(selected?.id)await api("/api/messages/selection",{method:"PATCH",body:JSON.stringify({conversation_id:state.current,parent_message_id:message.parent_message_id,assistant_message_id:selected.id})});return; }
  if (action === "more") { $("#messageMenu").dataset.messageIndex=article.dataset.index;$("#messageMenu").hidden=false;return; }
  if (action === "edit") return openMessageEditor(message);
  if (action === "branch") {
    const conversation = await api(`/api/conversations/${state.current}/branch/${message.id}`, { method: "POST" });
    state.conversations.unshift(conversation); renderHistory(); return openConversation(conversation.id);
  }
  if (action === "regenerate") {
    let userId=message.role === "user" ? message.id : message.parent_message_id;
    const mediaContext=message.media_context||message.retry_media_context||"";
    if(!userId&&message.role==="assistant"){
      const index=state.messages.indexOf(message);
      userId=[...state.messages.slice(0,index)].reverse().find(item=>item.role==="user")?.id||null;
    }
    if(message.role==="assistant"&&!message.id)state.messages.splice(state.messages.indexOf(message),1);
    return generateReply(message.retry_content || "",userId,[],mediaContext);
  }
}

function openMessageEditor(message){if(!message)return;const editor=$("#messageEditor");editor.dataset.messageId=message.id||"";editor.dataset.messageIndex=String(state.messages.indexOf(message));$("#messageEditContent").value=message.content;editor.hidden=false;requestAnimationFrame(()=>$("#messageEditContent").focus());}

function renderPickers() {
  const provider = activeProvider(); const persona = activePersona();
  const latestUsage=[...state.messages].reverse().find(item=>item.role==="assistant"&&item.usage)?.usage;
  const tokenLabel=latestUsage?.total_tokens!=null?` · ${Number(latestUsage.total_tokens).toLocaleString()} 全部 tokens`:" · tokens—";
  $("#modelPicker").textContent = provider ? `${provider.name} · ${provider.model}${tokenLabel}⌄` : "添加 API 线路";
  $("#personaPicker").textContent = persona ? `${persona.name}⌄` : "默认人格⌄";
  const phrases=persona?.config?.quick_phrases||[];$("#quickPhraseButton").hidden=!phrases.length;
}

let editingWorldbookEntries=[];
function renderWorldbookEntries(){const list=$("#worldbookEntryList");list.innerHTML=editingWorldbookEntries.map((entry,index)=>`<div class="worldbook-entry-row"><div><strong>${escapeHtml(entry.name||"未命名条目")}</strong><small>${entry.constant?"常驻":"关键词触发"} · ${entry.enabled!==false?"启用":"停用"} · 优先级 ${entry.priority||0}</small></div><div><button type="button" data-edit-worldbook-entry="${index}">编辑</button><button type="button" data-delete-worldbook-entry="${index}">删除</button></div></div>`).join("")||`<p class="muted">还没有条目。添加后才能向模型注入内容。</p>`;list.querySelectorAll("[data-edit-worldbook-entry]").forEach(button=>button.onclick=()=>openWorldbookEntryEditor(Number(button.dataset.editWorldbookEntry)));list.querySelectorAll("[data-delete-worldbook-entry]").forEach(button=>button.onclick=()=>{editingWorldbookEntries.splice(Number(button.dataset.deleteWorldbookEntry),1);renderWorldbookEntries();});}
function renderWorldbooks(){const list=$("#worldbookList");list.innerHTML=state.worldbooks.map(book=>`<div class="list-card"><div><strong>${escapeHtml(book.name)}</strong><small>${book.enabled?"已启用":"已停用"} · ${book.entries?.length||0} 个条目 · ${escapeHtml(book.description||"无简介")}</small></div><div class="provider-card-actions"><button data-edit-worldbook="${book.id}">编辑</button><button data-delete-worldbook="${book.id}">删除</button></div></div>`).join("")||`<p class="muted">还没有世界书。</p>`;list.querySelectorAll("[data-edit-worldbook]").forEach(button=>button.onclick=()=>openWorldbookForm(state.worldbooks.find(book=>book.id===button.dataset.editWorldbook)));list.querySelectorAll("[data-delete-worldbook]").forEach(button=>button.onclick=async()=>{if(!confirm("删除这本世界书？现有聊天中的选择也会失效。"))return;await api(`/api/worldbooks/${button.dataset.deleteWorldbook}`,{method:"DELETE"});state.worldbooks=state.worldbooks.filter(book=>book.id!==button.dataset.deleteWorldbook);renderWorldbooks();renderInjectionTray();});}
function openWorldbookForm(book=null){const form=$("#worldbookForm");form.hidden=false;form.dataset.editing=book?.id||"";form.elements.name.value=book?.name||"";form.elements.description.value=book?.description||"";form.elements.enabled.checked=book?.enabled!==false;editingWorldbookEntries=(book?.entries||[]).map(entry=>({...entry,keywords:[...(entry.keywords||[])]}));renderWorldbookEntries();requestAnimationFrame(()=>form.elements.name.focus());}
function openWorldbookEntryEditor(index=-1){const overlay=$("#worldbookEntryEditor"),form=overlay.querySelector("form"),entry=index>=0?editingWorldbookEntries[index]:{};form.dataset.entryIndex=String(index);for(const name of ["name","content","position","role","priority","scan_depth"])form.elements[name].value=entry[name]??({position:"system_after",role:"system",priority:0,scan_depth:4}[name]??"");form.elements.enabled.checked=entry.enabled!==false;form.elements.constant.checked=!!entry.constant;form.elements.use_regex.checked=!!entry.use_regex;form.elements.case_sensitive.checked=!!entry.case_sensitive;form.elements.keywords.value=(entry.keywords||[]).join("\n");overlay.hidden=false;}
function instructionEntryMeta(entry){if(entry.constant)return"常驻注入";const keywords=(entry.keywords||[]).filter(Boolean);return keywords.length?`触发词：${keywords.join("、")}`:"选择后注入";}
function openInstructionPicker(){const selected=new Set(selectedWorldbookIds()),books=state.worldbooks.filter(book=>book.enabled);$("#instructionBookList").innerHTML=books.map(book=>{const entries=(book.entries||[]).filter(entry=>entry.enabled!==false);return `<label class="instruction-book"><input type="checkbox" value="${book.id}" ${selected.has(book.id)?"checked":""}><span class="instruction-book-body"><strong>${escapeHtml(book.name)}</strong>${book.description?`<small class="instruction-book-description">${escapeHtml(book.description)}</small>`:""}<span class="instruction-entry-list">${entries.map(entry=>`<span class="instruction-entry"><span class="instruction-entry-head"><b>${escapeHtml(entry.name||"未命名条目")}</b><em>${escapeHtml(instructionEntryMeta(entry))}</em></span><span class="instruction-entry-content">${escapeHtml(entry.content||"（内容为空）")}</span></span>`).join("")||`<small class="instruction-empty">这本世界书还没有启用的条目。</small>`}</span></span></label>`;}).join("")||`<p class="muted">请先在设置的“世界书”中添加内容。</p>`;$("#instructionPicker").hidden=false;}
function updateMcpTransportFields(){const stdio=$("#mcpTransport").value==="stdio";$("#mcpHttpFields").hidden=stdio;$("#mcpStdioFields").hidden=!stdio;$("#mcpServerForm").elements.url.required=!stdio;$("#mcpServerForm").elements.command.required=stdio;}
function renderMcpTools(server){const list=$("#mcpToolList");if(!server){list.innerHTML="";return;}list.innerHTML=`<h4 class="subheading">${escapeHtml(server.name)} · 工具权限</h4>`+(server.tools||[]).map(tool=>{const name=String(tool.name||""),policy=server.tool_policies?.[name]||"allow";return `<div class="setting-row"><div><strong>${escapeHtml(name)}</strong><small>${escapeHtml(tool.description||"无说明")}</small></div><select data-mcp-policy="${escapeHtml(name)}" data-mcp-server="${server.id}"><option value="allow" ${policy==="allow"?"selected":""}>始终允许</option><option value="ask" ${policy==="ask"?"selected":""}>每次询问</option><option value="deny" ${policy==="deny"?"selected":""}>禁止</option></select></div>`;}).join("")||`<p class="muted">尚未读取工具，请点击刷新工具。</p>`;list.querySelectorAll("[data-mcp-policy]").forEach(select=>select.onchange=async()=>{const item=state.mcp_servers.find(server=>server.id===select.dataset.mcpServer);item.tool_policies={...(item.tool_policies||{}),[select.dataset.mcpPolicy]:select.value};const payload=mcpPayloadFromServer(item);const saved=await api(`/api/mcp-servers/${item.id}`,{method:"PUT",body:JSON.stringify(payload)});Object.assign(item,saved);renderMcpServers();renderMcpTools(item);});}
function renderMcpAudit(){const list=$("#mcpAuditList");if(!list)return;list.innerHTML=state.mcp_audit.map(item=>`<div class="list-card"><div><strong>${escapeHtml(item.server_name||"已删除服务")} · ${escapeHtml(item.tool_name)}</strong><small>${escapeHtml(item.status)} · ${new Date(item.created_at).toLocaleString()}</small>${item.conversation_title||item.user_content?`<p>来源：${escapeHtml(item.conversation_title||"未命名对话")}${item.user_content?` · ${escapeHtml(String(item.user_content).slice(0,120))}`:""}</p>`:""}${item.detail?`<p>${escapeHtml(item.detail)}</p>`:""}</div></div>`).join("")||`<p class="muted">还没有工具调用记录。</p>`;}
function mcpPayloadFromServer(server){return {name:server.name,transport:server.transport||"http",url:server.url||"",token:"",command:server.command||"",args:server.args||[],env:{},headers:{},tool_policies:server.tool_policies||{},enabled:server.enabled!==false&&server.enabled!==0};}
function renderMcpServers(){const list=$("#mcpServerList");if(!list)return;list.innerHTML=state.mcp_servers.map(server=>`<div class="list-card"><div><strong>${escapeHtml(server.name)}</strong><small>${server.enabled?"已启用":"已停用"} · ${server.transport==="stdio"?"stdio":escapeHtml(server.url)} · ${server.last_status||"未测试"} · ${(server.tools||[]).length} 个工具</small></div><div class="provider-card-actions"><button data-tools-mcp="${server.id}">工具</button><button data-refresh-mcp="${server.id}">刷新</button><button data-edit-mcp="${server.id}">编辑</button><button data-delete-mcp="${server.id}">删除</button></div></div>`).join("")||`<p class="muted">还没有 MCP 服务。</p>`;list.querySelectorAll("[data-tools-mcp]").forEach(button=>button.onclick=()=>renderMcpTools(state.mcp_servers.find(item=>item.id===button.dataset.toolsMcp)));list.querySelectorAll("[data-refresh-mcp]").forEach(button=>button.onclick=async()=>{const item=state.mcp_servers.find(server=>server.id===button.dataset.refreshMcp);$("#mcpStatusLabel").textContent=`正在刷新 ${item.name}…`;try{Object.assign(item,await api(`/api/mcp-servers/${item.id}/refresh`,{method:"POST",timeout:40000}));$("#mcpStatusDot").classList.add("online");$("#mcpStatusLabel").textContent=`${item.name} 已连接`;$("#mcpStatusDetail").textContent=`发现 ${(item.tools||[]).length} 个工具`;renderMcpServers();renderMcpTools(item);}catch(error){$("#mcpStatusLabel").textContent="MCP 连接失败";$("#mcpStatusDetail").textContent=error.message;}});list.querySelectorAll("[data-edit-mcp]").forEach(button=>button.onclick=()=>{const server=state.mcp_servers.find(item=>item.id===button.dataset.editMcp),form=$("#mcpServerForm");form.dataset.editing=server.id;form.elements.name.value=server.name;form.elements.transport.value=server.transport||"http";form.elements.url.value=server.url||"";form.elements.token.value="";form.elements.command.value=server.command||"";form.elements.args.value=(server.args||[]).join("\n");form.elements.env.value="{}";form.elements.headers.value=JSON.stringify(server.headers||{},null,2);form.elements.enabled.checked=server.enabled!==false&&server.enabled!==0;updateMcpTransportFields();$("#cancelMcpEdit").hidden=false;$("#saveMcpServer").textContent="保存修改";form.scrollIntoView({behavior:"smooth",block:"center"});});list.querySelectorAll("[data-delete-mcp]").forEach(button=>button.onclick=async()=>{const server=state.mcp_servers.find(item=>item.id===button.dataset.deleteMcp);if(!confirm(`删除 MCP 服务“${server.name}”？`))return;await api(`/api/mcp-servers/${server.id}`,{method:"DELETE"});state.mcp_servers=state.mcp_servers.filter(item=>item.id!==server.id);renderMcpServers();});}
function resetMcpForm(){const form=$("#mcpServerForm");form.reset();form.elements.enabled.checked=true;form.elements.headers.value="{}";form.elements.env.value="{}";delete form.dataset.editing;$("#cancelMcpEdit").hidden=true;$("#saveMcpServer").textContent="保存连接";updateMcpTransportFields();}

function renderSettings() {
  if($("#embeddingProvider")){const selected=$("#embeddingProvider").value||state.settings.embedding_provider_id||"";$("#embeddingProvider").innerHTML=`<option value="">选择 API 线路</option>`+state.providers.filter(item=>item.protocol!=="anthropic").map(item=>`<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("");$("#embeddingProvider").value=state.providers.some(item=>item.id===selected&&item.protocol!=="anthropic")?selected:"";}
  $("#providerList").innerHTML = state.providers.map(p => `<div class="list-card"><div><strong>${escapeHtml(p.name)}</strong><small>${escapeHtml(p.protocol)} · ${escapeHtml(p.model)} · 温度 ${p.temperature ?? 0.7} · ${p.has_api_key ? "Key 已保存" : "无 Key"}</small></div><div class="provider-card-actions"><button data-edit-provider="${p.id}">编辑</button><button data-delete-provider="${p.id}">删除</button></div></div>`).join("") || ($("#providerForm").hidden ? `<div class="empty-provider"><p class="muted">还没有 API 线路。</p><button class="primary" id="emptyAddProvider">添加第一条线路</button></div>` : "");
  $("#personaList").innerHTML = state.personas.map(p => `<div class="list-card"><div><strong>${escapeHtml(p.name)}</strong><small>${escapeHtml(p.prompt.slice(0, 70) || "空白人格")}</small></div><div class="provider-card-actions"><button data-edit-persona="${p.id}">编辑</button><button data-delete-persona="${p.id}">删除</button></div></div>`).join("");
  renderWorldbooks();
  renderMcpServers();
  const kindLabels = { fact: "事实", preference: "偏好", relationship: "关系", promise: "承诺", event: "事件", emotion: "情感", summary: "摘要", diary: "日记", other: "其他" };
  const kindFilter = $("#memoryKindFilter")?.value || "";
  const visibleMemories = state.memories.filter(memory => !kindFilter || memory.kind === kindFilter);
  $("#memoryList").innerHTML = visibleMemories.map(memory => `<div class="list-card memory-card"><div><strong>${memory.starred ? "★ " : ""}${escapeHtml(memory.title)}</strong><small>${kindLabels[memory.kind] || escapeHtml(memory.kind)} · 更新于 ${new Date(memory.updated_at).toLocaleString()}</small><p>${escapeHtml(memory.content.slice(0, 180))}</p></div><div><button data-memory-edit="${memory.id}">编辑</button><button data-memory-star="${memory.id}">${memory.starred ? "取消星标" : "星标"}</button><button data-memory-trash="${memory.id}">回收</button></div></div>`).join("") || `<p class="muted">没有符合条件的本地记忆。</p>`;
  document.querySelectorAll("[data-delete-provider]").forEach(b => b.onclick = async () => { await api(`/api/providers/${b.dataset.deleteProvider}`, { method: "DELETE" }); state.providers = state.providers.filter(p => p.id !== b.dataset.deleteProvider); if (state.provider === b.dataset.deleteProvider) state.provider = state.providers[0]?.id || null; renderSettings(); renderPickers(); });
  document.querySelectorAll("[data-edit-provider]").forEach(b=>b.onclick=()=>{const provider=state.providers.find(item=>item.id===b.dataset.editProvider),form=$("#providerForm"),notice=$("#providerEditState");if(!provider)return;document.querySelectorAll("[data-edit-provider]").forEach(button=>button.closest(".list-card")?.classList.toggle("editing",button===b));form.hidden=false;form.dataset.editing=provider.id;notice.hidden=false;notice.textContent=`正在编辑「${provider.name}」`;for(const name of ["name","protocol","base_url","model","custom_headers","temperature","top_p","max_tokens","vision_mode","cache_mode","prompt_cache_key"])if(form.elements[name])form.elements[name].value=provider[name]??({temperature:.7,top_p:1,max_tokens:4096,custom_headers:"{}",vision_mode:"auto",cache_mode:"auto",prompt_cache_key:""}[name]??"");form.elements.api_key.value="";form.elements.api_key.placeholder=provider.has_api_key?"已安全保存 · 留空继续使用":"尚未保存 Key";$("#providerKeyState").textContent=provider.has_api_key?"✓ 密钥已安全保存。为保护你不显示原文；留空保存、拉取模型和测试连接都会继续使用原密钥。":"这条线路还没有保存 Key，请填写后保存。";form.elements.prompt_cache.checked=!!provider.prompt_cache;form.elements.thinking_enabled.checked=provider.thinking_enabled!==false&&provider.thinking_enabled!==0;form.elements.stream_enabled.checked=provider.stream_enabled!==false&&provider.stream_enabled!==0;form.elements.enabled.checked=provider.enabled!==false&&provider.enabled!==0;$("#saveProviderCopy").hidden=false;$("#connectionState").textContent="已保存的 Key 会自动用于拉取模型、测试和另存模型";updateProviderCacheUI();requestAnimationFrame(()=>{const scroller=$(".settings-content");scroller.scrollTop=Math.max(0,form.offsetTop-18);form.elements.name.focus({preventScroll:true});});});
  document.querySelectorAll("[data-edit-persona]").forEach(b=>b.onclick=()=>openPersonaEditor(b.dataset.editPersona));
  document.querySelectorAll("[data-delete-persona]").forEach(b=>b.onclick=async()=>{const persona=state.personas.find(item=>item.id===b.dataset.deletePersona);if(!confirm(`删除人格“${persona.name}”？已绑定对话会切回默认人格。`))return;await api(`/api/personas/${persona.id}`,{method:"DELETE"});state.personas=state.personas.filter(item=>item.id!==persona.id);state.conversations.forEach(item=>{if(item.persona_id===persona.id)item.persona_id=null;});if(state.persona===persona.id)state.persona=state.personas[0]?.id||null;renderSettings();renderPickers();});
  document.querySelectorAll("[data-memory-star]").forEach(b => b.onclick = async () => { const memory = state.memories.find(item => item.id === b.dataset.memoryStar); Object.assign(memory, await api(`/api/memories/${memory.id}/state`, { method: "PATCH", body: JSON.stringify({ starred: !memory.starred }) })); renderSettings(); });
  document.querySelectorAll("[data-memory-edit]").forEach(b => b.onclick = () => { const memory = state.memories.find(item => item.id === b.dataset.memoryEdit); const form = $("#memoryForm"); form.dataset.editing = memory.id; form.elements.title.value = memory.title; form.elements.kind.value = memory.kind; form.elements.content.value = memory.content; $("#saveMemory").textContent = "保存修改"; $("#cancelMemoryEdit").hidden = false; form.scrollIntoView({ behavior: "smooth", block: "center" }); });
  document.querySelectorAll("[data-memory-trash]").forEach(b => b.onclick = async () => { const memory = state.memories.find(item => item.id === b.dataset.memoryTrash); if (!confirm(`将“${memory.title}”移入回收站？`)) return; await api(`/api/memories/${memory.id}/state`, { method: "PATCH", body: JSON.stringify({ trash: true }) }); state.memories = state.memories.filter(item => item.id !== memory.id); renderSettings(); });
  if ($("#emptyAddProvider")) $("#emptyAddProvider").onclick = () => { $("#providerForm").hidden = false; renderSettings(); };
}

function renderMessageTemplateSample(template,role,message,date=new Date()){const labels={user:"用户",assistant:"助手"};return String(template||"{{message}}").replace(/\{\{\s*(role|message|time|date)\s*\}\}/g,(_,key)=>({role:labels[role]||role,message,time:date.toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"}),date:date.toLocaleDateString("zh-CN")})[key]);}
function renderMessageTemplatePreview(){const form=$("#personaForm"),target=$("#messageTemplatePreview");if(!form||!target)return;const template=form.elements.message_template?.value||"{{message}}",now=new Date();target.innerHTML=`<div class="template-preview-line user"><span>用户 · ${escapeHtml(now.toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"}))}</span><div class="template-preview-bubble">${escapeHtml(renderMessageTemplateSample(template,"user","你好啊",now))}</div></div><div class="template-preview-line assistant"><span>助手 · ${escapeHtml(now.toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"}))}</span><div class="template-preview-bubble">${escapeHtml(renderMessageTemplateSample(template,"assistant","你好，有什么我可以帮你的吗？",now))}</div></div>`;}
function fillPersonaConfig(form,config={}){const tools=config.tools||{},selected=config.provider_id||"";form.elements.provider_id.innerHTML=`<option value="">尚未绑定</option>`+state.providers.map(provider=>`<option value="${provider.id}">${escapeHtml(provider.name)} · ${escapeHtml(provider.model)}</option>`).join("");form.elements.provider_id.value=state.providers.some(provider=>provider.id===selected)?selected:"";form.elements.startup_chat.value=config.startup_chat==="new"?"new":"resume";form.elements.pinned.checked=!!config.pinned;form.elements.memory_enabled.checked=config.memory_enabled!==false;form.elements.history_enabled.checked=config.history_enabled!==false;form.elements.summary_frequency.value=config.summary_frequency||20;form.elements.message_template.value=config.message_template||"{{message}}";form.elements.quick_phrases.value=(config.quick_phrases||[]).join("\n");form.elements.persona_headers.value=JSON.stringify(config.custom_headers||{},null,2);form.elements.persona_body.value=JSON.stringify(config.custom_body||{},null,2);form.elements.regex_rules.value=JSON.stringify(config.regex_rules||[],null,2);form.elements.tool_time.checked=tools.time!==false;form.elements.tool_clipboard.checked=!!tools.clipboard;form.elements.tool_tts.checked=!!tools.tts;form.elements.tool_ask_user.checked=tools.ask_user!==false;form.elements.tool_calculator.checked=tools.calculator!==false;form.elements.mcp_servers.value=(config.mcp_servers||[]).join("\n");renderMessageTemplatePreview();}
function personaConfigFromForm(form){let custom_headers,custom_body,regex_rules;try{custom_headers=JSON.parse(form.elements.persona_headers.value||"{}");custom_body=JSON.parse(form.elements.persona_body.value||"{}");regex_rules=JSON.parse(form.elements.regex_rules.value||"[]");}catch(error){throw new Error(`人格高级配置 JSON 格式错误：${error.message}`);}if(!custom_headers||Array.isArray(custom_headers)||typeof custom_headers!=="object")throw new Error("自定义 Header 必须是 JSON 对象");if(!custom_body||Array.isArray(custom_body)||typeof custom_body!=="object")throw new Error("自定义 Body 必须是 JSON 对象");if(!Array.isArray(regex_rules))throw new Error("正则规则必须是 JSON 数组");return {provider_id:form.elements.provider_id.value,startup_chat:form.elements.startup_chat.value,pinned:form.elements.pinned.checked,memory_enabled:form.elements.memory_enabled.checked,history_enabled:form.elements.history_enabled.checked,summary_frequency:Number(form.elements.summary_frequency.value||20),message_template:form.elements.message_template.value.trim()||"{{message}}",quick_phrases:form.elements.quick_phrases.value.split("\n").map(item=>item.trim()).filter(Boolean),custom_headers,custom_body,regex_rules,tools:{time:form.elements.tool_time.checked,clipboard:form.elements.tool_clipboard.checked,tts:form.elements.tool_tts.checked,ask_user:form.elements.tool_ask_user.checked,calculator:form.elements.tool_calculator.checked},mcp_servers:form.elements.mcp_servers.value.split("\n").map(item=>item.trim()).filter(Boolean)};}
function resetPersonaForm(){const form=$("#personaForm");form.reset();fillPersonaConfig(form,{});delete form.dataset.editing;$("#savePersona").textContent="保存人格";$("#cancelPersonaEdit").hidden=true;$("#personaSaveState").textContent="";$("#personaSaveState").classList.remove("error");}

function updateProviderCacheUI() {
  const mode = $("#providerCacheMode").value;
  $("#promptCacheControl").hidden = !["auto","anthropic"].includes(mode);
  $("#promptCacheKeyField").hidden = mode !== "openai";
  $("#automaticCacheHint").hidden = mode !== "auto";
}

function startupConversationPlan(persona,conversations){const mode=persona?.config?.startup_chat==="new"?"new":"resume",recent=conversations.find(item=>(item.persona_id||null)===(persona?.id||null));return {mode,conversationId:recent?.id||null};}

async function bootstrap() {
  Object.assign(state, await api("/api/bootstrap"));
  state.favorites = await api("/api/favorites");
  state.mcp_audit = await api("/api/mcp-audit");
  state.provider = state.providers[0]?.id || null;const storedPersona=localStorage.getItem("atherloom:last-persona");state.persona=state.personas.some(item=>item.id===storedPersona)?storedPersona:state.personas[0]?.id||null;state.memories=await api(`/api/memories?persona_key=${encodeURIComponent(memoryPersonaKey())}`);
  $("#autoTitleMode").value = state.settings.auto_title_mode || "local";
  $("#summaryEnabled").checked = state.settings.summary_enabled;
  $("#summaryRounds").value = state.settings.summary_trigger_rounds;
  $("#summaryRoundsValue").textContent = `${state.settings.summary_trigger_rounds} 轮`;
  $("#summaryPrompt").value = state.settings.summary_prompt;
  $("#summaryPrompt").dataset.defaultPrompt = state.settings.default_summary_prompt;
  $("#fontScale").value = state.settings.font_scale || 100;
  $("#fontScaleValue").textContent = `${state.settings.font_scale || 100}%`;
  $("#messageDensity").value = state.settings.message_density || "comfortable";
  $("#codeTheme").value = state.settings.code_theme || "auto";
  $("#streamSpeed").value = state.settings.stream_speed || "standard";
  $("#proactiveQuestions").checked = !!state.settings.proactive_questions;
  $("#typingPresenceEnabled").checked = state.settings.typing_presence_enabled!==false;
  $("#memoryStrategy").value = state.settings.memory_strategy || "hybrid";
  $("#vectorMemoryEnabled").checked = !!state.settings.vector_memory_enabled;
  $("#embeddingProvider").innerHTML = `<option value="">选择 API 线路</option>` + state.providers.filter(item=>item.protocol!=="anthropic").map(item=>`<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("");
  $("#embeddingProvider").value = state.settings.embedding_provider_id || "";
  $("#embeddingModel").value = state.settings.embedding_model || "";
  $("#visionProvider").innerHTML=`<option value="">跟随当前聊天线路</option>`+state.providers.filter(item=>item.vision_mode!=="text").map(item=>`<option value="${item.id}">${escapeHtml(item.name)} · ${escapeHtml(item.model)}</option>`).join("");
  $("#visionProvider").value=state.settings.vision_provider_id||"";
  $("#searchProvider").value=state.settings.search_provider||"builtin";
  $("#searchApiKey").value=state.settings.search_api_key||"";
  $("#searchEndpoint").value=state.settings.search_endpoint||"";
  updateSearchRouteFields();
  document.querySelectorAll("[data-permission]").forEach(select => select.value = state.settings.tool_permissions?.[select.dataset.permission] || "ask");
  applyAppearance();
  renderProfile(); renderTimeGreeting(); renderHistory(); renderSettings(); renderPickers(); renderMcpAudit(); updateMcpTransportFields(); await refreshVectorMemoryStatus();
  const startup=startupConversationPlan(activePersona(),state.conversations);
  if(startup.mode==="new"&&state.provider)await newConversation();else if(startup.conversationId)await openConversation(startup.conversationId);
  await loadUnreadStickyNotes();
}

function renderFavorites() {
  $("#favoriteList").innerHTML=state.favorites.map(item=>`<article class="favorite-card"><div class="favorite-meta"><span>${item.role==="user"?"用户":"助手"}</span><span>${escapeHtml(item.conversation_title_snapshot||"未命名对话")}</span><time>${new Date(item.original_message_created_at).toLocaleString()}</time></div><p>${escapeHtml(item.text_snapshot)}</p><button class="ghost" data-remove-favorite="${item.source_message_id}">取消珍藏</button></article>`).join("")||`<div class="game-empty"><span>☆</span><h3>还没有珍藏</h3><p>在任意消息下点击“☆ 珍藏”。</p></div>`;
  document.querySelectorAll("[data-remove-favorite]").forEach(button=>button.onclick=async()=>{await api(`/api/favorites/${button.dataset.removeFavorite}?owner=user`,{method:"DELETE"});state.favorites=await api(`/api/favorites?q=${encodeURIComponent($("#favoriteSearch").value)}`);renderFavorites();renderMessages();});
}

async function openFavorites(){state.favorites=await api("/api/favorites");renderFavorites();$("#favoritesSpace").hidden=false;}
function openMedia(mode){$("#mediaSpace").hidden=false;$("#readingRoom").hidden=mode!=="reading";$("#cinemaRoom").hidden=mode!=="cinema";$("#listeningRoom").hidden=mode!=="listening";$("#mediaTitle").textContent={reading:"一起读书",cinema:"一起看电影",listening:"一起听歌"}[mode]||"共同空间";}
const callState={active:false,recognition:null};
function callLine(role,text){$("#callTranscript").insertAdjacentHTML("beforeend",`<p class="${role}"><b>${role==="user"?"你":"AI"}</b>${escapeHtml(text)}</p>`);$("#callTranscript").scrollTop=$("#callTranscript").scrollHeight;}
async function callTurn(content){const provider=activeProvider();if(!provider)throw new Error("请先添加并选择 API 线路");if(!state.current)await newConversation();callLine("user",content);$("#callStatus").textContent="正在思考…";const response=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({conversation_id:state.current,content,provider_id:provider.id,persona_id:state.persona,local_time:localTimeContext()})});const reader=response.body.getReader(),decoder=new TextDecoder();let pending="",reply="";while(true){const {value,done}=await reader.read();if(done)break;pending+=decoder.decode(value,{stream:true});const lines=pending.split("\n");pending=lines.pop();for(const line of lines){if(!line)continue;const event=JSON.parse(line);if(event.error)throw new Error(event.error);if(event.delta)reply+=event.delta;}}callLine("assistant",reply);if(!callState.active)return;$("#callStatus").textContent="正在朗读…";const utterance=new SpeechSynthesisUtterance(reply);utterance.lang="zh-CN";utterance.onend=()=>{if(callState.active){$("#callStatus").textContent="正在听…";try{callState.recognition.start();}catch{}}};speechSynthesis.speak(utterance);}
async function startVoiceCall(){const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;if(!Recognition)throw new Error("当前浏览器没有系统语音识别能力");const stream=await navigator.mediaDevices.getUserMedia({audio:true});stream.getTracks().forEach(track=>track.stop());callState.active=true;callState.recognition=new Recognition();callState.recognition.lang="zh-CN";callState.recognition.interimResults=false;callState.recognition.onresult=event=>callTurn(event.results[event.results.length-1][0].transcript).catch(error=>{$("#callStatus").textContent=error.message;});callState.recognition.onerror=event=>{if(callState.active)$("#callStatus").textContent=`没有听清：${event.error}`;};callState.recognition.start();$("#callStatus").textContent="正在听…";$("#startCall").disabled=true;$("#endCall").disabled=false;}
function endVoiceCall(){callState.active=false;callState.recognition?.abort();speechSynthesis.cancel();$("#callStatus").textContent="通话已结束";$("#startCall").disabled=false;$("#endCall").disabled=true;}
function openVoiceCall(){if(window.AtherloomNative?.showNotice){window.AtherloomNative.showNotice("Android 测试版暂未接通稳定语音，已阻止会卡死的通话页。");return;}$("#callSpace").hidden=false;}

async function newConversation() {
  saveCurrentDraft();++state.navigation;
  const provider=activeProvider();if(!provider){openSettings("personas");throw new Error("请先为当前人格绑定专属模型线路");}
  const conversation = await api("/api/conversations", { method: "POST", body: JSON.stringify({ provider_id: provider.id, persona_id: state.persona }) });
  state.conversations.unshift(conversation); state.current = conversation.id; state.messages = [];state.message_cache.set(conversation.id,state.messages);
  if(state.persona)localStorage.setItem("atherloom:last-persona",state.persona);localStorage.setItem("atherloom:last-conversation",conversation.id);
  renderCurrentTitle();renderHistory();renderMessages();restoreCurrentDraft();renderInjectionTray();setChatStatus(localStorage.getItem(chatStatusKey())==="1");
}

async function openConversation(id) {
  if(id===state.current)return;saveCurrentDraft();const navigation=++state.navigation,conversation = state.conversations.find(c => c.id === id);if(!conversation)return;
  const messages=state.message_cache.has(id)?state.message_cache.get(id):await api(`/api/conversations/${id}/messages`);if(navigation!==state.navigation)return;
  state.current = id;
  state.provider = conversation.provider_id || state.provider; state.persona = conversation.persona_id || state.persona;
  if(state.persona)localStorage.setItem("atherloom:last-persona",state.persona);localStorage.setItem("atherloom:last-conversation",id);
  state.messages = messages;state.message_cache.set(id,messages);
  state.version_selection={};for(const message of state.messages)if(message.role==="assistant"&&message.parent_message_id&&message.selected){const versions=state.messages.filter(item=>item.role==="assistant"&&item.parent_message_id===message.parent_message_id);state.version_selection[message.parent_message_id]=versions.indexOf(message);}
  renderCurrentTitle();renderHistory();renderMessages();renderPickers();restoreCurrentDraft();renderInjectionTray();setChatStatus(localStorage.getItem(chatStatusKey())==="1");
}

async function sendMessage() {
  const input = $("#prompt"); const content = input.value.trim(); const provider = activeProvider();
  if (/^(我想|我要|想)?\s*玩(一下)?角色扮演[吧。！!？?]*$/u.test(content)) {
    input.value="";input.style.height="auto";updateComposerState();await openRoleplay();return;
  }
  if ((!content&&!state.attachments.length) || currentBusy()) return; if (!provider) return openSettings("providers"); if (!state.current) await newConversation();
  const attachments=state.attachments.splice(0);renderAttachments();const visibleContent=content||"请查看附件";
  input.value = "";input.style.height = "auto";localStorage.removeItem(draftKey(state.current));updateComposerState();
  state.messages.push({ role: "user", content:visibleContent,attachments }); renderMessages();
  await generateReply(visibleContent,null,attachments);
}

let streamScrollFrame=0,streamScrollDue=0,streamFollow=true;
function chatIsNearBottom(){const area=$("#chatScroll");return area.scrollHeight-area.scrollTop-area.clientHeight<96;}
function scheduleStreamingScroll(){if(!streamFollow)return;const now=performance.now();if(streamScrollFrame||now<streamScrollDue)return;streamScrollFrame=requestAnimationFrame(()=>{streamScrollFrame=0;streamScrollDue=performance.now()+120;if(streamFollow){const area=$("#chatScroll");area.scrollTop=area.scrollHeight;}});}
function updateStreamingMessage(message,messages=state.messages,conversationId=state.current) {
  if(conversationId!==state.current)return;const index=messages.indexOf(message),article=document.querySelector(`.message[data-index="${index}"]`);
  if(!article){renderMessages({stickToBottom:streamFollow});return;}
  const body=article.querySelector(".message-body"),bubble=article.querySelector(".bubble");
  if(message.memory_sources?.length){let sources=article.querySelector(".memory-sources");if(!sources){sources=document.createElement("div");sources.className="memory-sources";body.insertBefore(sources,body.firstChild);}sources.innerHTML=`本轮使用记忆：${message.memory_sources.map(source=>`<span>${escapeHtml(source.title)}</span>`).join("")}`;}
  if(message.reasoning){let thinking=article.querySelector(".thinking");if(!thinking){thinking=document.createElement("details");thinking.className="thinking";thinking.innerHTML="<summary>思考过程（点开查看）</summary><div></div>";body.insertBefore(thinking,bubble);}const reasoning=thinking.querySelector("div");if(reasoning.textContent!==message.reasoning)reasoning.textContent=message.reasoning;}
  if(bubble){if(message.role==="assistant"&&message.streaming){if(message.content){if(bubble.childNodes.length===1&&bubble.firstChild?.nodeType===Node.TEXT_NODE)bubble.firstChild.nodeValue=message.content;else bubble.replaceChildren(document.createTextNode(message.content));}}else bubble.innerHTML=message.role==="assistant"?renderAssistantContent(message.content):escapeHtml(message.content);}
  renderPickers();scheduleStreamingScroll();
}

function createStreamPresenter(message, animated, messages=state.messages, conversationId=state.current) {
  let queue=[],timer=null,ended=false,resolveFinished;
  const finishTimer=()=>{if(timer){clearInterval(timer);timer=null;}if(resolveFinished){resolveFinished();resolveFinished=null;}};
  const tick=()=>{if(!queue.length){if(ended)finishTimer();return;}const count=!animated?queue.length:1;message.content+=queue.splice(0,count).join("");message.pending=false;updateStreamingMessage(message,messages,conversationId);if(ended&&!queue.length)finishTimer();};
  return {
    push(text){if(!text)return;queue.push(...Array.from(text));if(!animated){tick();return;}if(!timer){tick();const delay={slow:90,standard:55,fast:30}[state.settings.stream_speed]||55;timer=setInterval(tick,delay);}},
    finish(){ended=true;tick();if(!timer&&!queue.length)return Promise.resolve();return new Promise(resolve=>{resolveFinished=resolve;});},
    cancel(){queue=[];ended=true;finishTimer();}
  };
}

async function generateReply(content, reuseUserMessageId = null, attachments = [], mediaContext = "") {
  const conversationId=state.current,messages=state.messages,provider=activeProvider(),personaId=state.persona,worldbookIds=selectedWorldbookIds(),controller=new AbortController();
  if (!provider) return openSettings("providers");
  if(reuseUserMessageId){
    for(let index=messages.length-1;index>=0;index--){
      const item=messages[index];
      if(item.role==="assistant"&&!item.id&&item.parent_message_id===reuseUserMessageId)messages.splice(index,1);
    }
  }
  state.generating.add(conversationId);state.generation_controllers.set(conversationId,controller);state.message_cache.set(conversationId,messages);streamFollow=true;renderHistory();renderCurrentTitle();updateComposerState();
  if(reuseUserMessageId)delete state.version_selection[reuseUserMessageId];
  messages.push({ role: "assistant", content: "", reasoning: "", model: provider.model, parent_message_id: reuseUserMessageId, pending: true, streaming: provider.stream_enabled!==false&&provider.stream_enabled!==0 });if(state.current===conversationId)renderMessages();
  const assistant = messages[messages.length - 1];
  let presenter;
  try {
    const gameContext=reuseUserMessageId?"":await prepareChatGameContext(content);
    const response = await fetch("/api/chat", { method: "POST",headers: { "Content-Type": "application/json" },signal:controller.signal,body: JSON.stringify({ conversation_id:conversationId,content: content || "重新生成",attachments,provider_id: provider.id,vision_provider_id:state.settings.vision_provider_id||"",persona_id:personaId,reuse_user_message_id:reuseUserMessageId,local_time: localTimeContext(),typing_context:reuseUserMessageId?"":consumeTypingContext(),game_context:gameContext,media_context:mediaContext,worldbook_ids:worldbookIds }) });
    if (!response.ok) {const detail=(await response.json().catch(()=>({}))).detail;throw new Error(formatHttpError(response.status,detail));}
    const reader=response.body.getReader(),decoder=new TextDecoder();presenter=createStreamPresenter(assistant,assistant.streaming,messages,conversationId);let pending="";
    while(true){const {value,done}=await reader.read();if(done)break;pending+=decoder.decode(value,{stream:true});const lines=pending.split("\n");pending=lines.pop();for(const line of lines){if(!line)continue;const event=JSON.parse(line);if(event.error)throw new Error(event.error);let structureUpdated=false;if(event.memory_sources){assistant.memory_sources=event.memory_sources;structureUpdated=true;}if(typeof event.delta==="string"&&event.delta!=="null")presenter.push(event.delta);if(typeof event.reasoning_delta==="string"&&event.reasoning_delta!=="null"){assistant.reasoning+=event.reasoning_delta;structureUpdated=true;}if(structureUpdated)updateStreamingMessage(assistant,messages,conversationId);if(event.done){await presenter.finish();assistant.pending=false;assistant.streaming=false;assistant.id=event.assistant_id;assistant.parent_message_id=event.user_id;const pendingUser=[...messages].reverse().find(m=>m.role==="user"&&!m.id);if(pendingUser)pendingUser.id=event.user_id;if(event.title){const conversation=state.conversations.find(c=>c.id===conversationId);if(conversation)conversation.title=event.title;}if(state.current===conversationId){renderCurrentTitle();renderMessages({stickToBottom:streamFollow});}renderHistory();}}}
  } catch (error) {presenter?.cancel();assistant.pending=false;assistant.streaming=false;if(error.name==="AbortError"){if(!assistant.content)assistant.content="已停止生成";}else{assistant.retry_content=content;assistant.retry_media_context=mediaContext;assistant.content=`生成未完成：${error.message}`;if(!reuseUserMessageId){try{const fresh=await api(`/api/conversations/${conversationId}/messages`),persisted=[...fresh].reverse().find(item=>item.role==="user"&&item.content===content);if(persisted)persisted.attachments=attachments;messages.splice(0,messages.length,...fresh,assistant);}catch{}}}if(state.current===conversationId)renderMessages({stickToBottom:streamFollow});}
  finally{state.generating.delete(conversationId);state.generation_controllers.delete(conversationId);renderHistory();if(state.current===conversationId){renderCurrentTitle();updateComposerState();state.memories=await api(`/api/memories?persona_key=${encodeURIComponent(memoryPersonaKey())}`).catch(()=>state.memories);renderSettings();}}
}

function motivationPersonaKey(){return state.persona||"__default__";}
function chatStatusKey(){return `atherloom:chat-status:${state.current||"new"}`;}
async function renderChatStatus(){
  const personaName=activePersonaName(),named=!!activePersona()?.name?.trim();$("#chatStatusTitle").textContent=named?`${personaName}的状态`:"当前人格状态";$("#chatStatusPersona").textContent=personaName;const provider=activeProvider();
  try{const motivation=await api(`/api/motivation/${encodeURIComponent(motivationPersonaKey())}`),drives=motivation.state?.drives||{},top=Object.entries(drives).sort((a,b)=>b[1]-a[1]).slice(0,3),labels=motivation.drives||{},worldbooks=selectedWorldbookIds().length;
    $("#chatStatusBody").innerHTML=[...top.map(([key,value])=>[labels[key]?.label||key,`${Number(value).toFixed(1)}/100`]),["心跳",`${motivation.state?.tick_count||0} 次`],["记忆",`${state.memories.length} 条`],["世界书",`${worldbooks} 本`],["MCP",`${state.mcp_servers.filter(item=>item.enabled).length} 个`],["模型",provider?.model||"未选择"],["输出",provider?.stream_enabled===false?"非流式":"流式"]].map(([name,value])=>`<div class="status-chip"><span>${escapeHtml(name)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  }catch(error){$("#chatStatusBody").innerHTML=`<p class="muted">${escapeHtml(error.message)}</p>`;}
}
async function setChatStatus(open){$("#chatStatusStrip").hidden=!open;localStorage.setItem(chatStatusKey(),open?"1":"0");if(open)await renderChatStatus();}
let stickyQueue=[],stickyIndex=0;
function showStickyNote(){if(stickyIndex>=stickyQueue.length){$("#stickyInbox").hidden=true;return;}const note=stickyQueue[stickyIndex];$("#stickyAuthor").textContent=`${note.persona_name} 留给你的话`;$("#stickyContent").textContent=note.content;$("#stickyCounter").textContent=`${stickyIndex+1} / ${stickyQueue.length} · ${new Date(note.created_at).toLocaleString()}`;$("#stickyNext").textContent=stickyIndex+1<stickyQueue.length?"下一张":"收好";$("#stickyReplyForm").hidden=true;$("#stickyReplyForm").reset();$("#stickyInbox").hidden=false;}
async function loadUnreadStickyNotes(){
  const seen=new Set(JSON.parse(localStorage.getItem("atherloom:seen-board-notes")||"[]")),personas=[{id:null,name:"当前人格"},...state.personas],results=await Promise.all(personas.map(async persona=>{try{const data=await api(`/api/board/${encodeURIComponent(persona.id||"__default__")}`);return (data.messages||[]).filter(item=>(item.author_role==="assistant"||item.author==="ai")&&!seen.has(item.id)).map(item=>({...item,persona_id:persona.id||"__default__",persona_name:persona.name}));}catch{return [];}}));stickyQueue=results.flat().sort((a,b)=>String(a.created_at).localeCompare(String(b.created_at))).slice(0,12);stickyIndex=0;if(stickyQueue.length)showStickyNote();
}
window.addEventListener("atherloom:scheduled-board-delivered",()=>loadUnreadStickyNotes());
async function loadInnerWriting(){
  const key=encodeURIComponent(motivationPersonaKey()),[journals,board,dreams]=await Promise.all([api(`/api/journals/${key}`),api(`/api/board/${key}`),api(`/api/dreams/${key}`)]);
  state.journals=journals.entries||[];state.board_messages=board.messages||[];state.dreams=dreams.entries||[];
  $("#journalPersonaName").textContent=activePersonaName();
  $("#journalSealed").hidden=!journals.sealed_count;$("#journalSealed").textContent=journals.sealed_count?`有 ${journals.sealed_count} 篇 AI 密封日记，仅 AI 可见。`:"";
  $("#boardSealed").hidden=!board.sealed_count;$("#boardSealed").textContent=board.sealed_count?`有 ${board.sealed_count} 条密封留言，仅 AI 可见。`:"";
  const spaces={user:"我的私人日记",shared:"共同日记",ai:"AI 私人日记"};
  $("#journalList").innerHTML=state.journals.map(item=>`<article class="journal-paper"><header><div><span>${spaces[item.space]||item.space} · ${item.author==="ai"?"AI 写":"我写"}</span><h4>${escapeHtml(item.title)}</h4></div><time>${new Date(item.updated_at).toLocaleString()}</time></header><p>${escapeHtml(item.content)}</p><footer><span>${item.visible_to_ai?"AI 可读":"不提供给 AI"} · ${item.visible_to_user?"我可读":"对我密封"}</span><div><button data-edit-journal="${item.id}">编辑</button><button data-delete-journal="${item.id}">删除</button></div></footer></article>`).join("")||`<p class="muted">还没有日记。你可以先写一篇只属于自己的，也可以邀请 AI 一起写。</p>`;
  $("#boardList").innerHTML=state.board_messages.map(item=>{const fromPersona=item.author_role==="assistant"||item.author==="ai",authorName=fromPersona?(item.author==="ai"?(activePersona()?.name||"当前人格"):item.author):"我的留言";return `<article class="board-note ${fromPersona?"from-ai":"from-user"}"><span>${escapeHtml(fromPersona?`${authorName}的留言`:authorName)}</span><p>${escapeHtml(item.content)}</p><footer>${new Date(item.created_at).toLocaleString()} · ${item.visible_to_ai?"当前人格可读":"不提供给当前人格"} <button data-delete-board="${item.id}">移除</button></footer></article>`;}).join("")||`<p class="muted">留言板还是空的。</p>`;
  $("#dreamList").innerHTML=state.dreams.map(item=>`<article class="journal-paper dream-paper ${item.kind==="quarantined"?"quarantined":""}"><header><div><span>${item.kind==="quarantined"?"隔离梦境":item.claimed?"已认领的梦":"未认领"}</span><h4>${escapeHtml(item.title)}</h4></div><time>${new Date(item.created_at).toLocaleString()}</time></header><p>${escapeHtml(item.raw_text)}</p>${item.necropsy?`<blockquote>${escapeHtml(item.necropsy)}</blockquote>`:""}<footer><span>${escapeHtml(item.summary||"")}</span>${item.claimed?"":`<button data-claim-dream="${item.id}">认领这场梦</button>`}</footer></article>`).join("")||`<p class="muted">梦库还很安静。聊过一些话以后，可以请当前人格做一场梦。</p>`;
  document.querySelectorAll("[data-edit-journal]").forEach(button=>button.onclick=()=>{const item=state.journals.find(row=>row.id===button.dataset.editJournal),form=$("#journalForm");form.dataset.editing=item.id;form.elements.title.value=item.title;form.elements.content.value=item.content;form.elements.space.value=item.space;form.elements.visible_to_user.checked=!!item.visible_to_user;form.elements.visible_to_ai.checked=!!item.visible_to_ai;$("#cancelJournalEdit").hidden=false;form.scrollIntoView({behavior:"smooth",block:"start"});});
  document.querySelectorAll("[data-delete-journal]").forEach(button=>button.onclick=async()=>{if(!confirm("删除这篇日记？"))return;await api(`/api/journals/${key}/${button.dataset.deleteJournal}`,{method:"DELETE"});loadInnerWriting();});
  document.querySelectorAll("[data-delete-board]").forEach(button=>button.onclick=async()=>{await api(`/api/board/${key}/${button.dataset.deleteBoard}`,{method:"DELETE"});loadInnerWriting();});
  document.querySelectorAll("[data-claim-dream]").forEach(button=>button.onclick=async()=>{const note=prompt("认领后想留下什么注记？","我愿意记住这场梦。");if(note===null)return;await api(`/api/dreams/${key}/${button.dataset.claimDream}/claim`,{method:"POST",body:JSON.stringify({note})});loadInnerWriting();});
}
function wireBoardReplies(){
  const notes=Array.from(document.querySelectorAll("#boardList .board-note.from-ai")), sources=state.board_messages.filter(item=>item.author==="ai"||item.author_role==="assistant");
  notes.forEach((note,index)=>{
    if(note.querySelector(".board-inline-reply"))return;
    const source=sources[index];if(!source)return;
    const button=document.createElement("button");button.type="button";button.textContent="回复";button.className="board-reply-button";
    const form=document.createElement("form");form.className="board-inline-reply";form.hidden=true;
    const textarea=document.createElement("textarea");textarea.rows=2;textarea.placeholder="回复这条留言……";textarea.required=true;
    const actions=document.createElement("div"),cancel=document.createElement("button"),submit=document.createElement("button");cancel.type="button";cancel.textContent="取消";submit.type="submit";submit.textContent="送出回复";actions.append(cancel,submit);form.append(textarea,actions);
    const footer=note.querySelector("footer");if(footer)footer.prepend(button);note.append(form);
    button.onclick=()=>{form.hidden=!form.hidden;if(!form.hidden)textarea.focus();};cancel.onclick=()=>{form.hidden=true;};
    form.onsubmit=async event=>{event.preventDefault();const content=textarea.value.trim();if(!content)return;submit.disabled=true;try{const key=encodeURIComponent(motivationPersonaKey()),saved=await api("/api/board/"+key,{method:"POST",body:JSON.stringify({content,author:"user",visible_to_user:true,visible_to_ai:true,reply_to:source.id})});if(!saved?.id)throw new Error("留言板没有返回已保存的回复");await loadInnerWriting();}catch(error){alert("回复没有保存："+error.message);submit.disabled=false;}};
  });
}
new MutationObserver(wireBoardReplies).observe($("#boardList"),{childList:true});
let motivationBackgroundTimer;
function syncMotivationBackground(){
  clearInterval(motivationBackgroundTimer);motivationBackgroundTimer=null;
  if(!$("#motivationBackground")?.checked)return;
  motivationBackgroundTimer=setInterval(async()=>{if(!$("#motivationEnabled")?.checked)return;try{await api("/api/motivation/"+encodeURIComponent(motivationPersonaKey())+"/tick",{method:"POST",body:"{}"});if($("#settingsPanel")?.classList.contains("open"))renderRuntimePanel();}catch{}},5*60*1000);
}
async function renderRuntimePanel(){
  const provider=activeProvider(),worldbooks=selectedWorldbookIds().map(id=>state.worldbooks.find(item=>item.id===id)).filter(Boolean),lastAssistant=[...state.messages].reverse().find(item=>item.role==="assistant");
  $("#pluginOverview").innerHTML=[["记忆系统",activePersona()?.config?.memory_enabled===false?"已停用":`运行中 · ${state.memories.length} 条记忆`,"memory","忆"],["欲望系统",$("#motivationEnabled").checked?"已启用":"可按人格启用","desire","欲"],["日记",state.journals.length?`${state.journals.length} 篇可见`:"私人或共同书写","journal","记"],["留言板",state.board_messages.length?`${state.board_messages.length} 条可见`:"给彼此留句话","board","笺"],["梦库",state.dreams.length?`${state.dreams.length} 场梦`:"等待第一场梦","dream","梦"],["MCP",state.mcp_servers.length?`${state.mcp_servers.filter(item=>item.enabled).length} 个已启用`:"尚未添加","mcp","M"]].map(([name,status,kind,glyph])=>`<article class="plugin-card" data-plugin="${kind}"><span class="plugin-glyph">${glyph}</span><div><strong>${name}</strong><small>${status}</small></div><i></i></article>`).join("");
  document.querySelectorAll("#pluginOverview [data-plugin]").forEach(card=>{card.tabIndex=0;card.onclick=()=>{if(card.dataset.plugin==="memory")switchTab("memory");else if(["journal","board","dream"].includes(card.dataset.plugin)){switchTab("journal");document.querySelector(`[data-inner-space="${card.dataset.plugin}"]`)?.click();}else if(card.dataset.plugin==="mcp")switchTab("mcp");else card.scrollIntoView({behavior:"smooth",block:"center"});};card.onkeydown=event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();card.click();}};});
  const capabilities=provider?`${provider.vision_mode==="text"?"仅文本":provider.vision_mode==="anthropic"?"Claude 图片":provider.vision_mode==="openai"?"OpenAI 图片":"自动图片"} · ${provider.thinking_enabled!==false?"可显示思考":"隐藏思考"} · ${provider.stream_enabled!==false?"流式":"非流式"} · 缓存 ${provider.cache_mode||"auto"}`:"尚未选择线路";
  $("#requestDiagnostic").innerHTML=`<div class="list-card"><div><strong>线路能力</strong><small>${escapeHtml(capabilities)}</small></div></div><div class="list-card"><div><strong>注入来源</strong><small>世界书 ${worldbooks.length} 本 · 本轮记忆 ${lastAssistant?.memory_sources?.length||0} 条 · 附件 ${state.attachments.length} 个</small></div></div><div class="list-card"><div><strong>人格与历史</strong><small>${escapeHtml(activePersonaName())} · ${activePersona()?.config?.history_enabled===false?"仅当前消息":"参考聊天历史"}</small></div></div>`;
  const duplicates=state.memories.length-new Set(state.memories.map(item=>`${item.kind}:${item.title.trim().toLowerCase()}:${item.content.trim().toLowerCase()}`)).size,brokenProviders=state.providers.filter(item=>!item.has_api_key||!item.model).length,brokenMcp=state.mcp_servers.filter(item=>item.enabled&&item.last_status&&item.last_status!=="online").length;
  $("#dataHealth").innerHTML=[["记忆",duplicates?`${duplicates} 条可能重复`:"未发现完全重复"],["API 线路",brokenProviders?`${brokenProviders} 条缺少 Key 或模型`:"配置完整"],["MCP",brokenMcp?`${brokenMcp} 个连接异常`:"未发现已知异常"]].map(([name,detail])=>`<div class="list-card"><div><strong>${name}</strong><small>${detail}</small></div></div>`).join("");
  try{const data=await api(`/api/motivation/${encodeURIComponent(motivationPersonaKey())}`);$("#motivationEnabled").checked=!!data.enabled;const labels=data.drives||{};$("#motivationPanel").innerHTML=Object.entries(data.state?.drives||{}).map(([key,value])=>`<div class="drive-cell"><span>${escapeHtml(labels[key]?.label||key)}</span><strong>${Number(value).toFixed(1)}</strong><i style="--drive:${Math.max(0,Math.min(100,Number(value)))}%"></i></div>`).join("")+`<p class="muted drive-meta">心跳 ${data.state?.tick_count||0} 次 · ${data.state?.last_tick?new Date(data.state.last_tick).toLocaleString():"尚未运行"}</p>`;}catch(error){$("#motivationPanel").innerHTML=`<p class="muted">欲望状态读取失败：${escapeHtml(error.message)}</p>`;}
  $("#motivationOfflineMode").value=localStorage.getItem(`atherloom:motivation-offline:${motivationPersonaKey()}`)||"limited";$("#motivationBackground").checked=localStorage.getItem(`atherloom:motivation-background:${motivationPersonaKey()}`)==="1";syncMotivationBackground();
}
function openSettings(tab = "providers") {
  for(const id of ["gameLibrary","mediaSpace","favoritesSpace","roleplaySpace","callSpace","stickyInbox","messageMenu","messageEditor","instructionPicker","worldbookEntryEditor"]){const layer=$("#"+id);if(layer)layer.hidden=true;}
  $("#backdrop").hidden = false; $("#settingsPanel").classList.add("open"); $("#settingsPanel").setAttribute("aria-hidden", "false"); switchTab(tab);
}
function closeSettings() { $("#settingsPanel").classList.remove("open"); $("#settingsPanel").setAttribute("aria-hidden", "true"); $("#backdrop").hidden = true; }
function switchTab(tab) { document.querySelectorAll(".settings-nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab)); document.querySelectorAll(".tab").forEach(s => s.classList.toggle("active", s.id === `tab-${tab}`));if(tab==="runtime")renderRuntimePanel();if(tab==="journal")loadInnerWriting(); }
function closePopovers() { document.querySelectorAll(".popover").forEach(popover => { popover.hidden = true; }); }
function showPopover(target, popover, items, select) {
  const wasOpen = !popover.hidden; closePopovers(); if (wasOpen) return;
  const rect = target.getBoundingClientRect(); popover.innerHTML = items || `<button type="button" data-close-popover>暂无可选项 · 点击关闭</button>`; popover.hidden = false;
  popover.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - 270))}px`;
  if (rect.top < innerHeight / 2) { popover.style.top = `${rect.bottom + 8}px`; popover.style.bottom = "auto"; }
  else { popover.style.top = "auto"; popover.style.bottom = `${innerHeight - rect.top + 8}px`; }
  popover.querySelectorAll("button[data-value]").forEach(b => b.onclick = () => { select(b.dataset.value); closePopovers(); });
  popover.querySelector("[data-close-popover]")?.addEventListener("click", closePopovers);
}

async function renameCurrentConversation() {
  if (!state.current) return;
  const current = state.conversations.find(c => c.id === state.current);
  const title = window.prompt("重命名对话", current?.title || "新对话");
  if (!title?.trim()) return;
  const saved = await api(`/api/conversations/${state.current}`, { method: "PATCH", body: JSON.stringify({ title: title.trim() }) });
  current.title = saved.title;renderCurrentTitle();renderHistory();
}

function openConversationSwitcher(event) {
  event.stopPropagation();
  const recent = state.conversations.filter(item => !item.archived).slice(0, 30);
  const items = `<button data-value="__new__"><strong>＋ 新对话</strong></button><p class="conversation-longpress-hint">长按任意对话约 0.7 秒可删除</p>${recent.map(item => `<div class="conversation-switch-row"><button data-value="${item.id}" class="${item.id === state.current ? "active" : ""}"><strong><span>${escapeHtml(item.title)}</span>${generationDot(item.id)}</strong><small>${item.id === state.current ? "当前对话 · 长按删除" : `${new Date(item.updated_at || item.created_at).toLocaleString("zh-CN")} · 长按删除`}</small></button><button type="button" class="conversation-switch-delete" data-delete-switch="${item.id}" aria-label="删除 ${escapeHtml(item.title)}">×</button></div>`).join("")}<button data-value="__rename__" ${state.current ? "" : "disabled"}>重命名当前对话</button>`;
  showPopover(event.currentTarget, $("#conversationPopover"), items, async value => {
    if (value === "__new__") await newConversation();
    else if (value === "__rename__") await renameCurrentConversation();
    else await openConversation(value);
  });
  $("#conversationPopover").querySelectorAll("[data-delete-switch]").forEach(button=>button.onclick=async event=>{event.preventDefault();event.stopPropagation();button.disabled=true;await updateHistoryState(button.dataset.deleteSwitch,"delete",{skipConfirm:true});});
  bindConversationLongPress($("#conversationPopover"));
}

function bindConversationLongPress(popover){
  popover.querySelectorAll('.conversation-switch-row button[data-value]').forEach(button=>{let timer=null;const cancel=()=>{clearTimeout(timer);timer=null;button.classList.remove("longpress-armed");};button.addEventListener("pointerdown",event=>{if(event.button!==undefined&&event.button!==0)return;cancel();button.classList.add("longpress-armed");timer=setTimeout(async()=>{timer=null;button.dataset.longPressFired="1";button.classList.remove("longpress-armed");navigator.vibrate?.(35);await updateHistoryState(button.dataset.value,"delete",{skipConfirm:true});},700);});for(const type of ["pointerup","pointercancel"])button.addEventListener(type,cancel);button.addEventListener("click",event=>{if(button.dataset.longPressFired==="1"){event.preventDefault();event.stopImmediatePropagation();delete button.dataset.longPressFired;}},true);});
}

function shareConversation() {
  if (!state.messages.length) return;
  const title = state.conversations.find(c => c.id === state.current)?.title || "对话分享";
  const visible = state.messages.map(m => `## ${m.role === "user" ? "用户" : "助手"}\n\n${m.content}`).join("\n\n---\n\n");
  const blob = new Blob([`# ${title}\n\n${visible}\n`], { type: "text/markdown;charset=utf-8" });
  const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `${title.replace(/[\\/:*?\"<>|]/g, "-")}.md`; link.click(); URL.revokeObjectURL(link.href);
}

function exportLocalBackup() {
  const data = {},selected=new Set([...document.querySelectorAll("[data-backup-part]:checked")].map(item=>item.dataset.backupPart)),partForKey=key=>key.includes("messages:")||key.includes("conversations")||key.includes("versions:")?"conversations":key.includes("personas")||key.includes("worldbooks")?"personas":key.includes("memories")||key.includes("motivation:")?"memory":key.includes("game:")?"games":"settings";
  for (let index = 0; index < localStorage.length; index++) {
    const key = localStorage.key(index);
    if (!key?.startsWith("atherloom:")) continue;
    if(!selected.has(partForKey(key)))continue;
    if (key === "atherloom:providers") {
      try { data[key] = JSON.stringify(JSON.parse(localStorage.getItem(key)).map(({ api_key, ...provider }) => provider)); } catch { /* skip malformed provider data */ }
    } else data[key] = localStorage.getItem(key);
  }
  const bundle = { format: "atherloom-backup", version: 1, exported_at: new Date().toISOString(), data };
  const content=JSON.stringify(bundle,null,2),fileName=`atherloom-backup-${new Date().toISOString().replace(/[:.]/g,"-")}.json`;
  if(window.AtherloomNative?.saveBackup){
    try{
      const result=JSON.parse(window.AtherloomNative.saveBackup(fileName,content));
      if(!result.ok)throw new Error(result.error||"系统没有返回保存结果");
      $("#backupState").textContent=`备份成功：${result.location}（API Key 未包含）`;
      return;
    }catch(error){
      $("#backupState").textContent=`备份失败：${error.message}`;
      return;
    }
  }
  const blob = new Blob([content], { type: "application/json;charset=utf-8" });
  const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = fileName; link.click(); setTimeout(()=>URL.revokeObjectURL(link.href),1000);
  $("#backupState").textContent = `已交给浏览器下载：${fileName}。具体位置请查看浏览器下载记录；API Key 未包含。`;
}

async function restoreLocalBackup(file) {
  const bundle = JSON.parse(await file.text());
  if (bundle?.format !== "atherloom-backup" || bundle.version !== 1 || !bundle.data) throw new Error("不是有效的 Atherloom 备份文件");
  if (!confirm("恢复会替换当前本机的 Atherloom 数据，确定继续吗？")) return;
  [...Array(localStorage.length)].map((_, index) => localStorage.key(index)).filter(key => key?.startsWith("atherloom:")).forEach(key => localStorage.removeItem(key));
  for (const [key, value] of Object.entries(bundle.data)) if (key.startsWith("atherloom:") && typeof value === "string") localStorage.setItem(key, value);
  $("#backupState").textContent = "恢复完成，正在重新载入…"; setTimeout(() => location.reload(), 500);
}

let settingsSaveTimer;
function updateSearchRouteFields(){const provider=$("#searchProvider").value,keyField=$("#searchApiKeyField"),endpointField=$("#searchEndpointField"),help=$("#searchRouteHelp");keyField.hidden=provider==="builtin";endpointField.hidden=provider!=="custom";help.textContent={builtin:"免费线路无需 Key；实时性与覆盖面受公开索引限制。",tavily:"需要 Tavily API Key；适合给 AI 提供带链接的实时网页结果。",brave:"需要 Brave Search API Key；使用独立网页索引并返回标题、链接与摘要。",custom:"向自定义地址 POST {query,max_results}；可用 Bearer Key，响应需包含 results 数组。"}[provider];}
function appSettingsPayload(){
  return {auto_title_mode:$("#autoTitleMode").value,summary_enabled:$("#summaryEnabled").checked,summary_trigger_rounds:Number($("#summaryRounds").value),summary_prompt:$("#summaryPrompt").value,display_name:$("#displayName").value.trim(),proactive_questions:$("#proactiveQuestions").checked,typing_presence_enabled:$("#typingPresenceEnabled").checked,font_scale:Number($("#fontScale").value),message_density:$("#messageDensity").value,code_theme:$("#codeTheme").value,memory_strategy:$("#memoryStrategy").value,vector_memory_enabled:$("#vectorMemoryEnabled").checked,embedding_provider_id:$("#embeddingProvider").value,embedding_model:$("#embeddingModel").value.trim(),vision_provider_id:$("#visionProvider").value,search_provider:$("#searchProvider").value,search_api_key:$("#searchApiKey").value.trim(),search_endpoint:$("#searchEndpoint").value.trim(),stream_speed:$("#streamSpeed").value,tool_permissions:Object.fromEntries([...document.querySelectorAll("[data-permission]")].map(select=>[select.dataset.permission,select.value]))};
}
async function refreshVectorMemoryStatus(){
  try{const status=await api(`/api/memories/vector/status?persona_key=${encodeURIComponent(memoryPersonaKey())}`);$("#vectorMemoryStatus").textContent=status.total?`已索引 ${status.indexed}/${status.total}${status.stale?` · ${status.stale} 条待更新`:""}`:"当前人格记忆库为空";return status;}catch(error){$("#vectorMemoryStatus").textContent=`状态读取失败：${error.message}`;return null;}
}
async function rebuildMemoryVectors(){
  await persistAppSettingsNow();const button=$("#rebuildMemoryVectors");button.disabled=true;$("#vectorMemoryStatus").textContent="正在建立向量索引…";
  try{const result=await api("/api/memories/vector/rebuild",{method:"POST",body:JSON.stringify({provider_id:$("#embeddingProvider").value,model:$("#embeddingModel").value.trim(),persona_key:memoryPersonaKey()})});$("#vectorMemoryStatus").textContent=`已索引 ${result.indexed}/${result.total} · ${result.dimensions||0} 维`;}catch(error){$("#vectorMemoryStatus").textContent=`重建失败：${error.message}`;}finally{button.disabled=false;}
}
async function persistAppSettingsNow(){
  clearTimeout(settingsSaveTimer);state.settings=await api("/api/settings",{method:"PUT",body:JSON.stringify(appSettingsPayload())});applyAppearance();renderProfile();renderTimeGreeting();$("#summarySaveState").textContent="已保存到本地";$("#toolSaveState").textContent="AI 工具权限已保存并生效";return state.settings;
}
function saveAppSettings() {
  clearTimeout(settingsSaveTimer);
  $("#summarySaveState").textContent = "等待保存…";
  settingsSaveTimer = setTimeout(async () => {
    const tool_permissions = Object.fromEntries([...document.querySelectorAll("[data-permission]")].map(select => [select.dataset.permission, select.value]));
    state.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify({
      auto_title_mode: $("#autoTitleMode").value,
      summary_enabled: $("#summaryEnabled").checked,
      summary_trigger_rounds: Number($("#summaryRounds").value),
      summary_prompt: $("#summaryPrompt").value,
      display_name: $("#displayName").value.trim(),
      proactive_questions: $("#proactiveQuestions").checked,
      typing_presence_enabled: $("#typingPresenceEnabled").checked,
      font_scale: Number($("#fontScale").value),
      message_density: $("#messageDensity").value,
      code_theme: $("#codeTheme").value,
      memory_strategy: $("#memoryStrategy").value,
      vector_memory_enabled: $("#vectorMemoryEnabled").checked,
      embedding_provider_id: $("#embeddingProvider").value,
      embedding_model: $("#embeddingModel").value.trim(),
      vision_provider_id: $("#visionProvider").value,
      search_provider: $("#searchProvider").value,
      search_api_key: $("#searchApiKey").value.trim(),
      search_endpoint: $("#searchEndpoint").value.trim(),
      stream_speed: $("#streamSpeed").value,
      tool_permissions
    }) });
    applyAppearance();
    renderProfile();
    renderTimeGreeting();
    $("#summarySaveState").textContent = "已保存到本地";
    $("#toolSaveState").textContent = "已保存到本地";
  }, 350);
}

$("#prompt").addEventListener("input", e => {const now=Date.now(),hasText=!!e.target.value.trim();if(hasText){if(!typingSession.startedAt)typingSession.startedAt=now;typingSession.lastAt=now;typingSession.keystrokes++;}else if(typingSession.hadText&&typingSession.startedAt&&now-typingSession.startedAt>3000)localStorage.setItem(typingAbandonedKey(),"1");typingSession.hadText=hasText;e.target.style.height = "auto"; e.target.style.height = `${Math.min(e.target.scrollHeight, 180)}px`;saveCurrentDraft();updateComposerState();renderContextUsage(); });
$("#chatScroll").addEventListener("scroll",()=>{if(currentBusy())streamFollow=chatIsNearBottom();},{passive:true});
function syncKeyboardViewport(){const viewport=window.visualViewport;if(!viewport)return;const offset=Math.max(0,window.innerHeight-viewport.height-viewport.offsetTop);document.documentElement.style.setProperty("--keyboard-offset",`${Math.round(offset)}px`);if(offset&&document.activeElement===$("#prompt"))requestAnimationFrame(()=>$("#prompt").scrollIntoView({block:"nearest"}));}
window.visualViewport?.addEventListener("resize",syncKeyboardViewport);
window.visualViewport?.addEventListener("scroll",syncKeyboardViewport);
window.addEventListener("orientationchange",syncKeyboardViewport);
syncKeyboardViewport();
$("#attachmentButton").onclick=event=>{event.stopPropagation();$("#attachmentMenu").hidden=!$("#attachmentMenu").hidden;};document.querySelectorAll("[data-attachment-source]").forEach(button=>button.onclick=()=>{const inputs={camera:$("#cameraInput"),images:$("#imageInput"),files:$("#fileInput")};$("#attachmentMenu").hidden=true;inputs[button.dataset.attachmentSource].click();});[$("#cameraInput"),$("#imageInput"),$("#fileInput")].forEach(input=>input.onchange=async event=>{await addAttachments(event.target.files);event.target.value="";$("#send").disabled=false;});
function roleplayProviderOptions(selected=""){return state.providers.map(provider=>`<option value="${provider.id}" ${provider.id===selected?"selected":""}>${escapeHtml(provider.name)} · ${escapeHtml(provider.model)}</option>`).join("");}
function roleplayPersonaOptions(selected=""){return `<option value="">不绑定人格</option>`+state.personas.map(persona=>`<option value="${persona.id}" ${persona.id===selected?"selected":""}>${escapeHtml(persona.name)}</option>`).join("");}
function renderRoleplayProse(value){return String(value||"").split(/\n+/).filter(Boolean).map(line=>{const match=line.match(/^([^：:\n]{1,24})[：:]\s*([\s\S]*)$/);if(!match)return `<div class="roleplay-speaker legacy"><b>旧稿·未署名</b><p>${escapeHtml(line)}</p></div>`;const speaker=match[1].trim(),kind=speaker==="旁白"?" narration":"";return `<div class="roleplay-speaker${kind}"><b>${escapeHtml(speaker)}</b><p>${escapeHtml(match[2])}</p></div>`;}).join("");}
function addRoleplayCastRow(actor={}){
  const row=document.createElement("div");row.className="roleplay-cast-row";
  row.innerHTML=`<input data-cast-name maxlength="80" required placeholder="角色姓名" value="${escapeHtml(actor.name||"")}"><select data-cast-provider required>${roleplayProviderOptions(actor.provider_id||"")}</select><input data-cast-description maxlength="6000" placeholder="角色设定" value="${escapeHtml(actor.description||"")}"><button type="button" aria-label="移除角色">×</button><select data-cast-persona hidden>${roleplayPersonaOptions(actor.persona_id||"")}</select>`;
  row.querySelector("button").onclick=()=>{if($("#roleplayCastRows").children.length>1)row.remove();};
  $("#roleplayCastRows").append(row);
}
function resetRoleplaySetup(){
  const form=$("#roleplaySetup");form.reset();form.hidden=false;$("#roleplayStage").hidden=true;
  $("#roleplaySetupStatus").textContent="";$("#roleplaySetupStatus").className="";
  delete form.dataset.editing;
  form.elements.narrator_provider_id.innerHTML=roleplayProviderOptions();$("#roleplayCastRows").innerHTML="";addRoleplayCastRow();
  $("#roleplayWorldbooks").innerHTML=state.worldbooks.length?state.worldbooks.filter(book=>book.enabled!==false).map(book=>`<label><input type="checkbox" value="${book.id}"><span>${escapeHtml(book.name)}</span></label>`).join(""):`<span class="muted">还没有世界书；可以先在设置中创建。</span>`;
  applyRoleplayPreset("ancient",form);
}
function applyRoleplayPreset(name,form=$("#roleplaySetup")){const field=form.elements.premise;if(!field)return;const labels={ancient:"古风",modern:"现代",mystery:"悬疑",fantasy:"幻想",custom:"自定"},next=roleplayPresets[name]||"",base=field.value.replace(/\n*\n?【预设风格·[^】]+】[^\n]*/g,"").trim();field.value=name==="custom"?base:[base,`【预设风格·${labels[name]}】${next}`].filter(Boolean).join("\n\n");form.dataset.presetText=next;const status=$("#roleplaySetupStatus");if(status){status.textContent=name==="custom"?"已切换为自定：只保留你的核心设定":`已切换为${labels[name]}：风格约束已加入故事设定`;status.className="";}}
function renderRoleplayStories(){
  $("#roleplayStoryList").innerHTML=roleplayState.stories.length?roleplayState.stories.map(story=>`<button class="roleplay-story-card ${story.id===roleplayState.current?.id?"active":""}" data-roleplay-story="${story.id}"><strong>${escapeHtml(story.title)}</strong><small>${escapeHtml(story.player_name)} · ${story.state?.turn_number||0} 回合${story.status==="completed"?" · 已收场":""}</small></button>`).join(""):`<p class="muted">还没有故事。新建一个，名字由你决定。</p>`;
  document.querySelectorAll("[data-roleplay-story]").forEach(button=>button.onclick=()=>loadRoleplayStory(button.dataset.roleplayStory));
}
function renderRoleplayBackstage(){const story=roleplayState.current,log=$("#roleplayBackstageLog");if(!story||!log)return;log.innerHTML=(story.backstage||[]).length?story.backstage.map(item=>`<article class="${item.role==="user"?"from-author":"from-ai"}${item.pending?" pending":""}"><b>${item.role==="user"?"作者":activePersonaName()}</b><p>${escapeHtml(item.content)}</p></article>`).join(""):`<p class="roleplay-backstage-empty">幕布后面还很安静。可以直接问人物动机、下一幕走向，或要求调整写法。</p>`;requestAnimationFrame(()=>log.scrollTop=log.scrollHeight);}
function renderRoleplayStage(){
  const story=roleplayState.current;if(!story)return;
  $("#roleplaySetup").hidden=true;$("#roleplayStage").hidden=false;$("#roleplayTitle").textContent=story.title;$("#roleplayPlayer").textContent=`你是 ${story.player_name} · 登场：${story.cast.map(actor=>actor.name).join("、")||"尚未填写"}`;$("#roleplayPremise").textContent=`核心设定：${story.premise?.trim()||"尚未保存，请先打开故事设置补充"}`;$("#roleplayPremise").classList.toggle("missing",!story.premise?.trim());
  const selectedBooks=new Set(story.worldbook_ids||story.state?.worldbook_ids||[]),bookNames=state.worldbooks.filter(book=>selectedBooks.has(book.id)).map(book=>book.name);$("#roleplayBindings").textContent=`预设：${({ancient:"古风",modern:"现代",mystery:"悬疑",fantasy:"幻想",custom:"自定"})[story.preset||story.state?.preset]||"未设置"} · 世界书：${bookNames.join("、")||"未绑定"}`;
  $("#roleplayTurnLabel").textContent=roleplayState.busy?(roleplayState.phase||"正在生成…"):story.turns.length?(story.state?.turn_number?`停在第 ${story.state.turn_number} 回合`:"旁白已经开场"):"正在准备开场";
  $("#finishRoleplay").textContent=story.status==="completed"?"重新开场":"收场";
  $("#roleplayTurnForm").hidden=story.status==="completed"||roleplayState.busy||!story.turns.length;$("#retryRoleplayOpening").hidden=roleplayState.busy||story.turns.length>0;$("#clearRoleplayStory").hidden=roleplayState.busy||!story.turns.length;$("#regenerateRoleplayOpening").hidden=roleplayState.busy||!story.turns.length;
  $("#roleplaySummary p").textContent=story.state?.rolling_summary||"旁白会在每一回合后更新这里。";
  $("#roleplayManuscript").innerHTML=story.turns.length?story.turns.map(turn=>`<section class="roleplay-scene" data-turn="${turn.turn_number===0?"开场":`第 ${turn.turn_number} 回合`}">${turn.turn_number===0?"":`<p class="roleplay-player-line">${escapeHtml(story.player_name)}：${escapeHtml(turn.player_input)}</p>`}<div class="roleplay-prose">${renderRoleplayProse(turn.prose)}</div><footer class="roleplay-scene-actions"><button type="button" data-roleplay-favorite="${turn.turn_number}">${turn.checkpoint?.favorite?"★ 已收藏":"☆ 收藏"}</button><button type="button" data-roleplay-reroll="${turn.turn_number}">↻ 重 Roll</button><button type="button" class="danger" data-roleplay-delete="${turn.turn_number}">删除</button></footer></section>`).join(""):`<p class="roleplay-empty">${roleplayState.busy?"旁白正在铺开第一幕……":"开场没有生成成功。检查线路后可以重试。"}</p>`;
  document.querySelectorAll("[data-roleplay-favorite]").forEach(button=>button.onclick=()=>toggleRoleplayFavorite(Number(button.dataset.roleplayFavorite)));
  document.querySelectorAll("[data-roleplay-reroll]").forEach(button=>button.onclick=()=>rerollRoleplayTurn(Number(button.dataset.roleplayReroll)));
  document.querySelectorAll("[data-roleplay-delete]").forEach(button=>button.onclick=()=>deleteRoleplayTurn(Number(button.dataset.roleplayDelete)));
  renderRoleplayStories();
}
async function loadRoleplayStory(id){roleplayState.current=await api(`/api/roleplay/stories/${id}`);renderRoleplayStage();renderRoleplayBackstage();}
async function requestRoleplayOpening(mode="ai",prose=""){const story=roleplayState.current;if(!story||roleplayState.busy)return;roleplayState.busy=true;roleplayState.phase=mode==="manual"?"正在保存你的开场…":"旁白正在写开场…";$("#roleplayStatus").textContent=roleplayState.phase;renderRoleplayStage();try{const opening=await api(`/api/roleplay/stories/${story.id}/opening${mode==="manual"?"/manual":""}`,{method:"POST",body:JSON.stringify(mode==="manual"?{prose}:{}),timeout:150000});if(!story.turns.some(turn=>turn.turn_number===0))story.turns.push(opening);story.state={...story.state,scene:opening.checkpoint.scene,rolling_summary:`开场：${opening.prose}`};$("#roleplayStatus").textContent="开场已经保存";}catch(error){$("#roleplayStatus").textContent=`开场失败：${error.message}`;}finally{roleplayState.busy=false;roleplayState.phase="";renderRoleplayStage();}}
async function toggleRoleplayFavorite(turnNumber){const story=roleplayState.current,turn=story?.turns.find(item=>item.turn_number===turnNumber);if(!turn)return;turn.checkpoint={...turn.checkpoint,favorite:!turn.checkpoint?.favorite};try{await api(`/api/roleplay/stories/${story.id}/turns/${turnNumber}`,{method:"PATCH",body:JSON.stringify({favorite:turn.checkpoint.favorite})});renderRoleplayStage();}catch(error){turn.checkpoint.favorite=!turn.checkpoint.favorite;alert(error.message);}}
async function truncateRoleplayFrom(turnNumber){const story=roleplayState.current,result=await api(`/api/roleplay/stories/${story.id}/truncate/${turnNumber}`,{method:"DELETE"});story.turns=result.turns;story.state=result.state;return result;}
async function deleteRoleplayTurn(turnNumber){const story=roleplayState.current;if(!story||roleplayState.busy)return;const later=story.turns.filter(turn=>turn.turn_number>turnNumber).length,message=later?`删除这一幕会同时撤回后面的 ${later} 回合，继续吗？`:`删除${turnNumber===0?"开场":`第 ${turnNumber} 回合`}？`;if(!confirm(message))return;await truncateRoleplayFrom(turnNumber);$("#roleplayStatus").textContent=turnNumber===0?"开场已删除，可以重新选择写法":"已撤回到上一幕";renderRoleplayStage();}
async function rerollRoleplayTurn(turnNumber){const story=roleplayState.current,turn=story?.turns.find(item=>item.turn_number===turnNumber);if(!turn||roleplayState.busy)return;const later=story.turns.filter(item=>item.turn_number>turnNumber).length;if(!confirm(later?`重写这一幕会同时撤回后面的 ${later} 回合，继续吗？`:`重新生成${turnNumber===0?"开场":`第 ${turnNumber} 回合`}？`))return;roleplayState.busy=true;roleplayState.phase=turnNumber===0?"正在撤回旧开场…":`正在撤回第 ${turnNumber} 回合…`;renderRoleplayStage();try{await truncateRoleplayFrom(turnNumber);roleplayState.busy=false;if(turnNumber===0){await requestRoleplayOpening("ai");return;}roleplayState.busy=true;roleplayState.phase=`角色正在重写第 ${turnNumber} 回合…`;renderRoleplayStage();const replacement=await api(`/api/roleplay/stories/${story.id}/turns`,{method:"POST",body:JSON.stringify({player_input:turn.player_input,fast_reroll:true}),timeout:180000});story.turns.push(replacement);story.state={...story.state,turn_number:replacement.turn_number,scene:replacement.checkpoint.scene,rolling_summary:replacement.checkpoint.rolling_summary||story.state.rolling_summary};$("#roleplayStatus").textContent=`第 ${turnNumber} 回合已经重写`;}catch(error){$("#roleplayStatus").textContent=`重 Roll 失败：${error.message}`;await loadRoleplayStory(story.id);}finally{roleplayState.busy=false;roleplayState.phase="";renderRoleplayStage();}}
async function openRoleplay(){
  if(!state.providers.length){openSettings("providers");return;}
  $("#roleplaySpace").hidden=false;setSidebar(false);
  roleplayState.stories=await api("/api/roleplay/stories");renderRoleplayStories();
  resetRoleplaySetup();
  $(".roleplay-desk").scrollTop=0;
}
$("#roleplaySetup").onsubmit=async event=>{
  event.preventDefault();const form=event.target,button=form.querySelector(".roleplay-open-curtain"),status=$("#roleplaySetupStatus"),cast=[...$("#roleplayCastRows").children].map(row=>({name:row.querySelector("[data-cast-name]").value.trim(),provider_id:row.querySelector("[data-cast-provider]").value,persona_id:row.querySelector("[data-cast-persona]").value||null,description:row.querySelector("[data-cast-description]").value.trim()})),worldbook_ids=[...$("#roleplayWorldbooks input:checked")].map(input=>input.value),preset=form.elements.preset.value,editing=form.dataset.editing;
  const missing=!form.elements.title.value.trim()?"故事名称":!form.elements.player_name.value.trim()?"你的角色姓名":!form.elements.premise.value.trim()?"故事设定":!form.elements.narrator_provider_id.value?"旁白线路":cast.some(actor=>!actor.name)?"登场角色姓名":cast.some(actor=>!actor.provider_id)?"角色线路":"";if(missing){status.textContent=`请补充：${missing}`;status.className="error";return;}button.disabled=true;button.textContent=editing?"正在保存…":"正在建立故事…";status.textContent=editing?"正在把核心设定写入当前故事…":"正在保存故事设置…";status.className="";
  try{const opening_mode=form.elements.opening_mode.value,opening_text=form.elements.opening_text.value.trim();if(!editing&&opening_mode==="manual"&&!opening_text)throw new Error("选择“我来写”后，请先写下开场白");const payload={title:form.elements.title.value.trim(),player_name:form.elements.player_name.value.trim(),premise:form.elements.premise.value.trim(),preset,worldbook_ids,narrator_provider_id:form.elements.narrator_provider_id.value,cast,opening_mode};roleplayState.current=await api(editing?`/api/roleplay/stories/${editing}`:"/api/roleplay/stories",{method:editing?"PUT":"POST",body:JSON.stringify(payload)});if(editing){const index=roleplayState.stories.findIndex(item=>item.id===editing);if(index>=0)roleplayState.stories[index]=roleplayState.current;renderRoleplayStage();$("#roleplayStatus").textContent="故事设置已保存，从下一回合生效";}else{roleplayState.stories.unshift(roleplayState.current);renderRoleplayStage();await requestRoleplayOpening(opening_mode,opening_text);}}catch(error){status.textContent=`保存失败：${error.message}`;status.className="error";}finally{button.disabled=false;button.textContent=editing?"保存故事设置":"保存并开场";}
};
$("#roleplayTurnForm").onsubmit=async event=>{
  event.preventDefault();if(roleplayState.busy)return;const input=$("#roleplayInput"),content=input.value.trim();if(!content||!roleplayState.current)return;
  roleplayState.busy=true;event.target.querySelector("button").disabled=true;$("#roleplayStatus").textContent="角色们正在各自回应，旁白随后落笔…";
  try{const turn=await api(`/api/roleplay/stories/${roleplayState.current.id}/turns`,{method:"POST",body:JSON.stringify({player_input:content}),timeout:360000});roleplayState.current.turns.push(turn);roleplayState.current.state={...roleplayState.current.state,turn_number:turn.turn_number,scene:turn.checkpoint.scene,rolling_summary:turn.checkpoint.rolling_summary||roleplayState.current.state.rolling_summary};input.value="";$("#roleplayStatus").textContent=`已保存第 ${turn.turn_number} 回合与停场位置`;renderRoleplayStage();}catch(error){$("#roleplayStatus").textContent=`续写失败：${error.message}`;}finally{roleplayState.busy=false;event.target.querySelector("button").disabled=false;renderRoleplayStage();}
};
$("#finishRoleplay").onclick=async()=>{const story=roleplayState.current;if(!story)return;const status=story.status==="completed"?"active":"completed";roleplayState.current={...story,...await api(`/api/roleplay/stories/${story.id}/state`,{method:"PUT",body:JSON.stringify({status})})};roleplayState.current.turns=story.turns;renderRoleplayStage();};
$("#retryRoleplayOpening").onclick=()=>requestRoleplayOpening("ai");
$("#clearRoleplayStory").onclick=async()=>{const story=roleplayState.current;if(!story||roleplayState.busy||!confirm("清空当前故事的全部正文和回合？人物、世界书、线路与剧情设定会保留。"))return;roleplayState.busy=true;roleplayState.phase="正在收起全部手稿…";renderRoleplayStage();try{await truncateRoleplayFrom(0);$("#roleplayStatus").textContent="舞台已经清空，可以自己写或生成新开场";}catch(error){$("#roleplayStatus").textContent=`清屏失败：${error.message}`;}finally{roleplayState.busy=false;roleplayState.phase="";renderRoleplayStage();}};
$("#regenerateRoleplayOpening").onclick=async()=>{const story=roleplayState.current;if(!story||roleplayState.busy||!confirm("清空现有正文，并让旁白重新生成开场白？"))return;roleplayState.busy=true;roleplayState.phase="正在撤下旧手稿…";renderRoleplayStage();try{await truncateRoleplayFrom(0);roleplayState.busy=false;roleplayState.phase="";await requestRoleplayOpening("ai");}catch(error){$("#roleplayStatus").textContent=`重新开场失败：${error.message}`;roleplayState.busy=false;roleplayState.phase="";renderRoleplayStage();}};
$("#openRoleplayBackstage").onclick=()=>{$("#roleplayBackstage").hidden=false;renderRoleplayBackstage();$("#roleplayBackstageForm textarea").focus();};
$("#closeRoleplayBackstage").onclick=()=>$("#roleplayBackstage").hidden=true;
async function sendRoleplayBackstage(content){const story=roleplayState.current,form=$("#roleplayBackstageForm"),button=form.querySelector("button");if(!story||!content||button.disabled)return;if(!story.premise?.trim()){$("#roleplayBackstageStatus").textContent="核心设定还没保存：请先打开“故事设置”补充，避免 AI 胡编";return;}const requestEpoch=++roleplayState.backstageEpoch,saved=[...(story.backstage||[])];story.backstage=[...saved,{role:"user",content},{role:"assistant",content:"正在核对核心设定、人物名单和最近正文…",pending:true}];renderRoleplayBackstage();button.disabled=true;button.textContent="回应中";$("#roleplayBackstageStatus").textContent=`${activePersonaName()}正在核对已验证故事档案…`;try{const result=await api(`/api/roleplay/stories/${story.id}/backstage`,{method:"POST",body:JSON.stringify({content,provider_id:activeProvider()?.id,persona_id:state.persona}),timeout:150000});if(requestEpoch!==roleplayState.backstageEpoch)return;story.backstage=result.backstage;renderRoleplayBackstage();$("#roleplayBackstageStatus").textContent="这段讨论只留在幕后，不进入正文";}catch(error){if(requestEpoch!==roleplayState.backstageEpoch)return;story.backstage=saved;renderRoleplayBackstage();$("#roleplayBackstageStatus").textContent=`讨论失败：${error.message}`;}finally{if(requestEpoch===roleplayState.backstageEpoch){button.disabled=false;button.textContent="发送";}}}
$("#roleplayBackstageForm").onsubmit=async event=>{event.preventDefault();const input=event.target.querySelector("textarea"),content=input.value.trim();if(!content)return;input.value="";await sendRoleplayBackstage(content);};
$("#clearRoleplayBackstage").onclick=async()=>{const story=roleplayState.current;if(!story||!story.backstage?.length||!confirm("清空这个故事的全部幕后讨论？小说正文不会改变。"))return;++roleplayState.backstageEpoch;story.backstage=[];renderRoleplayBackstage();const button=$("#roleplayBackstageForm button");button.disabled=false;button.textContent="发送";$("#roleplayBackstageStatus").textContent="幕后讨论已经清空，旧请求不会再恢复";try{await api(`/api/roleplay/stories/${story.id}/backstage/clear`,{method:"DELETE"});}catch(error){$("#roleplayBackstageStatus").textContent=`本地已清空；保存失败：${error.message}`;}};
$("#rerollRoleplayBackstage").onclick=async()=>{const story=roleplayState.current;if(!story||!story.backstage?.some(item=>item.role==="user"))return alert("还没有可以重 Roll 的幕后提问");const result=await api(`/api/roleplay/stories/${story.id}/backstage/last`,{method:"DELETE"});story.backstage=result.backstage;renderRoleplayBackstage();await sendRoleplayBackstage(result.content);};
$("#editRoleplayStory").onclick=()=>{const story=roleplayState.current;if(!story)return;resetRoleplaySetup();const form=$("#roleplaySetup");form.dataset.editing=story.id;form.elements.title.value=story.title;form.elements.player_name.value=story.player_name;form.elements.premise.value=story.premise||"";form.elements.narrator_provider_id.value=story.narrator_provider_id;form.elements.opening_mode.value=story.opening_mode||"ai";const preset=story.preset||story.state?.preset||"custom";form.elements.preset.value=preset;form.dataset.presetText=roleplayPresets[preset]||"";const ids=new Set(story.worldbook_ids||story.state?.worldbook_ids||[]);document.querySelectorAll("#roleplayWorldbooks input").forEach(input=>input.checked=ids.has(input.value));$("#roleplayCastRows").innerHTML="";story.cast.forEach(addRoleplayCastRow);form.querySelector(".roleplay-open-curtain").textContent="保存故事设置";};
window.addEventListener("atherloom:roleplay-progress",event=>{if(event.detail?.storyId===roleplayState.current?.id){roleplayState.phase=event.detail.phase;$("#roleplayStatus").textContent=event.detail.phase;renderRoleplayStage();}});
document.querySelectorAll('#roleplaySetup [name="preset"]').forEach(input=>input.onchange=()=>applyRoleplayPreset(input.value));
$("#showRoleplaySummary").onclick=()=>{$("#roleplaySummary").hidden=!$("#roleplaySummary").hidden;};$("#closeRoleplaySummary").onclick=()=>$("#roleplaySummary").hidden=true;
$("#exportRoleplayTxt").onclick=()=>{const story=roleplayState.current;if(!story)return;const cast=story.cast.map(actor=>actor.name).join("、"),chapters=story.turns.map(turn=>`${turn.turn_number===0?"【开场】":`【第 ${turn.turn_number} 回合】`}${turn.checkpoint?.favorite?" ★ 收藏":""}\n${turn.turn_number===0?"":`${story.player_name}：${turn.player_input}\n\n`}${turn.prose}`).join("\n\n"),text=`《${story.title}》\n\n玩家角色：${story.player_name}\n登场角色：${cast}\n故事设定：${story.premise}\n\n【自动剧情档案】\n${story.state?.rolling_summary||"暂无"}\n\n${chapters}\n`,blob=new Blob([text],{type:"text/plain;charset=utf-8"}),url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download=`${story.title.replace(/[\\\\/:*?"<>|]/g,"_")}.txt`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$("#newRoleplayStory").onclick=resetRoleplaySetup;$("#addRoleplayCast").onclick=()=>addRoleplayCastRow();$("#openRoleplay").onclick=openRoleplay;$("#closeRoleplay").onclick=()=>{$("#roleplayBackstage").hidden=true;$("#roleplaySpace").hidden=true;};
function toggleSidebarHub(button,panel){const open=panel.hidden;document.querySelectorAll(".sidebar-hub-panel").forEach(item=>item.hidden=true);document.querySelectorAll(".sidebar-hub-toggle").forEach(item=>item.setAttribute("aria-expanded","false"));panel.hidden=!open;button.setAttribute("aria-expanded",String(open));}
$("#toggleCreateHub").onclick=()=>toggleSidebarHub($("#toggleCreateHub"),$("#createHubPanel"));$("#toggleWritingHub").onclick=()=>toggleSidebarHub($("#toggleWritingHub"),$("#writingHubPanel"));
function openWritingHub(space){openSettings("journal");const button=document.querySelector(`[data-inner-space="${space}"]`);button?.click();if(innerWidth<=760)setSidebar(false);}
$("#openJournalHub").onclick=()=>openWritingHub("journal");$("#openBoardHub").onclick=()=>openWritingHub("board");
$("#openDreamHub").onclick=()=>openWritingHub("dream");
["openReading","openCinema","openListening","openRoleplay"].forEach(id=>document.getElementById(id)?.addEventListener("click",()=>{if(innerWidth<=760)setSidebar(false);}));
$("#openGamesFromComposer").onclick=()=>{$("#attachmentMenu").hidden=true;openGameLibrary();};
$("#openInstructionInjection").onclick=()=>{$("#attachmentMenu").hidden=true;openInstructionPicker();};
$("#closeInstructionPicker").onclick=()=>{saveSelectedWorldbooks([...$("#instructionBookList").querySelectorAll('input:checked')].map(input=>input.value));$("#instructionPicker").hidden=true;};
$("#instructionPicker").onclick=event=>{if(event.target===$("#instructionPicker"))$("#instructionPicker").hidden=true;};
$("#addWorldbook").onclick=()=>openWorldbookForm();$("#cancelWorldbook").onclick=()=>$("#worldbookForm").hidden=true;$("#addWorldbookEntry").onclick=()=>openWorldbookEntryEditor();
$("#exportWorldbooks").onclick=()=>{const blob=new Blob([JSON.stringify({format:"atherloom-worldbooks",version:1,worldbooks:state.worldbooks},null,2)],{type:"application/json"}),link=document.createElement("a");link.href=URL.createObjectURL(blob);link.download=`atherloom-worldbooks-${new Date().toISOString().slice(0,10)}.json`;link.click();URL.revokeObjectURL(link.href);};
$("#importWorldbooks").onclick=()=>$("#worldbookImportFile").click();$("#worldbookImportFile").onchange=async event=>{const file=event.target.files?.[0];event.target.value="";if(!file)return;try{const bundle=JSON.parse(await file.text());if(bundle?.format!=="atherloom-worldbooks"||!Array.isArray(bundle.worldbooks))throw new Error("不是有效的 Atherloom 世界书文件");for(const book of bundle.worldbooks){const payload={name:String(book.name||"导入的世界书"),description:String(book.description||""),enabled:book.enabled!==false,entries:Array.isArray(book.entries)?book.entries:[]},saved=await api("/api/worldbooks",{method:"POST",body:JSON.stringify(payload)});state.worldbooks.unshift(saved);}renderWorldbooks();}catch(error){alert(`导入失败：${error.message}`);}};
$("#worldbookForm").onsubmit=async event=>{event.preventDefault();const form=event.target,payload={name:form.elements.name.value.trim(),description:form.elements.description.value.trim(),enabled:form.elements.enabled.checked,entries:editingWorldbookEntries},id=form.dataset.editing,saved=await api(id?`/api/worldbooks/${id}`:"/api/worldbooks",{method:id?"PUT":"POST",body:JSON.stringify(payload)});if(id)Object.assign(state.worldbooks.find(book=>book.id===id),saved);else state.worldbooks.unshift(saved);form.hidden=true;renderWorldbooks();renderInjectionTray();};
$("#cancelWorldbookEntry").onclick=()=>$("#worldbookEntryEditor").hidden=true;$("#worldbookEntryEditor").onclick=event=>{if(event.target===$("#worldbookEntryEditor"))$("#worldbookEntryEditor").hidden=true;};
$("#worldbookEntryEditor form").onsubmit=event=>{event.preventDefault();const form=event.target,index=Number(form.dataset.entryIndex),entry={id:index>=0?editingWorldbookEntries[index].id:crypto.randomUUID(),name:form.elements.name.value.trim(),content:form.elements.content.value,enabled:form.elements.enabled.checked,constant:form.elements.constant.checked,keywords:form.elements.keywords.value.split("\n").map(item=>item.trim()).filter(Boolean),use_regex:form.elements.use_regex.checked,case_sensitive:form.elements.case_sensitive.checked,scan_depth:Number(form.elements.scan_depth.value||4),position:form.elements.position.value,role:form.elements.role.value,priority:Number(form.elements.priority.value||0)};if(index>=0)editingWorldbookEntries[index]=entry;else editingWorldbookEntries.push(entry);$("#worldbookEntryEditor").hidden=true;renderWorldbookEntries();};
function mcpPayloadFromForm(form){let env,headers;try{env=JSON.parse(form.elements.env.value||"{}");headers=JSON.parse(form.elements.headers.value||"{}");}catch(error){throw new Error(`MCP JSON 格式错误：${error.message}`);}const existing=state.mcp_servers.find(item=>item.id===form.dataset.editing);return {name:form.elements.name.value.trim(),transport:form.elements.transport.value,url:form.elements.url.value.trim(),token:form.elements.token.value,command:form.elements.command.value.trim(),args:form.elements.args.value.split("\n").map(item=>item.trim()).filter(Boolean),env,headers,tool_policies:existing?.tool_policies||{},enabled:form.elements.enabled.checked};}
$("#mcpServerForm").onsubmit=async event=>{event.preventDefault();try{const form=event.target,payload=mcpPayloadFromForm(form),id=form.dataset.editing,saved=await api(id?`/api/mcp-servers/${id}`:"/api/mcp-servers",{method:id?"PUT":"POST",body:JSON.stringify(payload)});if(id)Object.assign(state.mcp_servers.find(item=>item.id===id),saved);else state.mcp_servers.unshift(saved);resetMcpForm();renderMcpServers();$("#mcpStatusLabel").textContent=`已保存 ${saved.name}`;$("#mcpStatusDetail").textContent="点击刷新工具以完成验证";}catch(error){alert(error.message);}};
$("#testMcpServer").onclick=async()=>{const form=$("#mcpServerForm");if(!form.reportValidity())return;const status=$("#mcpStatusDetail");$("#mcpStatusLabel").textContent="正在连接 MCP…";status.textContent="初始化并读取工具列表";$("#mcpStatusDot").classList.remove("online");try{const existing=state.mcp_servers.find(item=>item.id===form.dataset.editing),payload=mcpPayloadFromForm(form);if(existing?.has_token&&!payload.token&&payload.transport==="http"){ $("#mcpStatusLabel").textContent="请重新输入令牌后测试";status.textContent="已保存令牌不会回传到页面；也可保存后直接点“刷新”";return;}const result=await api("/api/mcp-servers/test",{method:"POST",timeout:40000,body:JSON.stringify(payload)});$("#mcpStatusDot").classList.add("online");$("#mcpStatusLabel").textContent=result.message;status.textContent=result.tools.length?result.tools.map(tool=>tool.name).join("、"):"服务当前没有暴露工具";}catch(error){$("#mcpStatusLabel").textContent="MCP 连接失败";status.textContent=error.message;}};
$("#cancelMcpEdit").onclick=resetMcpForm;
$("#mcpTransport").onchange=updateMcpTransportFields;
$("#exportMcpServers").onclick=()=>{const clean=state.mcp_servers.map(({id,has_token,last_status,last_detail,last_tested_at,created_at,updated_at,env_keys,tools,...server})=>({...server,token:"",env:{}})),blob=new Blob([JSON.stringify({format:"atherloom-mcp",version:1,servers:clean},null,2)],{type:"application/json"}),link=document.createElement("a");link.href=URL.createObjectURL(blob);link.download=`atherloom-mcp-${new Date().toISOString().slice(0,10)}.json`;link.click();URL.revokeObjectURL(link.href);};
$("#importMcpServers").onclick=()=>$("#mcpImportFile").click();$("#mcpImportFile").onchange=async event=>{const file=event.target.files?.[0];event.target.value="";if(!file)return;try{const bundle=JSON.parse(await file.text());if(bundle?.format!=="atherloom-mcp"||!Array.isArray(bundle.servers))throw new Error("不是有效的 Atherloom MCP 配置");for(const server of bundle.servers){const saved=await api("/api/mcp-servers",{method:"POST",body:JSON.stringify({...server,token:"",enabled:server.enabled!==false})});state.mcp_servers.unshift(saved);}renderMcpServers();}catch(error){alert(`导入失败：${error.message}`);}};
// 聊天输入框中的 Enter 始终换行；只有可见的发送按钮会发送消息。
$("#send").onclick = ()=>currentBusy()?stopCurrentGeneration():sendMessage(); $("#newChat").onclick = newConversation;
$("#titleButton").onclick = openConversationSwitcher;
let searchTimer;
$("#conversationSearch").oninput = event => { clearTimeout(searchTimer); searchTimer = setTimeout(async () => { const query = event.target.value.trim(); if (!query) { const fresh = await api("/api/bootstrap"); state.conversations = fresh.conversations; } else { state.conversations = await api(`/api/search?q=${encodeURIComponent(query)}`); } renderHistory(); }, 180); };
$("#autoTitleMode").onchange = saveAppSettings;
$("#summaryEnabled").onchange = saveAppSettings;
$("#proactiveQuestions").onchange = saveAppSettings;
$("#typingPresenceEnabled").onchange = ()=>{saveAppSettings();updateTypingPresence();};
$("#searchProvider").onchange=()=>{updateSearchRouteFields();saveAppSettings();};
$("#searchApiKey").onchange=saveAppSettings;
$("#searchEndpoint").onchange=saveAppSettings;
$("#summaryRounds").oninput = event => { $("#summaryRoundsValue").textContent = `${event.target.value} 轮`; saveAppSettings(); };
$("#summaryPrompt").oninput = saveAppSettings;
$("#displayName").oninput = saveAppSettings;
$("#fontScale").oninput = event => { $("#fontScaleValue").textContent = `${event.target.value}%`; state.settings.font_scale = Number(event.target.value); applyAppearance(); saveAppSettings(); };
$("#messageDensity").onchange = event => { state.settings.message_density = event.target.value; applyAppearance(); saveAppSettings(); };
$("#streamSpeed").onchange=event=>{state.settings.stream_speed=event.target.value;saveAppSettings();};
$("#codeTheme").onchange = event => { state.settings.code_theme = event.target.value; applyAppearance(); saveAppSettings(); };
$("#memoryStrategy").onchange = saveAppSettings;
$("#vectorMemoryEnabled").onchange = saveAppSettings;
$("#embeddingProvider").onchange = saveAppSettings;
$("#embeddingModel").oninput = saveAppSettings;
$("#visionProvider").onchange = saveAppSettings;
$("#rebuildMemoryVectors").onclick = rebuildMemoryVectors;
$("#resetSummaryPrompt").onclick = () => { $("#summaryPrompt").value = $("#summaryPrompt").dataset.defaultPrompt; saveAppSettings(); };
function insertTemplateVariable(target,value){if(!target)return;target.focus();const start=target.selectionStart??target.value.length,end=target.selectionEnd??start;target.setRangeText(value,start,end,"end");target.dispatchEvent(new Event("input",{bubbles:true}));}
document.querySelectorAll("[data-message-template-variable]").forEach(button=>button.onclick=()=>{insertTemplateVariable($("#personaForm").elements.message_template,button.dataset.messageTemplateVariable);renderMessageTemplatePreview();});
$("#personaForm").elements.message_template.oninput=renderMessageTemplatePreview;
document.querySelectorAll("[data-summary-variable]").forEach(button=>button.onclick=()=>insertTemplateVariable($("#summaryPrompt"),button.dataset.summaryVariable));
document.querySelectorAll("[data-permission]").forEach(select => select.onchange = saveAppSettings);
$("#enableAiTools").onclick = () => {
  for (const name of ["web_search", "file_read", "memory_read", "memory_write", "diary_write"]) {
    const select = document.querySelector(`[data-permission="${name}"]`);
    if (select) select.value = "allow";
  }
  $("#toolSaveState").textContent = "正在开启联网、文件、记忆和日记工具…";
  persistAppSettingsNow().catch(error=>{$("#toolSaveState").textContent=`保存失败：${error.message}`;});
};
document.querySelectorAll("[data-bulk-permission]").forEach(button => button.onclick = () => {
  const permission = button.dataset.bulkPermission;
  document.querySelectorAll("[data-permission]").forEach(select => {
    select.value = select.dataset.permission === "delete" && permission === "allow" ? "ask" : permission;
  });
  saveAppSettings();
});
$("#openSettings").onclick = () => {
  setSidebar(false);
  openSettings("appearance");
  requestAnimationFrame(() => {
    $(".settings-content").scrollTop = 0;
    $("#displayName").focus({ preventScroll: true });
  });
};
$("#openGames").onclick = openGameLibrary; $("#closeGames").onclick = () => $("#gameLibrary").hidden = true;
$("#openFavorites").onclick=openFavorites;$("#closeFavorites").onclick=()=>$("#favoritesSpace").hidden=true;
$("#openReading").onclick=()=>openMedia("reading");$("#openCinema").onclick=()=>openMedia("cinema");$("#openListening").onclick=()=>openMedia("listening");$("#closeMedia").onclick=()=>{$("#mediaSpace").hidden=true;$("#moviePlayer").pause();$("#musicPlayer").pause();};
$("#toggleChatStatus").onclick=()=>setChatStatus($("#chatStatusStrip").hidden);$("#closeChatStatus").onclick=()=>setChatStatus(false);
$("#stickyNext").onclick=()=>{const note=stickyQueue[stickyIndex],seen=new Set(JSON.parse(localStorage.getItem("atherloom:seen-board-notes")||"[]"));if(note)seen.add(note.id);localStorage.setItem("atherloom:seen-board-notes",JSON.stringify([...seen].slice(-500)));stickyIndex++;showStickyNote();};
$("#stickyReply").onclick=()=>{$("#stickyReplyForm").hidden=false;$("#stickyReplyForm").elements.content.focus();};
$("#cancelStickyReply").onclick=()=>{$("#stickyReplyForm").hidden=true;};
$("#stickyReplyForm").onsubmit=async event=>{event.preventDefault();const form=event.target,note=stickyQueue[stickyIndex],content=form.elements.content.value.trim(),personaKey=note?.persona_id||"__default__";if(!note||!content)return;const submit=form.querySelector('[type="submit"]');submit.disabled=true;try{const saved=await api(`/api/board/${encodeURIComponent(personaKey)}`,{method:"POST",body:JSON.stringify({content,author:"user",author_role:"user",visible_to_user:true,visible_to_ai:true,reply_to:note.id})});if(!saved?.id)throw new Error("留言板没有返回已保存的回复");form.hidden=true;form.reset();if(motivationPersonaKey()===personaKey)await loadInnerWriting();$("#stickyCounter").textContent="回复已保存，并已显示在对应人格的留言板";}catch(error){$("#stickyCounter").textContent=`回复没有保存：${error.message}`;}finally{submit.disabled=false;}};
$("#stickyLater").onclick=$("#closeStickyInbox").onclick=()=>{$("#stickyInbox").hidden=true;};
$("#openCall").onclick=openVoiceCall;$("#closeCall").onclick=()=>{endVoiceCall();$("#callSpace").hidden=true;};$("#startCall").onclick=()=>startVoiceCall().catch(error=>{$("#callStatus").textContent=`无法开始：${error.message}`;});$("#endCall").onclick=endVoiceCall;
$("#closeMessageMenu").onclick=()=>$("#messageMenu").hidden=true;$("#messageMenu").onclick=event=>{if(event.target===$("#messageMenu"))$("#messageMenu").hidden=true;};
$("#branchMessage").onclick=async()=>{const message=state.messages[Number($("#messageMenu").dataset.messageIndex)];$("#messageMenu").hidden=true;if(!message?.id)return;try{const conversation=await api(`/api/conversations/${state.current}/branch/${message.id}`,{method:"POST"});state.conversations.unshift(conversation);renderHistory();await openConversation(conversation.id);}catch(error){alert(`创建分支失败：${error.message}`);}};
$("#editMessage").onclick=()=>{const message=state.messages[Number($("#messageMenu").dataset.messageIndex)];$("#messageMenu").hidden=true;openMessageEditor(message);};
$("#cancelMessageEdit").onclick=()=>$("#messageEditor").hidden=true;$("#messageEditor").onclick=event=>{if(event.target===$("#messageEditor"))$("#messageEditor").hidden=true;};
$("#messageEditor form").onsubmit=async event=>{event.preventDefault();const editor=$("#messageEditor"),id=editor.dataset.messageId,content=$("#messageEditContent").value.trim(),message=id?state.messages.find(item=>item.id===id):state.messages[Number(editor.dataset.messageIndex)];if(!message||!content)return;if(id){const saved=await api(`/api/messages/${id}`,{method:"PATCH",body:JSON.stringify({content})});message.content=saved.content;}else message.content=content;editor.hidden=true;renderMessages();};
$("#deleteMessageVersion").onclick=async()=>{const index=Number($("#messageMenu").dataset.messageIndex),message=state.messages[index];if(!message?.id)return;const note=message.role==="user"?"删除这条消息时，它下面的全部 AI 回答也会删除。":"只删除当前显示的这个 AI 回答版本。";if(!confirm(`${note}\n\n确定继续吗？`))return;await api(`/api/messages/${message.id}`,{method:"DELETE"});state.messages=state.messages.filter(item=>item.id!==message.id&&(message.role!=="user"||item.parent_message_id!==message.id));if(message.parent_message_id)delete state.version_selection[message.parent_message_id];if(message.role==="user")delete state.version_selection[message.id];$("#messageMenu").hidden=true;renderMessages();};
$("#deleteAllMessageVersions").onclick=async()=>{const index=Number($("#messageMenu").dataset.messageIndex),message=state.messages[index];if(!message?.id)return;const parentId=message.role==="assistant"?message.parent_message_id:message.id,note=message.role==="assistant"?"删除这条提问下的全部 AI 回答版本？你的提问会保留。":"删除你的这条消息以及它下面的全部 AI 回答？";if(!confirm(note))return;await api(`/api/messages/${message.id}/versions`,{method:"DELETE"});state.messages=state.messages.filter(item=>message.role==="assistant"?item.parent_message_id!==parentId:item.id!==message.id&&item.parent_message_id!==message.id);delete state.version_selection[parentId];$("#messageMenu").hidden=true;renderMessages();};
$("#chooseBook").onclick=()=>$("#bookInput").click();$("#bookInput").onchange=async event=>{const file=event.target.files?.[0];event.target.value="";await openLocalBook(file);};
document.querySelectorAll("[data-reading-tab]").forEach(button=>button.onclick=()=>showReadingTab(button.dataset.readingTab));
$("#addBookmark").onclick=()=>{
  if(!currentBook)return;const position=currentBookPosition(),items=readBookLocal("bookmarks");items.unshift({id:crypto.randomUUID?.()||String(Date.now()),...position,created_at:new Date().toISOString()});writeBookLocal("bookmarks",items.slice(0,300));renderBookNotes();showReadingTab("bookmarks");
};
$("#addAnnotation").onclick=()=>{
  if(!currentBook)return;const selected=selectedBookText();if(!selected){$("#bookStatus").textContent="请先在正文里选择一段文字";return;}const note=prompt("写下对这段文字的批注：","");if(note===null)return;const items=readBookLocal("annotations");items.unshift({id:crypto.randomUUID?.()||String(Date.now()),...selected,note:note.trim().slice(0,5000),created_at:new Date().toISOString()});writeBookLocal("annotations",items.slice(0,500));renderBookNotes();showReadingTab("annotations");getSelection()?.removeAllRanges();
};
$("#askBookAi").onclick=()=>{if(currentBook){showReadingTab("ai");$("#bookAiQuestion").focus();}};
let bookAiMessages=[];
function bookAiChatKey(){return `atherloom:book-ai:${currentBook?.key||"lobby"}`;}
function loadBookAiChat(){try{bookAiMessages=JSON.parse(localStorage.getItem(bookAiChatKey())||"[]");}catch{bookAiMessages=[];}renderBookAiChat();}
function saveBookAiChat(){localStorage.setItem(bookAiChatKey(),JSON.stringify(bookAiMessages.slice(-40)));}
function renderBookAiChat(){const log=$("#bookAiChat");if(!log)return;log.innerHTML=bookAiMessages.length?bookAiMessages.map(item=>`<article class="${item.role}"><b>${item.role==="user"?"你":escapeHtml(activePersonaName())}</b><p>${escapeHtml(item.content||"")}</p></article>`).join(""):`<p class="watch-chat-empty">选中一段文字，或者在阅读位置直接问他。</p>`;requestAnimationFrame(()=>log.scrollTop=log.scrollHeight);}
$("#bookAiForm").onsubmit=async event=>{
  event.preventDefault();const question=$("#bookAiQuestion").value.trim();if(!question||!currentBook)return;if(!activeProvider())return openSettings("providers");if(!state.current)await newConversation();
  const selected=selectedBookText(),position=currentBookPosition(),evidence=(selected?.quote||currentBook.text.slice(Math.max(0,position.offset-2000),position.offset+2000)).slice(0,4000),context=[`书籍：${currentBook.title}`,`本地阅读位置：约 ${Math.round(position.ratio*100)}%`,`证据范围：只包含用户选中的文字或当前位置附近最多 4,000 字`,`阅读片段：\n${evidence}`].join("\n");
  const button=event.target.querySelector("button"),assistant={role:"assistant",content:""};$("#bookAiQuestion").value="";bookAiMessages.push({role:"user",content:question},{...assistant});renderBookAiChat();button.disabled=true;button.textContent="正在共读…";
  try{const response=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({conversation_id:state.current,content:question,provider_id:activeProvider().id,persona_id:state.persona,local_time:localTimeContext(),media_context:context,worldbook_ids:selectedWorldbookIds()})});if(!response.ok)throw new Error((await response.json().catch(()=>({}))).detail||`请求失败 ${response.status}`);const reader=response.body.getReader(),decoder=new TextDecoder();let pending="";while(true){const {value,done}=await reader.read();if(done)break;pending+=decoder.decode(value,{stream:true});const lines=pending.split("\n");pending=lines.pop();for(const line of lines){if(!line)continue;const packet=JSON.parse(line);if(packet.error)throw new Error(packet.error);if(typeof packet.delta==="string"&&packet.delta!=="null"){assistant.content+=packet.delta;bookAiMessages[bookAiMessages.length-1].content=assistant.content;renderBookAiChat();}}}saveBookAiChat();}catch(error){bookAiMessages[bookAiMessages.length-1].content=`共读回应失败：${error.message}`;renderBookAiChat();saveBookAiChat();}finally{button.disabled=false;button.textContent="一起读这一段";}
};
let movieUrl,movieProgressKey,watchCues=[],watchMessages=[],watchSource={title:"私人放映室",kind:"local"};
function watchChatKey(){return `atherloom:watch-chat:${movieProgressKey||"lobby"}`;}
function loadWatchChat(){try{watchMessages=JSON.parse(localStorage.getItem(watchChatKey())||"[]");}catch{watchMessages=[];}renderWatchChat();}
function saveWatchChat(){localStorage.setItem(watchChatKey(),JSON.stringify(watchMessages.slice(-40)));}
function renderWatchChat(){const log=$("#watchChat");if(!log)return;log.innerHTML=watchMessages.length?watchMessages.map(item=>`<article class="${item.role}"><b>${item.role==="user"?"你":escapeHtml(activePersonaName())}</b><p>${escapeHtml(item.content||"")}</p></article>`).join(""):`<p class="watch-chat-empty">选好影片后，就在这里边看边聊。</p>`;requestAnimationFrame(()=>log.scrollTop=log.scrollHeight);}
const watchClock=seconds=>{seconds=Math.max(0,Number(seconds)||0);const h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),s=Math.floor(seconds%60);return [h?String(h).padStart(2,"0"):null,String(m).padStart(2,"0"),String(s).padStart(2,"0")].filter(Boolean).join(":");};
function parseSubtitleTime(value){const parts=String(value).trim().replace(",",".").split(":").map(Number);return parts.length===3?parts[0]*3600+parts[1]*60+parts[2]:parts[0]*60+parts[1];}
function parseWatchSubtitles(raw){
  return String(raw).replace(/\r/g,"").replace(/^WEBVTT[^\n]*\n+/,"").split(/\n{2,}/).map(block=>{const lines=block.split("\n").filter(Boolean);if(/^\d+$/.test(lines[0]||""))lines.shift();const timing=lines.shift()||"",match=timing.match(/([\d:,\.]+)\s*-->\s*([\d:,\.]+)/);if(!match)return null;return {start:parseSubtitleTime(match[1]),end:parseSubtitleTime(match[2]),text:lines.join(" ").replace(/<[^>]+>/g,"").trim()};}).filter(item=>item&&item.text).sort((a,b)=>a.start-b.start);
}
function renderWatchMoment(){
  const time=$("#moviePlayer").currentTime||0;$("#watchTime").textContent=watchClock(time);
  const nearby=watchCues.filter(cue=>cue.start<=time&&cue.end>=Math.max(0,time-18)).slice(-4);
  $("#subtitleRibbon").innerHTML=nearby.length?nearby.map(cue=>`<p class="${cue.start<=time&&cue.end>=time?"current":""}"><time>${watchClock(cue.start)}</time>${escapeHtml(cue.text)}</p>`).join(""):`<p>${watchCues.length?"这一刻没有对白。":"添加字幕后，AI 才能可靠地陪你看到这一幕。"}</p>`;
}
function loadMovieSource(src,title,key){
  const player=$("#moviePlayer"),empty=$("#watchEmpty");player.pause();player.removeAttribute("src");player.load();player.preload="auto";player.src=src;watchSource={title,kind:src.startsWith("blob:")?"local":"url"};movieProgressKey=key;loadWatchChat();$("#watchTitle").textContent=title;$("#movieStatus").textContent=`正在读取 ${title}…`;empty.hidden=true;
  player.onloadedmetadata=()=>{const saved=Number(localStorage.getItem(key)||0),end=Number.isFinite(player.duration)?Math.max(0,player.duration-1):0;player.currentTime=Math.min(saved,end);renderWatchMoment();};
  player.onloadeddata=()=>{if(!player.videoWidth||!player.videoHeight){$("#movieStatus").textContent=`${title} 没有可显示的视频画面，可能只有音轨或视频编码不受浏览器支持`;empty.innerHTML="<span>!</span><strong>读到了时长，但没有画面</strong><small>请换用 MP4（H.264 视频 + AAC 音频）后重试</small>";empty.hidden=false;return;}$("#movieStatus").textContent=`${title} · ${player.videoWidth}×${player.videoHeight} · 进度保存在本机`;empty.hidden=true;};
  player.onerror=()=>{const code=player.error?.code||0,hints={1:"视频读取被中止",2:"视频文件或链接读取失败",3:"浏览器无法解码这个视频",4:"浏览器不支持该视频格式"};$("#movieStatus").textContent=hints[code]||"视频无法播放";empty.innerHTML=`<span>!</span><strong>${escapeHtml(hints[code]||"视频无法播放")}</strong><small>推荐使用 MP4（H.264 视频 + AAC 音频）；在线视频还需要允许跨域访问</small>`;empty.hidden=false;};
  player.load();
  player.ontimeupdate=()=>{renderWatchMoment();if(Math.floor(player.currentTime)%5===0)localStorage.setItem(key,String(player.currentTime));};
}
$("#chooseMovie").onclick=()=>$("#movieInput").click();
$("#movieInput").onchange=event=>{const file=event.target.files?.[0];event.target.value="";if(!file)return;if(movieUrl)URL.revokeObjectURL(movieUrl);movieUrl=URL.createObjectURL(file);loadMovieSource(movieUrl,file.name,`atherloom:movie:${file.name}:${file.size}`);};
$("#openMovieLink").onclick=()=>{const value=$("#movieLink").value.trim();if(!/^https?:\/\//i.test(value))return $("#movieStatus").textContent="请输入 http 或 https 视频直链";loadMovieSource(value,new URL(value).pathname.split("/").pop()||"在线视频",`atherloom:movie-url:${value}`);};
$("#chooseSubtitle").onclick=()=>$("#subtitleInput").click();
$("#subtitleInput").onchange=async event=>{const file=event.target.files?.[0];event.target.value="";if(!file)return;watchCues=parseWatchSubtitles(await file.text());$("#subtitleStatus").textContent=watchCues.length?`${file.name} · ${watchCues.length} 条字幕`:"没有识别到 SRT/VTT 时间轴";renderWatchMoment();};
$("#watchQuestionForm").onsubmit=async event=>{
  event.preventDefault();const question=$("#watchQuestion").value.trim();if(!question)return;if(!activeProvider())return openSettings("providers");if(!state.current)await newConversation();
  const time=$("#moviePlayer").currentTime||0,evidence=watchCues.filter(cue=>cue.start<=time).slice(-18),context=[`你正在私人放映室里和用户一起实时观看《${watchSource.title}》。请像坐在用户身边一样自然回应，不要声称自己不在观影或无法看见；但只能依据下面提供的播放进度与字幕证据，不得虚构画面。`,`当前播放点：${watchClock(time)}`,`证据范围：只包含播放点之前的字幕`,evidence.length?`最近字幕：\n${evidence.map(cue=>`[${watchClock(cue.start)}] ${cue.text}`).join("\n")}`:"当前没有可用字幕证据。可以回应用户的感受，但不得猜测画面或剧情。",$("#watchNoSpoilers").checked?"严格禁止引用播放点之后的情节。":""].filter(Boolean).join("\n"),button=event.target.querySelector("button"),assistant={role:"assistant",content:""};
  $("#watchQuestion").value="";watchMessages.push({role:"user",content:question},{...assistant});renderWatchChat();button.disabled=true;button.textContent="正在回应…";
  try{const response=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({conversation_id:state.current,content:question,provider_id:activeProvider().id,persona_id:state.persona,local_time:localTimeContext(),media_context:context,worldbook_ids:selectedWorldbookIds()})});if(!response.ok)throw new Error((await response.json().catch(()=>({}))).detail||`请求失败 ${response.status}`);const reader=response.body.getReader(),decoder=new TextDecoder();let pending="";while(true){const {value,done}=await reader.read();if(done)break;pending+=decoder.decode(value,{stream:true});const lines=pending.split("\n");pending=lines.pop();for(const line of lines){if(!line)continue;const packet=JSON.parse(line);if(packet.error)throw new Error(packet.error);if(typeof packet.delta==="string"&&packet.delta!=="null"){assistant.content+=packet.delta;watchMessages[watchMessages.length-1].content=assistant.content;renderWatchChat();}}}saveWatchChat();}catch(error){watchMessages[watchMessages.length-1].content=`观影回应失败：${error.message}`;renderWatchChat();saveWatchChat();}finally{button.disabled=false;button.textContent="问这一幕";}
};
let musicTracks=[],musicIndex=-1,musicLyrics=[],musicMessages=[],musicObjectUrls=[];
const musicChatKey=()=>`atherloom:listening-chat:${musicTracks[musicIndex]?.key||"lobby"}`;
function saveMusicChat(){localStorage.setItem(musicChatKey(),JSON.stringify(musicMessages.slice(-40)));}
function renderMusicChat(){const log=$("#listeningChat");log.innerHTML=musicMessages.length?musicMessages.map(item=>`<article class="${item.role}"><b>${item.role==="user"?"你":escapeHtml(activePersonaName())}</b><p>${escapeHtml(item.content||"")}</p></article>`).join(""):`<p class="watch-chat-empty">歌响起来以后，可以说说这一句让你想起了什么。</p>`;requestAnimationFrame(()=>log.scrollTop=log.scrollHeight);}
function loadMusicChat(){try{musicMessages=JSON.parse(localStorage.getItem(musicChatKey())||"[]");}catch{musicMessages=[];}renderMusicChat();}
function parseLrc(raw){const rows=[];for(const line of String(raw).replace(/\r/g,"").split("\n")){const matches=[...line.matchAll(/\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\]/g)],text=line.replace(/\[[^\]]+\]/g,"").trim();for(const match of matches)if(text)rows.push({time:Number(match[1])*60+Number(match[2]),text});}return rows.sort((a,b)=>a.time-b.time);}
function renderMusicLyrics(){const time=$("#musicPlayer").currentTime||0,current=Math.max(-1,musicLyrics.findLastIndex(item=>item.time<=time)),nearby=musicLyrics.slice(Math.max(0,current-2),current+3);$("#lyricRibbon").innerHTML=nearby.length?nearby.map(item=>`<p class="${item===musicLyrics[current]?"current":""}"><time>${watchClock(item.time)}</time>${escapeHtml(item.text)}</p>`).join(""):`<p>${musicLyrics.length?"前奏里，先安静听一会儿。":"添加 LRC 歌词后，这里会跟着唱到的位置移动。"}</p>`;}
function renderMusicPlaylist(){$("#musicPlaylist").innerHTML=musicTracks.map((track,index)=>`<button class="music-track ${index===musicIndex?"active":""}" data-music-index="${index}"><span>${String(index+1).padStart(2,"0")}</span><strong>${escapeHtml(track.title)}</strong><small>${index===musicIndex?"正在播放":"本地"}</small></button>`).join("")||`<p class="muted">还没有歌曲。</p>`;document.querySelectorAll("[data-music-index]").forEach(button=>button.onclick=()=>loadMusicTrack(Number(button.dataset.musicIndex),true));}
function updateMediaSession(){if(!("mediaSession" in navigator)||musicIndex<0)return;navigator.mediaSession.metadata=new MediaMetadata({title:musicTracks[musicIndex].title,artist:"Atherloom · 一起听歌"});navigator.mediaSession.playbackState=$("#musicPlayer").paused?"paused":"playing";}
function loadMusicTrack(index,play=false){if(!musicTracks[index])return;musicIndex=index;musicLyrics=[];const track=musicTracks[index],player=$("#musicPlayer");player.src=track.url;$("#listeningTitle").textContent=track.title;$("#listeningStatus").textContent="歌曲留在本机 · 播放进度保存在这台设备";$("#lyricsInput").value="";loadMusicChat();renderMusicPlaylist();renderMusicLyrics();player.onloadedmetadata=()=>{const saved=Number(localStorage.getItem(`atherloom:music-progress:${track.key}`)||0);player.currentTime=Math.min(saved,Math.max(0,(player.duration||0)-1));$("#musicDuration").textContent=watchClock(player.duration);if(play)player.play().catch(()=>{$("#listeningStatus").textContent="请点一次播放，浏览器不允许自动发声";});};player.load();updateMediaSession();}
$("#chooseMusic").onclick=()=>$("#musicInput").click();
$("#musicInput").onchange=event=>{const files=[...(event.target.files||[])];event.target.value="";for(const file of files){const url=URL.createObjectURL(file);musicObjectUrls.push(url);musicTracks.push({title:file.name.replace(/\.[^.]+$/,""),url,key:`${file.name}:${file.size}:${file.lastModified}`});}renderMusicPlaylist();if(musicIndex<0&&musicTracks.length)loadMusicTrack(0);};
$("#chooseLyrics").onclick=()=>musicIndex<0?$("#listeningStatus").textContent="请先选择一首歌":$("#lyricsInput").click();
$("#lyricsInput").onchange=async event=>{const file=event.target.files?.[0];event.target.value="";if(!file)return;musicLyrics=parseLrc(await file.text());$("#listeningStatus").textContent=musicLyrics.length?`${file.name} · ${musicLyrics.length} 行歌词`:"没有识别到带时间轴的 LRC 歌词";renderMusicLyrics();};
$("#clearPlaylist").onclick=()=>{const player=$("#musicPlayer");player.pause();player.removeAttribute("src");musicObjectUrls.forEach(URL.revokeObjectURL);musicObjectUrls=[];musicTracks=[];musicIndex=-1;musicLyrics=[];$("#listeningTitle").textContent="把第一首歌放进来";$("#listeningStatus").textContent="音频只在这台设备播放，不上传歌曲文件。";renderMusicPlaylist();renderMusicLyrics();};
$("#musicPlay").onclick=()=>{const player=$("#musicPlayer");if(musicIndex<0)return $("#chooseMusic").click();player.paused?player.play().catch(error=>$("#listeningStatus").textContent=`无法播放：${error.message}`):player.pause();};
$("#musicPrevious").onclick=()=>musicTracks.length&&loadMusicTrack((musicIndex-1+musicTracks.length)%musicTracks.length,true);$("#musicNext").onclick=()=>musicTracks.length&&loadMusicTrack((musicIndex+1)%musicTracks.length,true);
$("#musicSeek").oninput=event=>{const player=$("#musicPlayer");if(Number.isFinite(player.duration))player.currentTime=player.duration*Number(event.target.value)/1000;};
$("#musicPlayer").ontimeupdate=()=>{const player=$("#musicPlayer"),track=musicTracks[musicIndex];$("#musicCurrent").textContent=watchClock(player.currentTime);$("#musicSeek").value=player.duration?String(Math.round(player.currentTime/player.duration*1000)):"0";renderMusicLyrics();if(track&&Math.floor(player.currentTime)%5===0)localStorage.setItem(`atherloom:music-progress:${track.key}`,String(player.currentTime));};
$("#musicPlayer").onplay=()=>{$(".listening-deck").classList.add("playing");$("#musicPlay").textContent="暂停";updateMediaSession();};$("#musicPlayer").onpause=()=>{$(".listening-deck").classList.remove("playing");$("#musicPlay").textContent="播放";updateMediaSession();};$("#musicPlayer").onended=()=>$("#musicNext").click();
if("mediaSession" in navigator){for(const [action,handler] of [["play",()=>$("#musicPlayer").play()],["pause",()=>$("#musicPlayer").pause()],["previoustrack",()=>$("#musicPrevious").click()],["nexttrack",()=>$("#musicNext").click()],["seekto",detail=>{if(detail.seekTime!=null)$("#musicPlayer").currentTime=detail.seekTime;}]])try{navigator.mediaSession.setActionHandler(action,handler);}catch{}}
$("#listeningQuestionForm").onsubmit=async event=>{event.preventDefault();const question=$("#listeningQuestion").value.trim(),track=musicTracks[musicIndex];if(!question||!track)return;if(!activeProvider())return openSettings("providers");if(!state.current)await newConversation();const time=$("#musicPlayer").currentTime||0,evidence=musicLyrics.filter(item=>item.time<=time).slice(-10),context=[`歌曲：《${track.title}》`,`当前播放点：${watchClock(time)}`,evidence.length?`到目前为止出现的最近歌词：\n${evidence.map(item=>`[${watchClock(item.time)}] ${item.text}`).join("\n")}`:"没有歌词证据。只能回应用户的感受，不得编造歌词、歌手或歌曲背景。","你正在和用户一起听这首歌。自然回应此刻的感受，但只把上面的本地播放信息当作证据。"].join("\n"),button=event.target.querySelector("button"),assistant={role:"assistant",content:""};$("#listeningQuestion").value="";musicMessages.push({role:"user",content:question},{...assistant});renderMusicChat();button.disabled=true;button.textContent="正在听…";try{const response=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({conversation_id:state.current,content:question,provider_id:activeProvider().id,persona_id:state.persona,local_time:localTimeContext(),media_context:context,worldbook_ids:selectedWorldbookIds()})});const reader=response.body.getReader(),decoder=new TextDecoder();let pending="";while(true){const {value,done}=await reader.read();if(done)break;pending+=decoder.decode(value,{stream:true});const lines=pending.split("\n");pending=lines.pop();for(const line of lines){if(!line)continue;const packet=JSON.parse(line);if(packet.error)throw new Error(packet.error);if(typeof packet.delta==="string"&&packet.delta!=="null"){assistant.content+=packet.delta;musicMessages[musicMessages.length-1].content=assistant.content;renderMusicChat();}}}saveMusicChat();}catch(error){musicMessages[musicMessages.length-1].content=`陪听回应失败：${error.message}`;renderMusicChat();saveMusicChat();}finally{button.disabled=false;button.textContent="和他聊这首歌";}};
let favoriteSearchTimer;$("#favoriteSearch").oninput=event=>{clearTimeout(favoriteSearchTimer);favoriteSearchTimer=setTimeout(async()=>{state.favorites=await api(`/api/favorites?q=${encodeURIComponent(event.target.value.trim())}`);renderFavorites();},220);};
document.querySelectorAll("[data-game-action]").forEach(button => button.onclick = () => playGame(button.dataset.gameAction, Number(button.dataset.amount || 1)));
document.querySelectorAll("[data-claw-action]").forEach(button=>button.onclick=()=>playMiniGame("claw_machine",button.dataset.clawAction));document.querySelectorAll("[data-slot-amount]").forEach(button=>button.onclick=()=>playMiniGame("cloud_slots","spin",Number(button.dataset.slotAmount)));
document.querySelectorAll("[data-star-move]").forEach(button=>button.onclick=()=>playMiniGame("star_merge",button.dataset.starMove));
$("#starModeSelf").onclick=()=>setStarMergeMode("self");$("#starModeAi").onclick=()=>setStarMergeMode("ai");
$("#undoStarMerge").onclick=()=>playMiniGame("star_merge","undo");
$("#resetStarMerge").onclick=()=>{if(!gameState.starMerge?.turn||confirm("重新开始这一局？当前棋盘会被清空。"))playMiniGame("star_merge","reset");};
$("#resetMaze").onclick=()=>playMiniGame("mist_maze","reset");
document.querySelectorAll("[data-maze-move]").forEach(button=>button.onclick=()=>playMiniGame("mist_maze",button.dataset.mazeMove));
$("#resetDungeon").onclick=()=>playMiniGame("ember_dungeon","reset");
document.querySelectorAll("[data-dungeon-action]").forEach(button=>button.onclick=()=>playMiniGame("ember_dungeon",button.dataset.dungeonAction));
document.addEventListener("keydown",event=>{if(gameState.current!=="star_merge"||gameState.starMergeMode!=="self"||$("#starMergeStage").hidden||event.target.closest("input,textarea,select"))return;const direction={ArrowUp:"up",ArrowDown:"down",ArrowLeft:"left",ArrowRight:"right"}[event.key];if(direction){event.preventDefault();playMiniGame("star_merge",direction);}});
let starSwipeStart=null;$("#starMergeBoard").addEventListener("pointerdown",event=>{if(gameState.starMergeMode==="self")starSwipeStart={x:event.clientX,y:event.clientY};});$("#starMergeBoard").addEventListener("pointerup",event=>{if(!starSwipeStart||gameState.starMergeMode!=="self")return;const dx=event.clientX-starSwipeStart.x,dy=event.clientY-starSwipeStart.y;starSwipeStart=null;if(Math.max(Math.abs(dx),Math.abs(dy))<24)return;playMiniGame("star_merge",Math.abs(dx)>Math.abs(dy)?dx>0?"right":"left":dy>0?"down":"up");});
document.querySelectorAll("[data-ai-game-turns]").forEach(button=>button.onclick=()=>aiPlayGame(button.dataset.aiGameTurns));
$("#stopAiGame").onclick=()=>{aiGameRun++;$("#stopAiGame").hidden=true;document.querySelectorAll("[data-ai-game-turns]").forEach(button=>button.disabled=false);$("#aiGameStatus").textContent=`已停止。当前棋局和刚完成的回合都保留着。`;};
async function sendGameRoomMessage(){const provider=activeProvider()||state.providers[0],input=$("#gameRoomInput"),content=input.value.trim();if(!content)return;if(!provider){$("#gameRoomMessages").insertAdjacentHTML("beforeend",'<div class="game-room-message event">还没有可用线路。游戏没有退出；请先到设置里添加线路。</div>');return;}state.provider||=provider.id;const gameId=gameState.current,pending={id:crypto.randomUUID?.()||`${Date.now()}-${Math.random()}`,gameId,content,error:""};gameRoomPending.push(pending);input.value="";renderGameRoom();input.focus();try{const payload=await api(`/api/games/${gameId}/room-chat`,{method:"POST",body:JSON.stringify({provider_id:provider.id,persona_id:state.persona,content}),timeout:20000});const index=gameRoomPending.indexOf(pending);if(index>=0)gameRoomPending.splice(index,1);storeGameState(gameId,payload.state);if(gameState.current===gameId)renderCurrentGame(gameId);}catch(error){pending.error=`回复失败：${error.message}。棋局仍在继续，可以重新发送。`;if(gameState.current===gameId)renderGameRoom();}}
$("#gameRoomInput").addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();sendGameRoomMessage();}});
$("#backdrop").onclick = closeSettings; document.querySelectorAll("[data-close]").forEach(b => b.onclick = closeSettings);
document.querySelectorAll(".settings-nav button").forEach(b => b.onclick = () => switchTab(b.dataset.tab));
$("#openMcpFromTools").onclick=()=>switchTab("mcp");
$("#openMemoryFromPlugins").onclick=()=>switchTab("memory");
$("#openWritingFromPlugins").onclick=()=>switchTab("journal");
$("#openMcpFromPlugins").onclick=()=>switchTab("mcp");
document.querySelectorAll("[data-inner-space]").forEach(button=>button.onclick=()=>{document.querySelectorAll("[data-inner-space]").forEach(item=>item.classList.toggle("active",item===button));$("#journalSpace").hidden=button.dataset.innerSpace!=="journal";$("#boardSpace").hidden=button.dataset.innerSpace!=="board";$("#dreamSpace").hidden=button.dataset.innerSpace!=="dream";});
$("#journalForm").onsubmit=async event=>{event.preventDefault();const form=event.target,key=encodeURIComponent(motivationPersonaKey()),editing=form.dataset.editing,payload={title:form.elements.title.value.trim(),content:form.elements.content.value.trim(),space:form.elements.space.value,author:"user",visible_to_user:form.elements.visible_to_user.checked,visible_to_ai:form.elements.visible_to_ai.checked};await api(editing?`/api/journals/${key}/${editing}`:`/api/journals/${key}`,{method:editing?"PUT":"POST",body:JSON.stringify(payload)});form.reset();form.elements.visible_to_user.checked=true;delete form.dataset.editing;$("#cancelJournalEdit").hidden=true;loadInnerWriting();};
$("#cancelJournalEdit").onclick=()=>{const form=$("#journalForm");form.reset();form.elements.visible_to_user.checked=true;delete form.dataset.editing;$("#cancelJournalEdit").hidden=true;};
$("#boardForm").onsubmit=async event=>{event.preventDefault();const form=event.target,key=encodeURIComponent(motivationPersonaKey());await api(`/api/board/${key}`,{method:"POST",body:JSON.stringify({content:form.elements.content.value.trim(),author:"user",visible_to_user:true,visible_to_ai:form.elements.visible_to_ai.checked})});form.reset();form.elements.visible_to_ai.checked=true;loadInnerWriting();};
$("#dreamForm").onsubmit=async event=>{event.preventDefault();const form=event.target,key=encodeURIComponent(motivationPersonaKey());await api(`/api/dreams/${key}`,{method:"POST",body:JSON.stringify({title:form.elements.title.value.trim(),raw_text:form.elements.raw_text.value.trim(),kind:form.elements.kind.value,necropsy:form.elements.necropsy.value.trim()})});form.reset();loadInnerWriting();};
$("#generateDream").onclick=async()=>{const button=$("#generateDream"),status=$("#dreamStatus"),provider=activeProvider();if(!provider){status.textContent="请先给当前人格绑定模型线路。";return;}button.disabled=true;button.textContent="正在做梦…";status.textContent="梦会从近期真实对话碎片中生长，请稍等。";try{await api(`/api/dreams/${encodeURIComponent(motivationPersonaKey())}/generate`,{method:"POST",body:JSON.stringify({provider_id:provider.id})});status.textContent="梦醒了，已经收进梦库。";await loadInnerWriting();}catch(error){status.textContent=error.message;}finally{button.disabled=false;button.textContent="让 TA 做梦";}};
function providerFormData(form){const data=Object.fromEntries(new FormData(form));data.prompt_cache=form.elements.prompt_cache.checked;data.thinking_enabled=form.elements.thinking_enabled.checked;data.stream_enabled=form.elements.stream_enabled.checked;data.enabled=form.elements.enabled.checked;data.temperature=Number(data.temperature);data.top_p=Number(data.top_p);data.max_tokens=Number(data.max_tokens);if(form.dataset.editing)data.source_provider_id=form.dataset.editing;return data;}
function closeProviderForm(form=$("#providerForm")){form.reset();delete form.dataset.editing;form.elements.api_key.placeholder="";$("#providerKeyState").textContent="新线路需要填写 Key；保存后不会把明文重新显示出来。";form.elements.custom_headers.value="{}";form.elements.vision_mode.value="auto";form.elements.cache_mode.value="auto";form.elements.prompt_cache_key.value="";form.elements.prompt_cache.checked=true;form.elements.thinking_enabled.checked=true;form.elements.stream_enabled.checked=true;form.elements.enabled.checked=true;form.elements.temperature.value=.7;form.elements.top_p.value=1;form.elements.max_tokens.value=4096;form.hidden=true;$("#providerEditState").hidden=true;$("#saveProviderCopy").hidden=true;showFetchedModels([],form);}
$("#addProvider").onclick = () => { const form=$("#providerForm");closeProviderForm(form);form.hidden=false;$("#connectionState").textContent="";renderSettings();updateProviderCacheUI();}; $("#cancelProvider").onclick = () => {closeProviderForm();renderSettings();};
$("#providerForm").onsubmit = async e => {e.preventDefault();const form=e.target,data=providerFormData(form),editing=form.dataset.editing,saved=await api(editing?`/api/providers/${editing}`:"/api/providers",{method:editing?"PUT":"POST",body:JSON.stringify(data)});if(editing)Object.assign(state.providers.find(item=>item.id===editing),saved);else state.providers.push(saved);state.provider||=saved.id;closeProviderForm(form);renderSettings();renderPickers();};
$("#providerProtocol").onchange = event => { const form = $("#providerForm"); const presets = { deepseek: { name: "DeepSeek", base_url: "https://api.deepseek.com", model: "deepseek-v4-flash" }, glm: { name: "智谱 GLM", base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-5.2" } }; const preset = presets[event.target.value]; if (preset) for (const [key, value] of Object.entries(preset)) if (!form.elements[key].value) form.elements[key].value = value; updateProviderCacheUI(); };
$("#providerCacheMode").onchange=updateProviderCacheUI;
$("#refreshRuntime").onclick=renderRuntimePanel;
$("#motivationEnabled").onchange=async event=>{await api(`/api/motivation/${encodeURIComponent(motivationPersonaKey())}/enabled`,{method:"PUT",body:JSON.stringify({enabled:event.target.checked,offline_mode:$("#motivationOfflineMode").value})});renderRuntimePanel();};
$("#motivationTick").onclick=async()=>{await api(`/api/motivation/${encodeURIComponent(motivationPersonaKey())}/tick`,{method:"POST",body:"{}"});renderRuntimePanel();};
$("#motivationReset").onclick=async()=>{if(!confirm("重置当前人格的欲望状态？记忆和聊天不会删除。"))return;await api(`/api/motivation/${encodeURIComponent(motivationPersonaKey())}/reset`,{method:"POST",body:"{}"});renderRuntimePanel();};
$("#motivationOfflineMode").onchange=async event=>{localStorage.setItem(`atherloom:motivation-offline:${motivationPersonaKey()}`,event.target.value);await api(`/api/motivation/${encodeURIComponent(motivationPersonaKey())}/enabled`,{method:"PUT",body:JSON.stringify({enabled:$("#motivationEnabled").checked,offline_mode:event.target.value})});renderRuntimePanel();};
$("#motivationBackground").onchange=event=>{localStorage.setItem(`atherloom:motivation-background:${motivationPersonaKey()}`,event.target.checked?"1":"0");syncMotivationBackground();};
$("#toggleApiKey").onclick = () => { const input = $("#providerForm").elements.api_key; input.type = input.type === "password" ? "text" : "password"; };
$("#pasteApiKey").onclick=async()=>{const input=$("#providerForm").elements.api_key;try{const value=window.AtherloomNative?.getClipboard?window.AtherloomNative.getClipboard():await navigator.clipboard.readText();if(!value)throw new Error("剪贴板为空");input.value=value.trim();$("#connectionState").className="connection-state success";$("#connectionState").textContent="已从剪贴板粘贴";}catch(error){$("#connectionState").className="connection-state error";$("#connectionState").textContent=`无法读取剪贴板：${error.message}`;}};
$("#fetchModels").onclick=async()=>{const form=$("#providerForm"),status=$("#connectionState"),data=providerFormData(form);data.provider_id=form.dataset.editing||null;if(!data.base_url){form.elements.base_url.reportValidity();return;}status.className="connection-state";status.textContent="正在使用已保存的密钥拉取模型…";try{const result=await api("/api/providers/models",{method:"POST",body:JSON.stringify(data)}),models=result.models||[];showFetchedModels(models,form);status.classList.add("success");status.textContent=models.length?`已读取 ${models.length} 个模型；选择后可保存或另存为新模型`:`线路已响应，但没有返回模型`; }catch(error){status.classList.add("error");status.textContent=`拉取失败：${error.message}；仍可手动填写模型 ID`;}};
$("#providerModelSelect").onchange=event=>{if(event.target.value)$("#providerForm").elements.model.value=event.target.value;};
$("#testProvider").onclick = async () => { const form = $("#providerForm"); if (!form.reportValidity()) return; const data=providerFormData(form),status=$("#connectionState");status.className="connection-state";status.textContent="正在测试连接…";try{const result=await api("/api/providers/test",{method:"POST",body:JSON.stringify(data)});status.classList.add("success");status.textContent=result.message;}catch(error){status.classList.add("error");status.textContent=error.message;}};
$("#saveProviderCopy").onclick=async()=>{const form=$("#providerForm"),source=form.dataset.editing;if(!source||!form.reportValidity())return;const data=providerFormData(form);data.name=`${data.name} · ${data.model}`;const saved=await api("/api/providers",{method:"POST",body:JSON.stringify(data)});state.providers.push(saved);$("#connectionState").className="connection-state success";$("#connectionState").textContent=`已另存 ${saved.model}；原模型仍保留`;renderSettings();renderPickers();};
$("#personaForm").onsubmit = async e => {e.preventDefault();const form=e.target,button=$("#savePersona"),status=$("#personaSaveState"),name=form.elements.name.value.trim();status.textContent="";status.classList.remove("error");if(!name){document.querySelectorAll("[data-persona-tab]").forEach(item=>item.classList.toggle("active",item.dataset.personaTab==="basic"));document.querySelectorAll("[data-persona-pane]").forEach(pane=>pane.classList.toggle("active",pane.dataset.personaPane==="basic"));status.textContent="请先填写助手名称";status.classList.add("error");form.elements.name.focus();return;}button.disabled=true;button.textContent="正在保存…";try{const data={name,prompt:form.elements.prompt.value,config:personaConfigFromForm(form)},editing=form.dataset.editing,saved=await api(editing?`/api/personas/${editing}`:"/api/personas",{method:editing?"PUT":"POST",body:JSON.stringify(data)});if(editing)Object.assign(state.personas.find(item=>item.id===editing),saved);else{state.personas.push(saved);form.dataset.editing=saved.id;state.persona=saved.id;localStorage.setItem("atherloom:last-persona",saved.id);}$("#cancelPersonaEdit").hidden=false;renderSettings();renderPickers();renderHistory();status.textContent=`已保存「${saved.name}」`;button.textContent="保存修改";}catch(error){status.textContent=error.message;status.classList.add("error");button.textContent=form.dataset.editing?"保存修改":"保存人格";}finally{button.disabled=false;}};
$("#cancelPersonaEdit").onclick=resetPersonaForm;
document.querySelectorAll("[data-persona-tab]").forEach(button=>button.onclick=()=>{document.querySelectorAll("[data-persona-tab]").forEach(item=>item.classList.toggle("active",item===button));document.querySelectorAll("[data-persona-pane]").forEach(pane=>pane.classList.toggle("active",pane.dataset.personaPane===button.dataset.personaTab));});
$("#memoryForm").onsubmit = async e => { e.preventDefault(); const form = e.target; const data = {...Object.fromEntries(new FormData(form)),persona_key:memoryPersonaKey()}; const editing = form.dataset.editing; await api(editing ? `/api/memories/${editing}` : "/api/memories", { method: editing ? "PUT" : "POST", body: JSON.stringify(data) }); state.memories=await api(`/api/memories?persona_key=${encodeURIComponent(memoryListKey())}`); form.reset(); delete form.dataset.editing; $("#saveMemory").textContent = "添加到当前人格"; $("#cancelMemoryEdit").hidden = true; renderSettings(); };
$("#cancelMemoryEdit").onclick = () => { const form = $("#memoryForm"); form.reset(); delete form.dataset.editing; $("#saveMemory").textContent = "添加到当前人格"; $("#cancelMemoryEdit").hidden = true; };
let memorySearchTimer;
$("#memorySearch").oninput = event => { clearTimeout(memorySearchTimer); memorySearchTimer = setTimeout(async () => { state.memories = await api(`/api/memories?persona_key=${encodeURIComponent(memoryListKey())}&q=${encodeURIComponent(event.target.value.trim())}`); renderSettings(); }, 180); };
$("#memoryOwnerFilter").onchange=async()=>{state.memories=await api(`/api/memories?persona_key=${encodeURIComponent(memoryListKey())}`);renderSettings();};
$("#memoryKindFilter").onchange = renderSettings;
$("#modelPicker").onclick = e => { e.stopPropagation(); if (!state.providers.length) return openSettings("providers"); showPopover(e.currentTarget, $("#modelPopover"), state.providers.map(p => `<button data-value="${p.id}"><strong>${escapeHtml(p.name)}</strong><small>${escapeHtml(p.model)}</small></button>`).join(""), async id => { state.provider=id;if(state.current){const conversation=state.conversations.find(item=>item.id===state.current),saved=await api(`/api/conversations/${state.current}`,{method:"PATCH",body:JSON.stringify({provider_id:id})});Object.assign(conversation,saved);}renderPickers(); }); };
$("#personaPicker").onclick = e => { e.stopPropagation(); showPopover(e.currentTarget, $("#personaPopover"), `<button data-value="">默认人格</button>` + sortedPersonas().map(p => `<button data-value="${p.id}">${p.config?.pinned?"● ":""}${escapeHtml(p.name)}</button>`).join(""), selectPersona); };
$("#addPersonaFromSidebar").onclick=()=>{resetPersonaForm();openSettings("personas");document.querySelectorAll("[data-persona-tab]").forEach(item=>item.classList.toggle("active",item.dataset.personaTab==="basic"));document.querySelectorAll("[data-persona-pane]").forEach(pane=>pane.classList.toggle("active",pane.dataset.personaPane==="basic"));};
$("#quickPhraseButton").onclick=e=>{e.stopPropagation();const phrases=activePersona()?.config?.quick_phrases||[];showPopover(e.currentTarget,$("#quickPhrasePopover"),phrases.map((phrase,index)=>`<button data-value="${index}">${escapeHtml(phrase)}</button>`).join(""),index=>{const input=$("#prompt"),phrase=phrases[Number(index)];input.value=`${input.value}${input.value&&!/\s$/.test(input.value)?"\n":""}${phrase||""}`;input.dispatchEvent(new Event("input"));input.focus();});};
document.addEventListener("click", event => { if (!event.target.closest(".popover")) closePopovers(); if(!event.target.closest("#attachmentMenu")&&!event.target.closest("#attachmentButton"))$("#attachmentMenu").hidden=true; });
document.addEventListener("keydown", event => { if (event.key === "Escape") closePopovers(); });
function setSidebar(open){$("#sidebar").classList.toggle("open",open);$("#sidebarBackdrop").hidden=!open;}
$("#mobileMenu").onclick=()=>setSidebar(true);$("#sidebarClose").onclick=()=>setSidebar(false);$("#sidebarToggle").onclick=()=>{if(innerWidth<=760)setSidebar(false);};$("#sidebarBackdrop").onclick=()=>setSidebar(false);document.querySelectorAll("#sidebar .new-chat:not(.sidebar-hub-toggle),#sidebar .profile-row,#sidebar .history-item").forEach(button=>button.addEventListener("click",()=>setSidebar(false)));
window.AtherloomHandleBack=()=>{if(!$("#callSpace").hidden){endVoiceCall();$("#callSpace").hidden=true;return true;}if(!$("#roleplaySpace").hidden){$("#roleplaySpace").hidden=true;return true;}if(!$("#mediaSpace").hidden){$("#mediaSpace").hidden=true;$("#moviePlayer").pause();return true;}if(!$("#favoritesSpace").hidden){$("#favoritesSpace").hidden=true;return true;}if(!$("#gameLibrary").hidden){$("#gameLibrary").hidden=true;return true;}if($("#settingsPanel").classList.contains("open")){closeSettings();return true;}if($("#sidebar").classList.contains("open")){setSidebar(false);return true;}if([...document.querySelectorAll(".popover")].some(item=>!item.hidden)){closePopovers();return true;}return false;};
$("#themeSelect").onchange = e => { document.documentElement.dataset.theme = e.target.value === "system" ? "" : e.target.value; localStorage.setItem("theme", e.target.value); };
$("#exportBackup").onclick = exportLocalBackup;
$("#chooseBackup").onclick = () => $("#backupFile").click();
$("#backupFile").onchange = async event => { const file = event.target.files?.[0]; if (!file) return; try { await restoreLocalBackup(file); } catch (error) { $("#backupState").textContent = `恢复失败：${error.message}`; } finally { event.target.value = ""; } };
$("#cardRoomDraw").onclick=()=>{if(cardRoomState.turn%2===0&&cardRoomState.hand.length){cardRoomState.played.push(cardRoomState.hand.shift());cardRoomState.turn++;setTimeout(()=>{if(cardRoomState.hand.length){cardRoomState.played.push(cardRoomState.hand.shift());cardRoomState.turn++;renderCardRoom();}},500);renderCardRoom();}};
$("#cardRoomPass").onclick=()=>{cardRoomState.turn++;renderCardRoom();};
$("#cardRoomReset").onclick=()=>{Object.assign(cardRoomState,freshCardRoom());renderCardRoom();};
document.querySelectorAll("[data-card-mode]").forEach(button=>button.onclick=()=>{document.querySelectorAll("[data-card-mode]").forEach(item=>item.classList.toggle("active",item===button));cardRoomState.mode=button.dataset.cardMode;$("#cardRoomStatus").textContent=cardRoomState.mode==="mahjong"?"月亮麻将仍在设计中；当前可玩云朵扑克原型":"轮到你出牌";});
const theme = localStorage.getItem("theme") || "system"; $("#themeSelect").value = theme; if (theme !== "system") document.documentElement.dataset.theme = theme;
bootstrap().catch(error => { console.error(error); openSettings("providers"); });
updateProviderCacheUI();
setInterval(renderTimeGreeting, 60_000);
