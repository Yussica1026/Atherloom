const $ = (s) => document.querySelector(s);
const state = { providers: [], personas: [], conversations: [], memories: [], favorites: [], attachments: [], version_selection: {}, settings: { auto_title_mode: "local", tool_permissions: {} }, current: null, provider: null, persona: null, messages: [], busy: false };
const gameState = { catalog: [], current: null, fishing: null, claw: null, slots: null, waters: {} };
function dismissLaunchScreen(){const screen=$("#launchScreen");if(!screen||screen.classList.contains("dismissed"))return;screen.classList.add("dismissed");setTimeout(()=>screen.remove(),320);}
if($("#launchScreen")){const refresh=document.documentElement.dataset.launchMode==="refresh",delay=matchMedia("(prefers-reduced-motion: reduce)").matches?180:refresh?430:1250;$("#launchScreen").onclick=dismissLaunchScreen;setTimeout(dismissLaunchScreen,delay);}

async function api(path, options = {}) {
  const { timeout, ...fetchOptions } = options;
  const controller = timeout ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeout) : null;
  let response;
  try { response = await fetch(path, { headers: { "Content-Type": "application/json", ...(fetchOptions.headers || {}) }, ...fetchOptions, signal: fetchOptions.signal || controller?.signal }); }
  catch (error) { if (error.name === "AbortError") throw new Error("等待 AI 超时，请检查线路后重试"); throw error; }
  finally { if (timer) clearTimeout(timer); }
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `请求失败 ${response.status}`);
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
function activeProvider() { return state.providers.find(p => p.id === state.provider) || state.providers[0]; }
function activePersona() { return state.personas.find(p => p.id === state.persona); }
function activePersonaName() { return activePersona()?.name?.trim() || "当前人格"; }
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

let bookObjectUrl;
async function openLocalBook(file) {
  if (!file) return;
  const reader = $("#bookReader");
  const status = $("#bookStatus");
  const key = `atherloom:book:${file.name}:${file.size}`;
  const isPdf = file.type === "application/pdf" || /\.pdf$/i.test(file.name);
  status.textContent = `${file.name} · 正在打开…`;
  await new Promise(resolve => requestAnimationFrame(resolve));
  try {
    if (bookObjectUrl) { URL.revokeObjectURL(bookObjectUrl); bookObjectUrl = undefined; }
    reader.onscroll = null;
    if (isPdf && window.AtherloomNative) {
      reader.innerHTML = `<div class="game-empty"><span>PDF</span><h3>安卓暂不在应用内打开 PDF</h3><p>部分 Android WebView 打开本地 PDF 会卡死。请先转成 TXT 或 Markdown；后续接入原生 PDF 阅读器。</p></div>`;
      status.textContent = `${file.name} · 已安全拦截`;
      window.AtherloomNative.showNotice?.("为避免卡死，安卓暂不在应用内打开 PDF");
      return;
    }
    if (isPdf) {
      bookObjectUrl = URL.createObjectURL(file);
      reader.innerHTML = `<iframe title="${escapeHtml(file.name)}" src="${bookObjectUrl}#page=${Number(localStorage.getItem(key) || 1)}"></iframe>`;
      status.textContent = `${file.name} · 本地 PDF`;
      return;
    }
    const limit = 2 * 1024 * 1024;
    const text = await file.slice(0, limit).text();
    const pre = document.createElement("pre");
    pre.textContent = text;
    reader.replaceChildren(pre);
    reader.scrollTop = Number(localStorage.getItem(key) || 0);
    reader.onscroll = () => localStorage.setItem(key, String(reader.scrollTop));
    status.textContent = file.size > limit ? `${file.name} · 已打开前 2 MB，避免设备卡顿` : `${file.name} · 本地文件`;
  } catch (error) {
    reader.innerHTML = `<div class="game-empty"><span>!</span><h3>这本书没有打开</h3><p>${escapeHtml(error.message || "无法读取本地文件")}</p></div>`;
    status.textContent = `${file.name} · 打开失败`;
  }
}

function renderHistory() {
  const group = (label, items) => items.length ? `<div class="history-group"><div class="history-label">${label}</div>${items.map(c => `<div class="history-row ${c.id === state.current ? "active" : ""}"><button class="history-item" data-id="${c.id}">${escapeHtml(c.title)}</button><div class="history-actions"><button data-history-action="star" data-id="${c.id}" title="星标">${c.starred ? "★" : "☆"}</button><button data-history-action="pin" data-id="${c.id}" title="置顶">${c.pinned ? "●" : "○"}</button><button data-history-action="archive" data-id="${c.id}" title="${c.archived ? "取消归档" : "归档"}">⌑</button></div></div>`).join("")}</div>` : "";
  const active = state.conversations.filter(c => !c.archived);
  const pinned = active.filter(c => c.pinned);
  const starred = active.filter(c => c.starred && !c.pinned);
  const recent = active.filter(c => !c.pinned && !c.starred);
  const archived = state.conversations.filter(c => c.archived);
  $("#history").innerHTML = group("置顶", pinned) + group("星标", starred) + group("最近", recent) + group("已归档", archived) || `<p class="muted" style="padding:8px 11px">还没有对话</p>`;
  document.querySelectorAll(".history-item").forEach(button => button.onclick = () => { setSidebar(false); openConversation(button.dataset.id); });
  document.querySelectorAll("[data-history-action]").forEach(button => button.onclick = () => updateHistoryState(button.dataset.id, button.dataset.historyAction));
}

async function updateHistoryState(id, action) {
  const conversation = state.conversations.find(c => c.id === id); if (!conversation) return;
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
async function addAttachments(files){for(const file of [...files]){if(file.size>12*1024*1024){alert(`${file.name} 超过 12 MB，暂不添加`);continue;}const image=file.type.startsWith("image/");const text=file.type.startsWith("text/")||/\.(md|txt|json|csv|js|ts|py|html|css)$/i.test(file.name);state.attachments.push({id:crypto.randomUUID?.()||`${Date.now()}-${Math.random()}`,name:file.name,mime:file.type||"application/octet-stream",kind:image?"image":text?"text":file.type==="application/pdf"?"pdf":"file",data:image||file.type==="application/pdf"?await readFile(file,"data"):undefined,text:text?(await readFile(file,"text")).slice(0,120000):undefined,size:file.size});}renderAttachments();}

function personaQuery() { return state.persona ? `?persona_id=${encodeURIComponent(state.persona)}` : ""; }

function renderGameCards() {
  $("#gameCards").innerHTML = gameState.catalog.map(game => `<button class="game-card ${game.id === gameState.current ? "active" : ""}" data-game-id="${game.id}"><span class="game-card-icon">${game.icon}</span><span><strong>${escapeHtml(game.name)}</strong><small>${escapeHtml(game.description)}</small></span></button>`).join("");
  document.querySelectorAll("[data-game-id]").forEach(button => button.onclick = () => openGame(button.dataset.gameId));
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
}

function renderClaw(){const current=gameState.claw;if(!current)return;$("#clawCoins").textContent=current.coins;$("#clawTurns").textContent=current.turn;$("#clawHead").style.left=`${current.position*20+10}%`;$("#clawPrizes").innerHTML=current.prizes.map((name,index)=>`<span class="${index===current.position?"targeted":""}">◇<small>${escapeHtml(name)}</small></span>`).join("");$("#clawInventory").innerHTML=Object.entries(current.inventory).map(([name,count])=>`<span><b>${escapeHtml(name)}</b><em>× ${count}</em></span>`).join("")||"<small>还没有抓到娃娃。</small>";$("#clawJournal").innerHTML=[...current.journal].reverse().slice(0,8).map(item=>`<span>${escapeHtml(item)}</span>`).join("")||"<small>机器正在等待第一爪。</small>";}
function renderSlots(){const current=gameState.slots;if(!current)return;[$("#slotOne"),$("#slotTwo"),$("#slotThree")].forEach((node,index)=>node.textContent=current.reels[index]);$("#slotCoins").textContent=current.coins;$("#slotTurns").textContent=current.turn;$("#slotJournal").innerHTML=[...current.journal].reverse().slice(0,8).map(item=>`<span>${escapeHtml(item)}</span>`).join("")||"<small>拉下摇杆开始。</small>";}

async function openGame(gameId) {
  gameState.current = gameId; renderGameCards();
  $("#gameEmpty").hidden=true;$("#fishingStage").hidden=gameId!=="quiet_fishing";$("#clawStage").hidden=gameId!=="claw_machine";$("#slotsStage").hidden=gameId!=="cloud_slots";
  $("#aiGameControls").hidden=!["quiet_fishing","claw_machine","cloud_slots"].includes(gameId);
  $("#aiGameTitle").textContent=`交给 ${activePersonaName()}`;
  if(!["quiet_fishing","claw_machine","cloud_slots"].includes(gameId)){$("#gameEmpty").hidden=false;const game=gameState.catalog.find(item=>item.id===gameId);$("#gameEmpty").innerHTML=`<span>${game.icon}</span><h3>${escapeHtml(game.name)}</h3><p>${escapeHtml(game.description)}</p>`;return;}
  const payload = await api(`/api/games/${gameId}/state${personaQuery()}`); gameState.fishing = payload.state; gameState.waters = payload.waters;
  if(gameId==="quiet_fishing"){gameState.fishing=payload.state;gameState.waters=payload.waters;renderFishing();}else if(gameId==="claw_machine"){gameState.claw=payload.state;renderClaw();}else{gameState.slots=payload.state;renderSlots();}
}

async function playGame(action, amount = 1, target = "") {
  try {
    const payload = await api(`/api/games/quiet_fishing/action${personaQuery()}`, { method: "POST", body: JSON.stringify({ action, amount, target }) });
    gameState.fishing = payload.state; renderFishing();
  } catch (error) { alert(error.message); }
}
async function playMiniGame(gameId,action,amount=1){try{const payload=await api(`/api/games/${gameId}/action${personaQuery()}`,{method:"POST",body:JSON.stringify({action,amount})});if(gameId==="claw_machine"){gameState.claw=payload.state;renderClaw();}else{gameState.slots=payload.state;renderSlots();}}catch(error){alert(error.message);}}
async function aiPlayGame(turns){const provider=activeProvider(),name=activePersonaName();if(!provider){$("#gameLibrary").hidden=true;return openSettings("providers");}const buttons=[$("#aiPlayOne"),$("#aiPlayThree")],gameId=gameState.current;buttons.forEach(button=>button.disabled=true);let completed=0,spent=0,lastComment="";try{for(let turn=0;turn<turns;turn++){const remaining=30-spent;if(remaining<=0)break;$("#aiGameStatus").textContent=`${name} 正在决定第 ${turn+1}/${turns} 回合…`;const payload=await api(`/api/games/${gameId}/ai-turn`,{method:"POST",body:JSON.stringify({provider_id:provider.id,persona_id:state.persona,turns:1,max_spend:remaining}),timeout:45000});spent+=payload.spent||0;if(payload.decisions.length){completed++;lastComment=payload.decisions.at(-1).comment||lastComment;}if(gameId==="quiet_fishing"){gameState.fishing=payload.state;renderFishing();}else if(gameId==="claw_machine"){gameState.claw=payload.state;renderClaw();}else{gameState.slots=payload.state;renderSlots();}if(!payload.decisions.length)break;$("#aiGameStatus").textContent=`${name} 已完成 ${completed}/${turns} 回合，正在准备下一步…`;}$("#aiGameStatus").textContent=completed?`${name} 完成 ${completed} 步，花费 ${spent} 云贝。心里话：${lastComment||"专心操作中"}`:`${name} 因预算或局面限制没有执行动作。`;}catch(error){$("#aiGameStatus").textContent=`${completed?`已完成 ${completed} 步；`:""}${name} 游玩失败：${error.message}`;}finally{buttons.forEach(button=>button.disabled=false);}}

function parseGameRequest(content){
  const text=String(content||"").replace(/\s+/g,""),requested=/(?:你|请|帮我|能不能|可以|可不可以).{0,12}(?:玩|去玩|来玩|试试|钓|抓|转)|(?:玩|去玩|来玩|试试).{0,8}(?:游戏|小游戏|钓鱼|抓娃娃|老虎机)/.test(text);
  if(!requested)return null;
  const gameId=/(?:抓娃娃|娃娃机|下爪)/.test(text)?"claw_machine":/(?:老虎机|拉杆|转盘|摇奖)/.test(text)?"cloud_slots":/(?:钓鱼|抛竿|钓一竿|鱼塘)/.test(text)?"quiet_fishing":gameState.current||"quiet_fishing";
  const turns=/(?:3|三|几)(?:次|步|回合|竿|局)/.test(text)?3:1;
  return {gameId,turns};
}
async function prepareChatGameContext(content){
  const request=parseGameRequest(content);if(!request)return "";
  const provider=activeProvider();if(!provider)return "";
  const {gameId,turns}=request,name=activePersonaName(),gameNames={quiet_fishing:"云汀钓记",claw_machine:"抓娃娃机",cloud_slots:"云纹老虎机"};
  try{
    const payload=await api(`/api/games/${gameId}/ai-turn`,{method:"POST",body:JSON.stringify({provider_id:provider.id,persona_id:state.persona,turns,max_spend:30}),timeout:50000});
    if(gameId==="quiet_fishing"){gameState.fishing=payload.state;if(gameState.current===gameId)renderFishing();}else if(gameId==="claw_machine"){gameState.claw=payload.state;if(gameState.current===gameId)renderClaw();}else{gameState.slots=payload.state;if(gameState.current===gameId)renderSlots();}
    const details=payload.decisions.flatMap(item=>[...(item.events||[]),item.comment?`心里话：${item.comment}`:""]).filter(Boolean);
    const stateSummary=gameId==="quiet_fishing"?`当前鱼篓：${Object.entries(payload.state.catch||{}).map(([fish,count])=>`${fish}×${count}`).join("、")||"空"}；鱼饵 ${payload.state.bait}，云贝 ${payload.state.coins}`:gameId==="claw_machine"?`当前收藏：${Object.entries(payload.state.inventory||{}).map(([prize,count])=>`${prize}×${count}`).join("、")||"空"}；云贝 ${payload.state.coins}`:`当前转轮：${(payload.state.reels||[]).join(" · ")}；云贝 ${payload.state.coins}`;
    return `${name} 已通过宿主游戏工具真实游玩「${gameNames[gameId]}」${payload.decisions.length} 个回合。${details.join("；")}。${stateSummary}。这是已执行结果，不是想象或角色扮演。`;
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

function renderMessages() {
  $("#welcome").hidden = state.messages.length > 0;
  $("#messages").innerHTML = visibleMessageVersions().map(m => { const index=state.messages.indexOf(m); return `<article class="message ${m.role}" data-index="${index}">
    <div class="message-body">${m.memory_sources?.length ? `<div class="memory-sources">本轮使用记忆：${m.memory_sources.map(source => `<span>${escapeHtml(source.title)}</span>`).join("")}</div>` : ""}${m.reasoning ? `<details class="thinking" open><summary>思考过程</summary><div>${escapeHtml(m.reasoning)}</div></details>` : ""}<div class="bubble">${m.pending && !m.content ? `<span class="response-waiting"><i></i>正在生成</span>` : m.role === "assistant" && !m.streaming ? renderAssistantContent(m.content) : escapeHtml(m.content)}</div></div>
    ${m.pending ? "" : `<div class="message-actions"><button data-action="copy">复制</button>${m.id ? `<button data-action="favorite">${state.favorites.some(f => f.source_message_id === m.id && f.owners?.includes("user")) ? "★ 已珍藏" : "☆ 珍藏"}</button><button data-action="edit">修改</button>${m.role === "user" || m.parent_message_id ? `<button data-action="regenerate">重新 Roll</button>` : ""}<button data-action="more" aria-label="更多消息操作">•••</button>` : ""}</div>`}
    ${m.role === "assistant" && m._version_count > 1 ? `<div class="version-switcher"><button data-action="version-prev" ${m._version_index === 0 ? "disabled" : ""}>‹</button><span>${m._version_index + 1} / ${m._version_count}</span><button data-action="version-next" ${m._version_index === m._version_count - 1 ? "disabled" : ""}>›</button></div>` : ""}
    ${m.role === "assistant" && m.model ? `<div class="message-meta">${escapeHtml(m.model)}</div>` : ""}</article>`; }).join("");
  document.querySelectorAll(".message [data-action]").forEach(button => button.onclick = () => handleMessageAction(button.closest(".message"), button.dataset.action));
  document.querySelectorAll("[data-question-option]").forEach(button=>button.onclick=()=>{const input=$("#prompt"),line=`关于「${decodeURIComponent(button.dataset.questionTitle)}」，我的选择是：${decodeURIComponent(button.dataset.questionOption)}`;input.value=input.value.trim()?`${input.value.trim()}\n${line}`:line;button.closest(".question-card").querySelectorAll("button").forEach(item=>item.classList.toggle("selected",item===button));input.dispatchEvent(new Event("input"));input.focus();});
  $("#chatScroll").scrollTop = $("#chatScroll").scrollHeight;
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
  if (action === "regenerate") return generateReply("", message.role === "user" ? message.id : message.parent_message_id);
}

function openMessageEditor(message){if(!message?.id)return;$("#messageEditor").dataset.messageId=message.id;$("#messageEditContent").value=message.content;$("#messageEditor").hidden=false;requestAnimationFrame(()=>$("#messageEditContent").focus());}

function renderPickers() {
  const provider = activeProvider(); const persona = activePersona();
  $("#modelPicker").textContent = provider ? `${provider.name} · ${provider.model}⌄` : "添加 API 线路";
  $("#personaPicker").textContent = persona ? `${persona.name}⌄` : "默认人格⌄";
  const phrases=persona?.config?.quick_phrases||[];$("#quickPhraseButton").hidden=!phrases.length;
}

function renderSettings() {
  $("#providerList").innerHTML = state.providers.map(p => `<div class="list-card"><div><strong>${escapeHtml(p.name)}</strong><small>${escapeHtml(p.protocol)} · ${escapeHtml(p.model)} · 温度 ${p.temperature ?? 0.7} · ${p.has_api_key ? "Key 已保存" : "无 Key"}</small></div><div class="provider-card-actions"><button data-edit-provider="${p.id}">编辑</button><button data-delete-provider="${p.id}">删除</button></div></div>`).join("") || ($("#providerForm").hidden ? `<div class="empty-provider"><p class="muted">还没有 API 线路。</p><button class="primary" id="emptyAddProvider">添加第一条线路</button></div>` : "");
  $("#personaList").innerHTML = state.personas.map(p => `<div class="list-card"><div><strong>${escapeHtml(p.name)}</strong><small>${escapeHtml(p.prompt.slice(0, 70) || "空白人格")}</small></div><div class="provider-card-actions"><button data-edit-persona="${p.id}">编辑</button><button data-delete-persona="${p.id}">删除</button></div></div>`).join("");
  const kindLabels = { fact: "事实", preference: "偏好", relationship: "关系", promise: "承诺", event: "事件", emotion: "情感", summary: "摘要", diary: "日记", other: "其他" };
  const kindFilter = $("#memoryKindFilter")?.value || "";
  const visibleMemories = state.memories.filter(memory => !kindFilter || memory.kind === kindFilter);
  $("#memoryList").innerHTML = visibleMemories.map(memory => `<div class="list-card memory-card"><div><strong>${memory.starred ? "★ " : ""}${escapeHtml(memory.title)}</strong><small>${kindLabels[memory.kind] || escapeHtml(memory.kind)} · 更新于 ${new Date(memory.updated_at).toLocaleString()}</small><p>${escapeHtml(memory.content.slice(0, 180))}</p></div><div><button data-memory-edit="${memory.id}">编辑</button><button data-memory-star="${memory.id}">${memory.starred ? "取消星标" : "星标"}</button><button data-memory-trash="${memory.id}">回收</button></div></div>`).join("") || `<p class="muted">没有符合条件的本地记忆。</p>`;
  document.querySelectorAll("[data-delete-provider]").forEach(b => b.onclick = async () => { await api(`/api/providers/${b.dataset.deleteProvider}`, { method: "DELETE" }); state.providers = state.providers.filter(p => p.id !== b.dataset.deleteProvider); if (state.provider === b.dataset.deleteProvider) state.provider = state.providers[0]?.id || null; renderSettings(); renderPickers(); });
  document.querySelectorAll("[data-edit-provider]").forEach(b=>b.onclick=()=>{const provider=state.providers.find(item=>item.id===b.dataset.editProvider),form=$("#providerForm"),notice=$("#providerEditState");if(!provider)return;document.querySelectorAll("[data-edit-provider]").forEach(button=>button.closest(".list-card")?.classList.toggle("editing",button===b));form.hidden=false;form.dataset.editing=provider.id;notice.hidden=false;notice.textContent=`正在编辑「${provider.name}」`;for(const name of ["name","protocol","base_url","model","custom_headers","temperature","top_p","max_tokens"])if(form.elements[name])form.elements[name].value=provider[name]??({temperature:.7,top_p:1,max_tokens:4096,custom_headers:"{}"}[name]??"");form.elements.api_key.value="";form.elements.prompt_cache.checked=!!provider.prompt_cache;form.elements.thinking_enabled.checked=provider.thinking_enabled!==false&&provider.thinking_enabled!==0;form.elements.stream_enabled.checked=provider.stream_enabled!==false&&provider.stream_enabled!==0;form.elements.enabled.checked=provider.enabled!==false&&provider.enabled!==0;$("#connectionState").textContent="API Key 留空会保留原密钥";updateProviderCacheUI();requestAnimationFrame(()=>{const scroller=$(".settings-content");scroller.scrollTop=Math.max(0,form.offsetTop-18);form.elements.name.focus({preventScroll:true});});});
  document.querySelectorAll("[data-edit-persona]").forEach(b=>b.onclick=()=>{const persona=state.personas.find(item=>item.id===b.dataset.editPersona),form=$("#personaForm");form.dataset.editing=persona.id;form.elements.name.value=persona.name;form.elements.prompt.value=persona.prompt;fillPersonaConfig(form,persona.config||{});$("#savePersona").textContent="保存修改";$("#cancelPersonaEdit").hidden=false;form.scrollIntoView({behavior:"smooth",block:"start"});});
  document.querySelectorAll("[data-delete-persona]").forEach(b=>b.onclick=async()=>{const persona=state.personas.find(item=>item.id===b.dataset.deletePersona);if(!confirm(`删除人格“${persona.name}”？已绑定对话会切回默认人格。`))return;await api(`/api/personas/${persona.id}`,{method:"DELETE"});state.personas=state.personas.filter(item=>item.id!==persona.id);state.conversations.forEach(item=>{if(item.persona_id===persona.id)item.persona_id=null;});if(state.persona===persona.id)state.persona=state.personas[0]?.id||null;renderSettings();renderPickers();});
  document.querySelectorAll("[data-memory-star]").forEach(b => b.onclick = async () => { const memory = state.memories.find(item => item.id === b.dataset.memoryStar); Object.assign(memory, await api(`/api/memories/${memory.id}/state`, { method: "PATCH", body: JSON.stringify({ starred: !memory.starred }) })); renderSettings(); });
  document.querySelectorAll("[data-memory-edit]").forEach(b => b.onclick = () => { const memory = state.memories.find(item => item.id === b.dataset.memoryEdit); const form = $("#memoryForm"); form.dataset.editing = memory.id; form.elements.title.value = memory.title; form.elements.kind.value = memory.kind; form.elements.content.value = memory.content; $("#saveMemory").textContent = "保存修改"; $("#cancelMemoryEdit").hidden = false; form.scrollIntoView({ behavior: "smooth", block: "center" }); });
  document.querySelectorAll("[data-memory-trash]").forEach(b => b.onclick = async () => { const memory = state.memories.find(item => item.id === b.dataset.memoryTrash); if (!confirm(`将“${memory.title}”移入回收站？`)) return; await api(`/api/memories/${memory.id}/state`, { method: "PATCH", body: JSON.stringify({ trash: true }) }); state.memories = state.memories.filter(item => item.id !== memory.id); renderSettings(); });
  if ($("#emptyAddProvider")) $("#emptyAddProvider").onclick = () => { $("#providerForm").hidden = false; renderSettings(); };
}

function fillPersonaConfig(form,config={}){const tools=config.tools||{};form.elements.startup_chat.value=config.startup_chat==="new"?"new":"resume";form.elements.memory_enabled.checked=config.memory_enabled!==false;form.elements.history_enabled.checked=config.history_enabled!==false;form.elements.summary_frequency.value=config.summary_frequency||20;form.elements.quick_phrases.value=(config.quick_phrases||[]).join("\n");form.elements.persona_headers.value=JSON.stringify(config.custom_headers||{},null,2);form.elements.persona_body.value=JSON.stringify(config.custom_body||{},null,2);form.elements.regex_rules.value=JSON.stringify(config.regex_rules||[],null,2);form.elements.tool_time.checked=tools.time!==false;form.elements.tool_clipboard.checked=!!tools.clipboard;form.elements.tool_tts.checked=!!tools.tts;form.elements.tool_ask_user.checked=tools.ask_user!==false;form.elements.tool_calculator.checked=tools.calculator!==false;form.elements.mcp_servers.value=(config.mcp_servers||[]).join("\n");}
function personaConfigFromForm(form){let custom_headers,custom_body,regex_rules;try{custom_headers=JSON.parse(form.elements.persona_headers.value||"{}");custom_body=JSON.parse(form.elements.persona_body.value||"{}");regex_rules=JSON.parse(form.elements.regex_rules.value||"[]");}catch(error){throw new Error(`人格高级配置 JSON 格式错误：${error.message}`);}if(!custom_headers||Array.isArray(custom_headers)||typeof custom_headers!=="object")throw new Error("自定义 Header 必须是 JSON 对象");if(!custom_body||Array.isArray(custom_body)||typeof custom_body!=="object")throw new Error("自定义 Body 必须是 JSON 对象");if(!Array.isArray(regex_rules))throw new Error("正则规则必须是 JSON 数组");return {startup_chat:form.elements.startup_chat.value,memory_enabled:form.elements.memory_enabled.checked,history_enabled:form.elements.history_enabled.checked,summary_frequency:Number(form.elements.summary_frequency.value||20),quick_phrases:form.elements.quick_phrases.value.split("\n").map(item=>item.trim()).filter(Boolean),custom_headers,custom_body,regex_rules,tools:{time:form.elements.tool_time.checked,clipboard:form.elements.tool_clipboard.checked,tts:form.elements.tool_tts.checked,ask_user:form.elements.tool_ask_user.checked,calculator:form.elements.tool_calculator.checked},mcp_servers:form.elements.mcp_servers.value.split("\n").map(item=>item.trim()).filter(Boolean)};}
function resetPersonaForm(){const form=$("#personaForm");form.reset();fillPersonaConfig(form,{});delete form.dataset.editing;$("#savePersona").textContent="保存人格";$("#cancelPersonaEdit").hidden=true;}

function updateProviderCacheUI() {
  const explicit = $("#providerProtocol").value === "anthropic";
  $("#promptCacheControl").hidden = !explicit;
  $("#automaticCacheHint").hidden = explicit;
}

function startupConversationPlan(persona,conversations){const mode=persona?.config?.startup_chat==="new"?"new":"resume",recent=conversations.find(item=>(item.persona_id||null)===(persona?.id||null));return {mode,conversationId:recent?.id||null};}

async function bootstrap() {
  Object.assign(state, await api("/api/bootstrap"));
  state.memories = await api("/api/memories");
  state.favorites = await api("/api/favorites");
  state.provider = state.providers[0]?.id || null;const storedPersona=localStorage.getItem("atherloom:last-persona");state.persona=state.personas.some(item=>item.id===storedPersona)?storedPersona:state.personas[0]?.id||null;
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
  $("#memoryStrategy").value = state.settings.memory_strategy || "hybrid";
  document.querySelectorAll("[data-permission]").forEach(select => select.value = state.settings.tool_permissions?.[select.dataset.permission] || "ask");
  applyAppearance();
  renderProfile(); renderTimeGreeting(); renderHistory(); renderSettings(); renderPickers();
  const startup=startupConversationPlan(activePersona(),state.conversations);
  if(startup.mode==="new"&&state.provider)await newConversation();else if(startup.conversationId)await openConversation(startup.conversationId);
}

function renderFavorites() {
  $("#favoriteList").innerHTML=state.favorites.map(item=>`<article class="favorite-card"><div class="favorite-meta"><span>${item.role==="user"?"用户":"助手"}</span><span>${escapeHtml(item.conversation_title_snapshot||"未命名对话")}</span><time>${new Date(item.original_message_created_at).toLocaleString()}</time></div><p>${escapeHtml(item.text_snapshot)}</p><button class="ghost" data-remove-favorite="${item.source_message_id}">取消珍藏</button></article>`).join("")||`<div class="game-empty"><span>☆</span><h3>还没有珍藏</h3><p>在任意消息下点击“☆ 珍藏”。</p></div>`;
  document.querySelectorAll("[data-remove-favorite]").forEach(button=>button.onclick=async()=>{await api(`/api/favorites/${button.dataset.removeFavorite}?owner=user`,{method:"DELETE"});state.favorites=await api(`/api/favorites?q=${encodeURIComponent($("#favoriteSearch").value)}`);renderFavorites();renderMessages();});
}

async function openFavorites(){state.favorites=await api("/api/favorites");renderFavorites();$("#favoritesSpace").hidden=false;}
function openMedia(mode){$("#mediaSpace").hidden=false;$("#readingRoom").hidden=mode!=="reading";$("#cinemaRoom").hidden=mode!=="cinema";$("#mediaTitle").textContent=mode==="reading"?"一起读书":"一起看电影";}
const callState={active:false,recognition:null};
function callLine(role,text){$("#callTranscript").insertAdjacentHTML("beforeend",`<p class="${role}"><b>${role==="user"?"你":"AI"}</b>${escapeHtml(text)}</p>`);$("#callTranscript").scrollTop=$("#callTranscript").scrollHeight;}
async function callTurn(content){const provider=activeProvider();if(!provider)throw new Error("请先添加并选择 API 线路");if(!state.current)await newConversation();callLine("user",content);$("#callStatus").textContent="正在思考…";const response=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({conversation_id:state.current,content,provider_id:provider.id,persona_id:state.persona,local_time:localTimeContext()})});const reader=response.body.getReader(),decoder=new TextDecoder();let pending="",reply="";while(true){const {value,done}=await reader.read();if(done)break;pending+=decoder.decode(value,{stream:true});const lines=pending.split("\n");pending=lines.pop();for(const line of lines){if(!line)continue;const event=JSON.parse(line);if(event.error)throw new Error(event.error);if(event.delta)reply+=event.delta;}}callLine("assistant",reply);if(!callState.active)return;$("#callStatus").textContent="正在朗读…";const utterance=new SpeechSynthesisUtterance(reply);utterance.lang="zh-CN";utterance.onend=()=>{if(callState.active){$("#callStatus").textContent="正在听…";try{callState.recognition.start();}catch{}}};speechSynthesis.speak(utterance);}
async function startVoiceCall(){const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;if(!Recognition)throw new Error("当前浏览器没有系统语音识别能力");const stream=await navigator.mediaDevices.getUserMedia({audio:true});stream.getTracks().forEach(track=>track.stop());callState.active=true;callState.recognition=new Recognition();callState.recognition.lang="zh-CN";callState.recognition.interimResults=false;callState.recognition.onresult=event=>callTurn(event.results[event.results.length-1][0].transcript).catch(error=>{$("#callStatus").textContent=error.message;});callState.recognition.onerror=event=>{if(callState.active)$("#callStatus").textContent=`没有听清：${event.error}`;};callState.recognition.start();$("#callStatus").textContent="正在听…";$("#startCall").disabled=true;$("#endCall").disabled=false;}
function endVoiceCall(){callState.active=false;callState.recognition?.abort();speechSynthesis.cancel();$("#callStatus").textContent="通话已结束";$("#startCall").disabled=false;$("#endCall").disabled=true;}
function openVoiceCall(){if(window.AtherloomNative?.showNotice){window.AtherloomNative.showNotice("Android 测试版暂未接通稳定语音，已阻止会卡死的通话页。");return;}$("#callSpace").hidden=false;}

async function newConversation() {
  const conversation = await api("/api/conversations", { method: "POST", body: JSON.stringify({ provider_id: state.provider, persona_id: state.persona }) });
  state.conversations.unshift(conversation); state.current = conversation.id; state.messages = [];
  if(state.persona)localStorage.setItem("atherloom:last-persona",state.persona);localStorage.setItem("atherloom:last-conversation",conversation.id);
  $("#titleButton").textContent = "新对话⌄"; renderHistory(); renderMessages();
}

async function openConversation(id) {
  state.current = id; const conversation = state.conversations.find(c => c.id === id);
  state.provider = conversation.provider_id || state.provider; state.persona = conversation.persona_id || state.persona;
  if(state.persona)localStorage.setItem("atherloom:last-persona",state.persona);localStorage.setItem("atherloom:last-conversation",id);
  state.messages = await api(`/api/conversations/${id}/messages`);
  state.version_selection={};for(const message of state.messages)if(message.role==="assistant"&&message.parent_message_id&&message.selected){const versions=state.messages.filter(item=>item.role==="assistant"&&item.parent_message_id===message.parent_message_id);state.version_selection[message.parent_message_id]=versions.indexOf(message);}
  $("#titleButton").textContent = `${conversation.title}⌄`; renderHistory(); renderMessages(); renderPickers();
}

async function sendMessage() {
  const input = $("#prompt"); const content = input.value.trim(); const provider = activeProvider();
  if ((!content&&!state.attachments.length) || state.busy) return; if (!provider) return openSettings("providers"); if (!state.current) await newConversation();
  const attachments=state.attachments.splice(0);renderAttachments();const visibleContent=content||"请查看附件";
  state.busy = true; input.value = ""; input.style.height = "auto"; $("#send").disabled = true;
  state.messages.push({ role: "user", content:visibleContent,attachments }); renderMessages();
  await generateReply(visibleContent,null,attachments);
}

let streamScrollFrame=0,streamScrollDue=0;
function scheduleStreamingScroll(){const now=performance.now();if(streamScrollFrame||now<streamScrollDue)return;streamScrollFrame=requestAnimationFrame(()=>{streamScrollFrame=0;streamScrollDue=performance.now()+120;const area=$("#chatScroll");area.scrollTop=area.scrollHeight;});}
function updateStreamingMessage(message) {
  const index=state.messages.indexOf(message),article=document.querySelector(`.message[data-index="${index}"]`);
  if(!article){renderMessages();return;}
  const body=article.querySelector(".message-body"),bubble=article.querySelector(".bubble");
  if(message.memory_sources?.length){let sources=article.querySelector(".memory-sources");if(!sources){sources=document.createElement("div");sources.className="memory-sources";body.insertBefore(sources,body.firstChild);}sources.innerHTML=`本轮使用记忆：${message.memory_sources.map(source=>`<span>${escapeHtml(source.title)}</span>`).join("")}`;}
  if(message.reasoning){let thinking=article.querySelector(".thinking");if(!thinking){thinking=document.createElement("details");thinking.className="thinking";thinking.open=true;thinking.innerHTML="<summary>思考过程</summary><div></div>";body.insertBefore(thinking,bubble);}const reasoning=thinking.querySelector("div");if(reasoning.textContent!==message.reasoning)reasoning.textContent=message.reasoning;}
  if(bubble){if(message.role==="assistant"&&message.streaming){if(message.content){if(bubble.childNodes.length===1&&bubble.firstChild?.nodeType===Node.TEXT_NODE)bubble.firstChild.nodeValue=message.content;else bubble.replaceChildren(document.createTextNode(message.content));}}else bubble.innerHTML=message.role==="assistant"?renderAssistantContent(message.content):escapeHtml(message.content);}
  scheduleStreamingScroll();
}

function createStreamPresenter(message, animated) {
  let queue=[],timer=null,ended=false,resolveFinished;
  const finishTimer=()=>{if(timer){clearInterval(timer);timer=null;}if(resolveFinished){resolveFinished();resolveFinished=null;}};
  const tick=()=>{if(!queue.length){if(ended)finishTimer();return;}const count=!animated?queue.length:1;message.content+=queue.splice(0,count).join("");message.pending=false;updateStreamingMessage(message);if(ended&&!queue.length)finishTimer();};
  return {
    push(text){if(!text)return;queue.push(...Array.from(text));if(!animated){tick();return;}if(!timer){tick();const delay={slow:90,standard:55,fast:30}[state.settings.stream_speed]||55;timer=setInterval(tick,delay);}},
    finish(){ended=true;tick();if(!timer&&!queue.length)return Promise.resolve();return new Promise(resolve=>{resolveFinished=resolve;});}
  };
}

async function generateReply(content, reuseUserMessageId = null, attachments = []) {
  const input = $("#prompt"); const provider = activeProvider();
  if (!provider) return openSettings("providers");
  state.busy = true;
  if(reuseUserMessageId)delete state.version_selection[reuseUserMessageId];
  state.messages.push({ role: "assistant", content: "", reasoning: "", model: provider.model, parent_message_id: reuseUserMessageId, pending: true, streaming: provider.stream_enabled!==false&&provider.stream_enabled!==0 }); renderMessages();
  const assistant = state.messages[state.messages.length - 1];
  try {
    const gameContext=reuseUserMessageId?"":await prepareChatGameContext(content);
    const response = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conversation_id: state.current, content: content || "重新生成", attachments, provider_id: provider.id, persona_id: state.persona, reuse_user_message_id: reuseUserMessageId, local_time: localTimeContext(), game_context:gameContext }) });
    if (!response.ok) throw new Error(`请求失败 ${response.status}`);
    const reader=response.body.getReader(),decoder=new TextDecoder(),presenter=createStreamPresenter(assistant,assistant.streaming);let pending="";
    while(true){const {value,done}=await reader.read();if(done)break;pending+=decoder.decode(value,{stream:true});const lines=pending.split("\n");pending=lines.pop();for(const line of lines){if(!line)continue;const event=JSON.parse(line);if(event.error)throw new Error(event.error);let structureUpdated=false;if(event.memory_sources){assistant.memory_sources=event.memory_sources;structureUpdated=true;}if(typeof event.delta==="string"&&event.delta!=="null")presenter.push(event.delta);if(typeof event.reasoning_delta==="string"&&event.reasoning_delta!=="null"){assistant.reasoning+=event.reasoning_delta;structureUpdated=true;}if(structureUpdated)updateStreamingMessage(assistant);if(event.done){await presenter.finish();assistant.pending=false;assistant.streaming=false;assistant.id=event.assistant_id;assistant.parent_message_id=event.user_id;const pendingUser=[...state.messages].reverse().find(m=>m.role==="user"&&!m.id);if(pendingUser)pendingUser.id=event.user_id;if(event.title){const conversation=state.conversations.find(c=>c.id===state.current);if(conversation)conversation.title=event.title;$("#titleButton").textContent=`${event.title}⌄`;renderHistory();}renderMessages();}}}
  } catch (error) { assistant.pending=false;assistant.streaming=false;assistant.content = `连接失败：${error.message}`; renderMessages(); }
  state.busy = false; $("#send").disabled = !input.value.trim();
}

function openSettings(tab = "providers") { $("#backdrop").hidden = false; $("#settingsPanel").classList.add("open"); $("#settingsPanel").setAttribute("aria-hidden", "false"); switchTab(tab); }
function closeSettings() { $("#settingsPanel").classList.remove("open"); $("#settingsPanel").setAttribute("aria-hidden", "true"); $("#backdrop").hidden = true; }
function switchTab(tab) { document.querySelectorAll(".settings-nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab)); document.querySelectorAll(".tab").forEach(s => s.classList.toggle("active", s.id === `tab-${tab}`)); }
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
  current.title = saved.title; $("#titleButton").textContent = `${saved.title}⌄`; renderHistory();
}

function openConversationSwitcher(event) {
  event.stopPropagation();
  const recent = state.conversations.filter(item => !item.archived).slice(0, 30);
  const items = `<button data-value="__new__"><strong>＋ 新对话</strong></button>${recent.map(item => `<button data-value="${item.id}" class="${item.id === state.current ? "active" : ""}"><strong>${escapeHtml(item.title)}</strong><small>${item.id === state.current ? "当前对话" : new Date(item.updated_at || item.created_at).toLocaleString("zh-CN")}</small></button>`).join("")}<button data-value="__rename__" ${state.current ? "" : "disabled"}>重命名当前对话</button>`;
  showPopover(event.currentTarget, $("#conversationPopover"), items, async value => {
    if (value === "__new__") await newConversation();
    else if (value === "__rename__") await renameCurrentConversation();
    else await openConversation(value);
  });
}

function shareConversation() {
  if (!state.messages.length) return;
  const title = state.conversations.find(c => c.id === state.current)?.title || "对话分享";
  const visible = state.messages.map(m => `## ${m.role === "user" ? "用户" : "助手"}\n\n${m.content}`).join("\n\n---\n\n");
  const blob = new Blob([`# ${title}\n\n${visible}\n`], { type: "text/markdown;charset=utf-8" });
  const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `${title.replace(/[\\/:*?\"<>|]/g, "-")}.md`; link.click(); URL.revokeObjectURL(link.href);
}

function exportLocalBackup() {
  const data = {};
  for (let index = 0; index < localStorage.length; index++) {
    const key = localStorage.key(index);
    if (!key?.startsWith("atherloom:")) continue;
    if (key === "atherloom:providers") {
      try { data[key] = JSON.stringify(JSON.parse(localStorage.getItem(key)).map(({ api_key, ...provider }) => provider)); } catch { /* skip malformed provider data */ }
    } else data[key] = localStorage.getItem(key);
  }
  const bundle = { format: "atherloom-backup", version: 1, exported_at: new Date().toISOString(), data };
  const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json;charset=utf-8" });
  const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `atherloom-backup-${new Date().toISOString().slice(0, 10)}.json`; link.click(); URL.revokeObjectURL(link.href);
  $("#backupState").textContent = "备份已导出；API Key 未包含。";
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
      font_scale: Number($("#fontScale").value),
      message_density: $("#messageDensity").value,
      code_theme: $("#codeTheme").value,
      memory_strategy: $("#memoryStrategy").value,
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

$("#prompt").addEventListener("input", e => { e.target.style.height = "auto"; e.target.style.height = `${Math.min(e.target.scrollHeight, 180)}px`; $("#send").disabled = !e.target.value.trim() || state.busy; renderContextUsage(); });
$("#attachmentButton").onclick=event=>{event.stopPropagation();$("#attachmentMenu").hidden=!$("#attachmentMenu").hidden;};document.querySelectorAll("[data-attachment-source]").forEach(button=>button.onclick=()=>{const inputs={camera:$("#cameraInput"),images:$("#imageInput"),files:$("#fileInput")};$("#attachmentMenu").hidden=true;inputs[button.dataset.attachmentSource].click();});[$("#cameraInput"),$("#imageInput"),$("#fileInput")].forEach(input=>input.onchange=async event=>{await addAttachments(event.target.files);event.target.value="";$("#send").disabled=false;});
$("#openGamesFromComposer").onclick=()=>{$("#attachmentMenu").hidden=true;openGameLibrary();};
$("#prompt").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
$("#send").onclick = sendMessage; $("#newChat").onclick = newConversation;
$("#titleButton").onclick = openConversationSwitcher;
let searchTimer;
$("#conversationSearch").oninput = event => { clearTimeout(searchTimer); searchTimer = setTimeout(async () => { const query = event.target.value.trim(); if (!query) { const fresh = await api("/api/bootstrap"); state.conversations = fresh.conversations; } else { state.conversations = await api(`/api/search?q=${encodeURIComponent(query)}`); } renderHistory(); }, 180); };
$("#autoTitleMode").onchange = saveAppSettings;
$("#summaryEnabled").onchange = saveAppSettings;
$("#proactiveQuestions").onchange = saveAppSettings;
$("#summaryRounds").oninput = event => { $("#summaryRoundsValue").textContent = `${event.target.value} 轮`; saveAppSettings(); };
$("#summaryPrompt").oninput = saveAppSettings;
$("#displayName").oninput = saveAppSettings;
$("#fontScale").oninput = event => { $("#fontScaleValue").textContent = `${event.target.value}%`; state.settings.font_scale = Number(event.target.value); applyAppearance(); saveAppSettings(); };
$("#messageDensity").onchange = event => { state.settings.message_density = event.target.value; applyAppearance(); saveAppSettings(); };
$("#streamSpeed").onchange=event=>{state.settings.stream_speed=event.target.value;saveAppSettings();};
$("#codeTheme").onchange = event => { state.settings.code_theme = event.target.value; applyAppearance(); saveAppSettings(); };
$("#memoryStrategy").onchange = saveAppSettings;
$("#resetSummaryPrompt").onclick = () => { $("#summaryPrompt").value = $("#summaryPrompt").dataset.defaultPrompt; saveAppSettings(); };
document.querySelectorAll("[data-permission]").forEach(select => select.onchange = saveAppSettings);
document.querySelectorAll("[data-bulk-permission]").forEach(button => button.onclick = () => {
  const permission = button.dataset.bulkPermission;
  document.querySelectorAll("[data-permission]").forEach(select => {
    select.value = select.dataset.permission === "delete" && permission === "allow" ? "ask" : permission;
  });
  saveAppSettings();
});
$("#openSettings").onclick = () => openSettings();
$("#openGames").onclick = openGameLibrary; $("#closeGames").onclick = () => $("#gameLibrary").hidden = true;
$("#openFavorites").onclick=openFavorites;$("#closeFavorites").onclick=()=>$("#favoritesSpace").hidden=true;
$("#openReading").onclick=()=>openMedia("reading");$("#openCinema").onclick=()=>openMedia("cinema");$("#closeMedia").onclick=()=>{$("#mediaSpace").hidden=true;$("#moviePlayer").pause();};
$("#openCall").onclick=openVoiceCall;$("#closeCall").onclick=()=>{endVoiceCall();$("#callSpace").hidden=true;};$("#startCall").onclick=()=>startVoiceCall().catch(error=>{$("#callStatus").textContent=`无法开始：${error.message}`;});$("#endCall").onclick=endVoiceCall;
$("#closeMessageMenu").onclick=()=>$("#messageMenu").hidden=true;$("#messageMenu").onclick=event=>{if(event.target===$("#messageMenu"))$("#messageMenu").hidden=true;};
$("#branchMessage").onclick=async()=>{const message=state.messages[Number($("#messageMenu").dataset.messageIndex)];$("#messageMenu").hidden=true;if(!message?.id)return;try{const conversation=await api(`/api/conversations/${state.current}/branch/${message.id}`,{method:"POST"});state.conversations.unshift(conversation);renderHistory();await openConversation(conversation.id);}catch(error){alert(`创建分支失败：${error.message}`);}};
$("#editMessage").onclick=()=>{const message=state.messages[Number($("#messageMenu").dataset.messageIndex)];$("#messageMenu").hidden=true;openMessageEditor(message);};
$("#cancelMessageEdit").onclick=()=>$("#messageEditor").hidden=true;$("#messageEditor").onclick=event=>{if(event.target===$("#messageEditor"))$("#messageEditor").hidden=true;};
$("#messageEditor form").onsubmit=async event=>{event.preventDefault();const id=$("#messageEditor").dataset.messageId,content=$("#messageEditContent").value.trim(),message=state.messages.find(item=>item.id===id);if(!message||!content)return;const saved=await api(`/api/messages/${id}`,{method:"PATCH",body:JSON.stringify({content})});message.content=saved.content;$("#messageEditor").hidden=true;renderMessages();};
$("#deleteMessageVersion").onclick=async()=>{const index=Number($("#messageMenu").dataset.messageIndex),message=state.messages[index];if(!message?.id)return;const note=message.role==="user"?"删除这条消息时，它下面的全部 AI 回答也会删除。":"只删除当前显示的这个 AI 回答版本。";if(!confirm(`${note}\n\n确定继续吗？`))return;await api(`/api/messages/${message.id}`,{method:"DELETE"});state.messages=state.messages.filter(item=>item.id!==message.id&&(message.role!=="user"||item.parent_message_id!==message.id));if(message.parent_message_id)delete state.version_selection[message.parent_message_id];if(message.role==="user")delete state.version_selection[message.id];$("#messageMenu").hidden=true;renderMessages();};
$("#deleteAllMessageVersions").onclick=async()=>{const index=Number($("#messageMenu").dataset.messageIndex),message=state.messages[index];if(!message?.id)return;const parentId=message.role==="assistant"?message.parent_message_id:message.id,note=message.role==="assistant"?"删除这条提问下的全部 AI 回答版本？你的提问会保留。":"删除你的这条消息以及它下面的全部 AI 回答？";if(!confirm(note))return;await api(`/api/messages/${message.id}/versions`,{method:"DELETE"});state.messages=state.messages.filter(item=>message.role==="assistant"?item.parent_message_id!==parentId:item.id!==message.id&&item.parent_message_id!==message.id);delete state.version_selection[parentId];$("#messageMenu").hidden=true;renderMessages();};
$("#chooseBook").onclick=()=>$("#bookInput").click();$("#bookInput").onchange=async event=>{const file=event.target.files?.[0];event.target.value="";await openLocalBook(file);};
let movieUrl;$("#chooseMovie").onclick=()=>$("#movieInput").click();$("#movieInput").onchange=event=>{const file=event.target.files?.[0];if(!file)return;if(movieUrl)URL.revokeObjectURL(movieUrl);movieUrl=URL.createObjectURL(file);const player=$("#moviePlayer"),key=`atherloom:movie:${file.name}:${file.size}`;player.src=movieUrl;$("#movieStatus").textContent=`${file.name} · 进度保存在本机`;player.onloadedmetadata=()=>{player.currentTime=Math.min(Number(localStorage.getItem(key)||0),Math.max(0,player.duration-1));};player.ontimeupdate=()=>{if(Math.floor(player.currentTime)%5===0)localStorage.setItem(key,String(player.currentTime));};};
let favoriteSearchTimer;$("#favoriteSearch").oninput=event=>{clearTimeout(favoriteSearchTimer);favoriteSearchTimer=setTimeout(async()=>{state.favorites=await api(`/api/favorites?q=${encodeURIComponent(event.target.value.trim())}`);renderFavorites();},220);};
document.querySelectorAll("[data-game-action]").forEach(button => button.onclick = () => playGame(button.dataset.gameAction, Number(button.dataset.amount || 1)));
document.querySelectorAll("[data-claw-action]").forEach(button=>button.onclick=()=>playMiniGame("claw_machine",button.dataset.clawAction));document.querySelectorAll("[data-slot-amount]").forEach(button=>button.onclick=()=>playMiniGame("cloud_slots","spin",Number(button.dataset.slotAmount)));
$("#aiPlayOne").onclick=()=>aiPlayGame(1);$("#aiPlayThree").onclick=()=>aiPlayGame(3);
$("#backdrop").onclick = closeSettings; document.querySelectorAll("[data-close]").forEach(b => b.onclick = closeSettings);
document.querySelectorAll(".settings-nav button").forEach(b => b.onclick = () => switchTab(b.dataset.tab));
$("#addProvider").onclick = () => { const form=$("#providerForm");delete form.dataset.editing;$("#providerEditState").hidden=true;form.reset();form.elements.custom_headers.value="{}";form.elements.temperature.value=.7;form.elements.top_p.value=1;form.elements.max_tokens.value=4096;form.elements.stream_enabled.checked=true;form.elements.enabled.checked=true;form.hidden = false; $("#connectionState").textContent = ""; renderSettings(); updateProviderCacheUI(); }; $("#cancelProvider").onclick = () => { const form=$("#providerForm");delete form.dataset.editing;$("#providerEditState").hidden=true;form.hidden = true; renderSettings(); };
$("#providerForm").onsubmit = async e => { e.preventDefault(); const data = Object.fromEntries(new FormData(e.target)); data.prompt_cache = e.target.elements.prompt_cache.checked; data.thinking_enabled=e.target.elements.thinking_enabled.checked;data.stream_enabled=e.target.elements.stream_enabled.checked;data.enabled=e.target.elements.enabled.checked;data.temperature=Number(data.temperature);data.top_p=Number(data.top_p);data.max_tokens=Number(data.max_tokens);const editing=e.target.dataset.editing; const saved = await api(editing?`/api/providers/${editing}`:"/api/providers", { method: editing?"PUT":"POST", body: JSON.stringify(data) });if(editing)Object.assign(state.providers.find(item=>item.id===editing),saved);else state.providers.push(saved); state.provider ||= saved.id; e.target.reset();delete e.target.dataset.editing; e.target.elements.custom_headers.value = "{}"; e.target.elements.prompt_cache.checked = true;e.target.elements.thinking_enabled.checked=true;e.target.elements.stream_enabled.checked=true;e.target.elements.enabled.checked=true;e.target.elements.temperature.value=.7;e.target.elements.top_p.value=1;e.target.elements.max_tokens.value=4096; e.target.hidden = true; renderSettings(); renderPickers(); };
$("#providerProtocol").onchange = event => { const form = $("#providerForm"); const presets = { deepseek: { name: "DeepSeek", base_url: "https://api.deepseek.com", model: "deepseek-v4-flash" }, glm: { name: "智谱 GLM", base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-5.2" } }; const preset = presets[event.target.value]; if (preset) for (const [key, value] of Object.entries(preset)) if (!form.elements[key].value) form.elements[key].value = value; updateProviderCacheUI(); };
$("#toggleApiKey").onclick = () => { const input = $("#providerForm").elements.api_key; input.type = input.type === "password" ? "text" : "password"; };
$("#pasteApiKey").onclick=async()=>{const input=$("#providerForm").elements.api_key;try{const value=window.AtherloomNative?.getClipboard?window.AtherloomNative.getClipboard():await navigator.clipboard.readText();if(!value)throw new Error("剪贴板为空");input.value=value.trim();$("#connectionState").className="connection-state success";$("#connectionState").textContent="已从剪贴板粘贴";}catch(error){$("#connectionState").className="connection-state error";$("#connectionState").textContent=`无法读取剪贴板：${error.message}`;}};
$("#fetchModels").onclick=async()=>{const form=$("#providerForm"),status=$("#connectionState"),data=Object.fromEntries(new FormData(form));if(!data.base_url){form.elements.base_url.reportValidity();return;}status.className="connection-state";status.textContent="正在拉取模型…";try{const result=await api("/api/providers/models",{method:"POST",body:JSON.stringify(data)}),models=result.models||[];showFetchedModels(models,form);status.classList.add("success");status.textContent=models.length?`已读取 ${models.length} 个模型，请在下方选择`:`线路已响应，但没有返回模型`; }catch(error){status.classList.add("error");status.textContent=`拉取失败：${error.message}；仍可手动填写模型 ID`;}};
$("#providerModelSelect").onchange=event=>{if(event.target.value)$("#providerForm").elements.model.value=event.target.value;};
$("#testProvider").onclick = async () => { const form = $("#providerForm"); if (!form.reportValidity()) return; const data = Object.fromEntries(new FormData(form)); data.prompt_cache = form.elements.prompt_cache.checked; const status = $("#connectionState"); status.className = "connection-state"; status.textContent = "正在测试连接…"; try { const result = await api("/api/providers/test", { method: "POST", body: JSON.stringify(data) }); status.classList.add("success"); status.textContent = result.message; } catch (error) { status.classList.add("error"); status.textContent = error.message; } };
$("#personaForm").onsubmit = async e => {e.preventDefault();try{const data={name:e.target.elements.name.value.trim(),prompt:e.target.elements.prompt.value,config:personaConfigFromForm(e.target)},editing=e.target.dataset.editing,saved=await api(editing?`/api/personas/${editing}`:"/api/personas",{method:editing?"PUT":"POST",body:JSON.stringify(data)});if(editing)Object.assign(state.personas.find(item=>item.id===editing),saved);else state.personas.push(saved);state.persona||=saved.id;resetPersonaForm();renderSettings();renderPickers();}catch(error){alert(error.message);}};
$("#cancelPersonaEdit").onclick=resetPersonaForm;
document.querySelectorAll("[data-persona-tab]").forEach(button=>button.onclick=()=>{document.querySelectorAll("[data-persona-tab]").forEach(item=>item.classList.toggle("active",item===button));document.querySelectorAll("[data-persona-pane]").forEach(pane=>pane.classList.toggle("active",pane.dataset.personaPane===button.dataset.personaTab));});
$("#memoryForm").onsubmit = async e => { e.preventDefault(); const form = e.target; const data = Object.fromEntries(new FormData(form)); const editing = form.dataset.editing; const saved = await api(editing ? `/api/memories/${editing}` : "/api/memories", { method: editing ? "PUT" : "POST", body: JSON.stringify(data) }); if (editing) Object.assign(state.memories.find(item => item.id === editing), saved); else state.memories.unshift(saved); form.reset(); delete form.dataset.editing; $("#saveMemory").textContent = "添加记忆"; $("#cancelMemoryEdit").hidden = true; renderSettings(); };
$("#cancelMemoryEdit").onclick = () => { const form = $("#memoryForm"); form.reset(); delete form.dataset.editing; $("#saveMemory").textContent = "添加记忆"; $("#cancelMemoryEdit").hidden = true; };
let memorySearchTimer;
$("#memorySearch").oninput = event => { clearTimeout(memorySearchTimer); memorySearchTimer = setTimeout(async () => { state.memories = await api(`/api/memories?q=${encodeURIComponent(event.target.value.trim())}`); renderSettings(); }, 180); };
$("#memoryKindFilter").onchange = renderSettings;
$("#modelPicker").onclick = e => { e.stopPropagation(); if (!state.providers.length) return openSettings("providers"); showPopover(e.currentTarget, $("#modelPopover"), state.providers.map(p => `<button data-value="${p.id}"><strong>${escapeHtml(p.name)}</strong><small>${escapeHtml(p.model)}</small></button>`).join(""), id => { state.provider = id; renderPickers(); }); };
$("#personaPicker").onclick = e => { e.stopPropagation(); showPopover(e.currentTarget, $("#personaPopover"), `<button data-value="">默认人格</button>` + state.personas.map(p => `<button data-value="${p.id}">${escapeHtml(p.name)}</button>`).join(""), id => { state.persona = id || null;if(state.persona)localStorage.setItem("atherloom:last-persona",state.persona);else localStorage.removeItem("atherloom:last-persona");renderPickers(); }); };
$("#quickPhraseButton").onclick=e=>{e.stopPropagation();const phrases=activePersona()?.config?.quick_phrases||[];showPopover(e.currentTarget,$("#quickPhrasePopover"),phrases.map((phrase,index)=>`<button data-value="${index}">${escapeHtml(phrase)}</button>`).join(""),index=>{const input=$("#prompt"),phrase=phrases[Number(index)];input.value=`${input.value}${input.value&&!/\s$/.test(input.value)?"\n":""}${phrase||""}`;input.dispatchEvent(new Event("input"));input.focus();});};
document.addEventListener("click", event => { if (!event.target.closest(".popover")) closePopovers(); if(!event.target.closest("#attachmentMenu")&&!event.target.closest("#attachmentButton"))$("#attachmentMenu").hidden=true; });
document.addEventListener("keydown", event => { if (event.key === "Escape") closePopovers(); });
function setSidebar(open){$("#sidebar").classList.toggle("open",open);$("#sidebarBackdrop").hidden=!open;}
$("#mobileMenu").onclick=()=>setSidebar(true);$("#sidebarClose").onclick=()=>setSidebar(false);$("#sidebarToggle").onclick=()=>{if(innerWidth<=760)setSidebar(false);};$("#sidebarBackdrop").onclick=()=>setSidebar(false);document.querySelectorAll("#sidebar .new-chat,#sidebar .profile-row,#sidebar .history-item").forEach(button=>button.addEventListener("click",()=>setSidebar(false)));
window.AtherloomHandleBack=()=>{if(!$("#callSpace").hidden){endVoiceCall();$("#callSpace").hidden=true;return true;}if(!$("#mediaSpace").hidden){$("#mediaSpace").hidden=true;$("#moviePlayer").pause();return true;}if(!$("#favoritesSpace").hidden){$("#favoritesSpace").hidden=true;return true;}if(!$("#gameLibrary").hidden){$("#gameLibrary").hidden=true;return true;}if($("#settingsPanel").classList.contains("open")){closeSettings();return true;}if($("#sidebar").classList.contains("open")){setSidebar(false);return true;}if([...document.querySelectorAll(".popover")].some(item=>!item.hidden)){closePopovers();return true;}return false;};
$("#themeSelect").onchange = e => { document.documentElement.dataset.theme = e.target.value === "system" ? "" : e.target.value; localStorage.setItem("theme", e.target.value); };
$("#exportBackup").onclick = exportLocalBackup;
$("#chooseBackup").onclick = () => $("#backupFile").click();
$("#backupFile").onchange = async event => { const file = event.target.files?.[0]; if (!file) return; try { await restoreLocalBackup(file); } catch (error) { $("#backupState").textContent = `恢复失败：${error.message}`; } finally { event.target.value = ""; } };
const theme = localStorage.getItem("theme") || "system"; $("#themeSelect").value = theme; if (theme !== "system") document.documentElement.dataset.theme = theme;
bootstrap().catch(error => { console.error(error); openSettings("providers"); });
updateProviderCacheUI();
setInterval(renderTimeGreeting, 60_000);
