const $ = (s) => document.querySelector(s);
const state = { providers: [], personas: [], conversations: [], memories: [], settings: { auto_title_mode: "local", tool_permissions: {} }, current: null, provider: null, persona: null, messages: [], busy: false };
const gameState = { catalog: [], current: null, fishing: null, waters: {} };

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `请求失败 ${response.status}`);
  return response.json();
}

function escapeHtml(value) { const div = document.createElement("div"); div.textContent = value; return div.innerHTML; }
function activeProvider() { return state.providers.find(p => p.id === state.provider) || state.providers[0]; }
function activePersona() { return state.personas.find(p => p.id === state.persona); }

function renderHistory() {
  const group = (label, items) => items.length ? `<div class="history-group"><div class="history-label">${label}</div>${items.map(c => `<div class="history-row ${c.id === state.current ? "active" : ""}"><button class="history-item" data-id="${c.id}">${escapeHtml(c.title)}</button><div class="history-actions"><button data-history-action="star" data-id="${c.id}" title="星标">${c.starred ? "★" : "☆"}</button><button data-history-action="pin" data-id="${c.id}" title="置顶">${c.pinned ? "●" : "○"}</button><button data-history-action="archive" data-id="${c.id}" title="${c.archived ? "取消归档" : "归档"}">⌑</button></div></div>`).join("")}</div>` : "";
  const active = state.conversations.filter(c => !c.archived);
  const pinned = active.filter(c => c.pinned);
  const starred = active.filter(c => c.starred && !c.pinned);
  const recent = active.filter(c => !c.pinned && !c.starred);
  const archived = state.conversations.filter(c => c.archived);
  $("#history").innerHTML = group("置顶", pinned) + group("星标", starred) + group("最近", recent) + group("已归档", archived) || `<p class="muted" style="padding:8px 11px">还没有对话</p>`;
  document.querySelectorAll(".history-item").forEach(button => button.onclick = () => openConversation(button.dataset.id));
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

async function openGame(gameId) {
  gameState.current = gameId; renderGameCards();
  if (gameId !== "quiet_fishing") { $("#gameEmpty").hidden = false; $("#fishingStage").hidden = true; const game = gameState.catalog.find(item => item.id === gameId); $("#gameEmpty").innerHTML = `<span>${game.icon}</span><h3>${escapeHtml(game.name)}</h3><p>${escapeHtml(game.description)}</p>`; return; }
  const payload = await api(`/api/games/${gameId}/state${personaQuery()}`); gameState.fishing = payload.state; gameState.waters = payload.waters;
  $("#gameEmpty").hidden = true; $("#fishingStage").hidden = false; renderFishing();
}

async function playGame(action, amount = 1, target = "") {
  try {
    const payload = await api(`/api/games/quiet_fishing/action${personaQuery()}`, { method: "POST", body: JSON.stringify({ action, amount, target }) });
    gameState.fishing = payload.state; renderFishing();
  } catch (error) { alert(error.message); }
}

async function openGameLibrary() {
  $("#gameLibrary").hidden = false;
  if (!gameState.catalog.length) gameState.catalog = await api("/api/games");
  renderGameCards(); if (!gameState.current) openGame("quiet_fishing");
}

function renderMessages() {
  $("#welcome").hidden = state.messages.length > 0;
  $("#messages").innerHTML = state.messages.map((m, index) => `<article class="message ${m.role}" data-index="${index}">
    <div class="message-body">${m.memory_sources?.length ? `<div class="memory-sources">本轮使用记忆：${m.memory_sources.map(source => `<span>${escapeHtml(source.title)}</span>`).join("")}</div>` : ""}${m.reasoning ? `<details class="thinking"><summary>思考过程</summary><div>${escapeHtml(m.reasoning)}</div></details>` : ""}<div class="bubble">${escapeHtml(m.content)}</div></div>
    <div class="message-actions"><button data-action="copy">复制</button>${m.id ? `<button data-action="branch">分支</button>` : ""}${m.role === "assistant" && m.parent_message_id ? `<button data-action="regenerate">重新 Roll</button>` : ""}</div>
    ${m.role === "assistant" && m.model ? `<div class="message-meta">${escapeHtml(m.model)}</div>` : ""}</article>`).join("");
  document.querySelectorAll(".message-actions button").forEach(button => button.onclick = () => handleMessageAction(button.closest(".message"), button.dataset.action));
  $("#chatScroll").scrollTop = $("#chatScroll").scrollHeight;
}

async function handleMessageAction(article, action) {
  const message = state.messages[Number(article.dataset.index)];
  if (action === "copy") return navigator.clipboard.writeText(message.content);
  if (action === "branch") {
    const conversation = await api(`/api/conversations/${state.current}/branch/${message.id}`, { method: "POST" });
    state.conversations.unshift(conversation); renderHistory(); return openConversation(conversation.id);
  }
  if (action === "regenerate") return generateReply("", message.parent_message_id);
}

function renderPickers() {
  const provider = activeProvider(); const persona = activePersona();
  $("#modelPicker").textContent = provider ? `${provider.name} · ${provider.model}⌄` : "选择模型⌄";
  $("#personaPicker").textContent = persona ? `${persona.name}⌄` : "默认人格⌄";
}

function renderSettings() {
  $("#providerList").innerHTML = state.providers.map(p => `<div class="list-card"><div><strong>${escapeHtml(p.name)}</strong><small>${escapeHtml(p.protocol)} · ${escapeHtml(p.model)} · ${p.has_api_key ? "Key 已保存" : "无 Key"}</small></div><button data-delete-provider="${p.id}">删除</button></div>`).join("") || ($("#providerForm").hidden ? `<div class="empty-provider"><p class="muted">还没有 API 线路。</p><button class="primary" id="emptyAddProvider">添加第一条线路</button></div>` : "");
  $("#personaList").innerHTML = state.personas.map(p => `<div class="list-card"><div><strong>${escapeHtml(p.name)}</strong><small>${escapeHtml(p.prompt.slice(0, 70) || "空白人格")}</small></div></div>`).join("");
  const kindLabels = { fact: "事实", preference: "偏好", relationship: "关系", promise: "承诺", event: "事件", emotion: "情感", summary: "摘要", diary: "日记", other: "其他" };
  const kindFilter = $("#memoryKindFilter")?.value || "";
  const visibleMemories = state.memories.filter(memory => !kindFilter || memory.kind === kindFilter);
  $("#memoryList").innerHTML = visibleMemories.map(memory => `<div class="list-card memory-card"><div><strong>${memory.starred ? "★ " : ""}${escapeHtml(memory.title)}</strong><small>${kindLabels[memory.kind] || escapeHtml(memory.kind)} · 更新于 ${new Date(memory.updated_at).toLocaleString()}</small><p>${escapeHtml(memory.content.slice(0, 180))}</p></div><div><button data-memory-edit="${memory.id}">编辑</button><button data-memory-star="${memory.id}">${memory.starred ? "取消星标" : "星标"}</button><button data-memory-trash="${memory.id}">回收</button></div></div>`).join("") || `<p class="muted">没有符合条件的本地记忆。</p>`;
  document.querySelectorAll("[data-delete-provider]").forEach(b => b.onclick = async () => { await api(`/api/providers/${b.dataset.deleteProvider}`, { method: "DELETE" }); state.providers = state.providers.filter(p => p.id !== b.dataset.deleteProvider); if (state.provider === b.dataset.deleteProvider) state.provider = state.providers[0]?.id || null; renderSettings(); renderPickers(); });
  document.querySelectorAll("[data-memory-star]").forEach(b => b.onclick = async () => { const memory = state.memories.find(item => item.id === b.dataset.memoryStar); Object.assign(memory, await api(`/api/memories/${memory.id}/state`, { method: "PATCH", body: JSON.stringify({ starred: !memory.starred }) })); renderSettings(); });
  document.querySelectorAll("[data-memory-edit]").forEach(b => b.onclick = () => { const memory = state.memories.find(item => item.id === b.dataset.memoryEdit); const form = $("#memoryForm"); form.dataset.editing = memory.id; form.elements.title.value = memory.title; form.elements.kind.value = memory.kind; form.elements.content.value = memory.content; $("#saveMemory").textContent = "保存修改"; $("#cancelMemoryEdit").hidden = false; form.scrollIntoView({ behavior: "smooth", block: "center" }); });
  document.querySelectorAll("[data-memory-trash]").forEach(b => b.onclick = async () => { const memory = state.memories.find(item => item.id === b.dataset.memoryTrash); if (!confirm(`将“${memory.title}”移入回收站？`)) return; await api(`/api/memories/${memory.id}/state`, { method: "PATCH", body: JSON.stringify({ trash: true }) }); state.memories = state.memories.filter(item => item.id !== memory.id); renderSettings(); });
  if ($("#emptyAddProvider")) $("#emptyAddProvider").onclick = () => { $("#providerForm").hidden = false; renderSettings(); };
}

function updateProviderCacheUI() {
  const explicit = $("#providerProtocol").value === "anthropic";
  $("#promptCacheControl").hidden = !explicit;
  $("#automaticCacheHint").hidden = explicit;
}

async function bootstrap() {
  Object.assign(state, await api("/api/bootstrap"));
  state.memories = await api("/api/memories");
  state.provider = state.providers[0]?.id || null; state.persona = state.personas[0]?.id || null;
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
  $("#memoryStrategy").value = state.settings.memory_strategy || "hybrid";
  document.querySelectorAll("[data-permission]").forEach(select => select.value = state.settings.tool_permissions?.[select.dataset.permission] || "ask");
  applyAppearance();
  renderProfile(); renderHistory(); renderSettings(); renderPickers();
}

async function newConversation() {
  const conversation = await api("/api/conversations", { method: "POST", body: JSON.stringify({ provider_id: state.provider, persona_id: state.persona }) });
  state.conversations.unshift(conversation); state.current = conversation.id; state.messages = [];
  $("#titleButton").textContent = "新对话⌄"; renderHistory(); renderMessages();
}

async function openConversation(id) {
  state.current = id; const conversation = state.conversations.find(c => c.id === id);
  state.provider = conversation.provider_id || state.provider; state.persona = conversation.persona_id || state.persona;
  state.messages = await api(`/api/conversations/${id}/messages`);
  $("#titleButton").textContent = `${conversation.title}⌄`; renderHistory(); renderMessages(); renderPickers();
}

async function sendMessage() {
  const input = $("#prompt"); const content = input.value.trim(); const provider = activeProvider();
  if (!content || state.busy) return; if (!provider) return openSettings("providers"); if (!state.current) await newConversation();
  state.busy = true; input.value = ""; input.style.height = "auto"; $("#send").disabled = true;
  state.messages.push({ role: "user", content }); renderMessages();
  await generateReply(content);
}

async function generateReply(content, reuseUserMessageId = null) {
  const input = $("#prompt"); const provider = activeProvider();
  if (!provider) return openSettings("providers");
  state.busy = true;
  state.messages.push({ role: "assistant", content: "", reasoning: "", model: provider.model, parent_message_id: reuseUserMessageId }); renderMessages();
  const assistant = state.messages[state.messages.length - 1];
  try {
    const response = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conversation_id: state.current, content: content || "重新生成", provider_id: provider.id, persona_id: state.persona, reuse_user_message_id: reuseUserMessageId }) });
    if (!response.ok) throw new Error(`请求失败 ${response.status}`);
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let pending = "";
    while (true) { const { value, done } = await reader.read(); if (done) break; pending += decoder.decode(value, { stream: true }); const lines = pending.split("\n"); pending = lines.pop(); for (const line of lines) { if (!line) continue; const event = JSON.parse(line); if (event.error) throw new Error(event.error); if (event.memory_sources) assistant.memory_sources = event.memory_sources; if (event.delta) assistant.content += event.delta; if (event.reasoning_delta) assistant.reasoning += event.reasoning_delta; if (event.done) { assistant.id = event.assistant_id; assistant.parent_message_id = event.user_id; const pendingUser = [...state.messages].reverse().find(m => m.role === "user" && !m.id); if (pendingUser) pendingUser.id = event.user_id; if (event.title) { const conversation = state.conversations.find(c => c.id === state.current); if (conversation) conversation.title = event.title; $("#titleButton").textContent = `${event.title}⌄`; renderHistory(); } } renderMessages(); } }
  } catch (error) { assistant.content = `连接失败：${error.message}`; renderMessages(); }
  state.busy = false; $("#send").disabled = !input.value.trim();
}

function openSettings(tab = "providers") { $("#backdrop").hidden = false; $("#settingsPanel").classList.add("open"); $("#settingsPanel").setAttribute("aria-hidden", "false"); switchTab(tab); }
function closeSettings() { $("#settingsPanel").classList.remove("open"); $("#settingsPanel").setAttribute("aria-hidden", "true"); $("#backdrop").hidden = true; }
function switchTab(tab) { document.querySelectorAll(".settings-nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab)); document.querySelectorAll(".tab").forEach(s => s.classList.toggle("active", s.id === `tab-${tab}`)); }
function showPopover(target, popover, items, select) { const rect = target.getBoundingClientRect(); popover.innerHTML = items || `<button disabled>暂无可选项</button>`; popover.hidden = false; popover.style.left = `${Math.min(rect.left, innerWidth - 270)}px`; popover.style.bottom = `${innerHeight - rect.top + 8}px`; popover.querySelectorAll("button[data-value]").forEach(b => b.onclick = () => { select(b.dataset.value); popover.hidden = true; }); }

function shareConversation() {
  if (!state.messages.length) return;
  const title = state.conversations.find(c => c.id === state.current)?.title || "对话分享";
  const visible = state.messages.map(m => `## ${m.role === "user" ? "用户" : "助手"}\n\n${m.content}`).join("\n\n---\n\n");
  const blob = new Blob([`# ${title}\n\n${visible}\n`], { type: "text/markdown;charset=utf-8" });
  const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `${title.replace(/[\\/:*?\"<>|]/g, "-")}.md`; link.click(); URL.revokeObjectURL(link.href);
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
      font_scale: Number($("#fontScale").value),
      message_density: $("#messageDensity").value,
      code_theme: $("#codeTheme").value,
      memory_strategy: $("#memoryStrategy").value,
      tool_permissions
    }) });
    applyAppearance();
    renderProfile();
    $("#summarySaveState").textContent = "已保存到本地";
    $("#toolSaveState").textContent = "已保存到本地";
  }, 350);
}

$("#prompt").addEventListener("input", e => { e.target.style.height = "auto"; e.target.style.height = `${Math.min(e.target.scrollHeight, 180)}px`; $("#send").disabled = !e.target.value.trim() || state.busy; });
$("#prompt").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
$("#send").onclick = sendMessage; $("#newChat").onclick = newConversation;
$("#shareChat").onclick = shareConversation;
$("#titleButton").onclick = async () => { if (!state.current) return; const current = state.conversations.find(c => c.id === state.current); const title = window.prompt("重命名对话", current?.title || "新对话"); if (!title?.trim()) return; const saved = await api(`/api/conversations/${state.current}`, { method: "PATCH", body: JSON.stringify({ title: title.trim() }) }); current.title = saved.title; $("#titleButton").textContent = `${saved.title}⌄`; renderHistory(); };
let searchTimer;
$("#conversationSearch").oninput = event => { clearTimeout(searchTimer); searchTimer = setTimeout(async () => { const query = event.target.value.trim(); if (!query) { const fresh = await api("/api/bootstrap"); state.conversations = fresh.conversations; } else { state.conversations = await api(`/api/search?q=${encodeURIComponent(query)}`); } renderHistory(); }, 180); };
$("#autoTitleMode").onchange = saveAppSettings;
$("#summaryEnabled").onchange = saveAppSettings;
$("#summaryRounds").oninput = event => { $("#summaryRoundsValue").textContent = `${event.target.value} 轮`; saveAppSettings(); };
$("#summaryPrompt").oninput = saveAppSettings;
$("#displayName").oninput = saveAppSettings;
$("#fontScale").oninput = event => { $("#fontScaleValue").textContent = `${event.target.value}%`; state.settings.font_scale = Number(event.target.value); applyAppearance(); saveAppSettings(); };
$("#messageDensity").onchange = event => { state.settings.message_density = event.target.value; applyAppearance(); saveAppSettings(); };
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
$("#openSettings").onclick = () => openSettings(); $("#topSettings").onclick = () => openSettings(); $("#openMemory").onclick = () => openSettings("memory");
$("#openGames").onclick = openGameLibrary; $("#closeGames").onclick = () => $("#gameLibrary").hidden = true;
document.querySelectorAll("[data-game-action]").forEach(button => button.onclick = () => playGame(button.dataset.gameAction, Number(button.dataset.amount || 1)));
$("#backdrop").onclick = closeSettings; document.querySelectorAll("[data-close]").forEach(b => b.onclick = closeSettings);
document.querySelectorAll(".settings-nav button").forEach(b => b.onclick = () => switchTab(b.dataset.tab));
$("#addProvider").onclick = () => { $("#providerForm").hidden = false; $("#connectionState").textContent = ""; renderSettings(); updateProviderCacheUI(); }; $("#cancelProvider").onclick = () => { $("#providerForm").hidden = true; renderSettings(); };
$("#providerForm").onsubmit = async e => { e.preventDefault(); const data = Object.fromEntries(new FormData(e.target)); data.prompt_cache = e.target.elements.prompt_cache.checked; const saved = await api("/api/providers", { method: "POST", body: JSON.stringify(data) }); state.providers.push(saved); state.provider ||= saved.id; e.target.reset(); e.target.elements.custom_headers.value = "{}"; e.target.elements.prompt_cache.checked = true; e.target.hidden = true; renderSettings(); renderPickers(); };
$("#providerProtocol").onchange = event => { const form = $("#providerForm"); const presets = { deepseek: { name: "DeepSeek", base_url: "https://api.deepseek.com", model: "deepseek-v4-flash" }, glm: { name: "智谱 GLM", base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-5.2" } }; const preset = presets[event.target.value]; if (preset) for (const [key, value] of Object.entries(preset)) if (!form.elements[key].value) form.elements[key].value = value; updateProviderCacheUI(); };
$("#toggleApiKey").onclick = () => { const input = $("#providerForm").elements.api_key; input.type = input.type === "password" ? "text" : "password"; };
$("#testProvider").onclick = async () => { const form = $("#providerForm"); if (!form.reportValidity()) return; const data = Object.fromEntries(new FormData(form)); data.prompt_cache = form.elements.prompt_cache.checked; const status = $("#connectionState"); status.className = "connection-state"; status.textContent = "正在测试连接…"; try { const result = await api("/api/providers/test", { method: "POST", body: JSON.stringify(data) }); status.classList.add("success"); status.textContent = result.message; } catch (error) { status.classList.add("error"); status.textContent = error.message; } };
$("#personaForm").onsubmit = async e => { e.preventDefault(); const data = Object.fromEntries(new FormData(e.target)); const saved = await api("/api/personas", { method: "POST", body: JSON.stringify(data) }); state.personas.push(saved); state.persona ||= saved.id; e.target.reset(); renderSettings(); renderPickers(); };
$("#memoryForm").onsubmit = async e => { e.preventDefault(); const form = e.target; const data = Object.fromEntries(new FormData(form)); const editing = form.dataset.editing; const saved = await api(editing ? `/api/memories/${editing}` : "/api/memories", { method: editing ? "PUT" : "POST", body: JSON.stringify(data) }); if (editing) Object.assign(state.memories.find(item => item.id === editing), saved); else state.memories.unshift(saved); form.reset(); delete form.dataset.editing; $("#saveMemory").textContent = "添加记忆"; $("#cancelMemoryEdit").hidden = true; renderSettings(); };
$("#cancelMemoryEdit").onclick = () => { const form = $("#memoryForm"); form.reset(); delete form.dataset.editing; $("#saveMemory").textContent = "添加记忆"; $("#cancelMemoryEdit").hidden = true; };
let memorySearchTimer;
$("#memorySearch").oninput = event => { clearTimeout(memorySearchTimer); memorySearchTimer = setTimeout(async () => { state.memories = await api(`/api/memories?q=${encodeURIComponent(event.target.value.trim())}`); renderSettings(); }, 180); };
$("#memoryKindFilter").onchange = renderSettings;
$("#modelPicker").onclick = e => showPopover(e.currentTarget, $("#modelPopover"), state.providers.map(p => `<button data-value="${p.id}"><strong>${escapeHtml(p.name)}</strong><small>${escapeHtml(p.model)}</small></button>`).join(""), id => { state.provider = id; renderPickers(); });
$("#personaPicker").onclick = e => showPopover(e.currentTarget, $("#personaPopover"), `<button data-value="">默认人格</button>` + state.personas.map(p => `<button data-value="${p.id}">${escapeHtml(p.name)}</button>`).join(""), id => { state.persona = id || null; renderPickers(); });
$("#mobileMenu").onclick = () => $("#sidebar").classList.toggle("open");
$("#themeSelect").onchange = e => { document.documentElement.dataset.theme = e.target.value === "system" ? "" : e.target.value; localStorage.setItem("theme", e.target.value); };
const theme = localStorage.getItem("theme") || "system"; $("#themeSelect").value = theme; if (theme !== "system") document.documentElement.dataset.theme = theme;
bootstrap().catch(error => { console.error(error); openSettings("providers"); });
updateProviderCacheUI();
