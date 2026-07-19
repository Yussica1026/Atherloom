const port = process.argv[2] || "9223";
const viewportWidth = Number(process.argv[3] || 390);
const viewportHeight = Number(process.argv[4] || 844);
const pages = await fetch(`http://127.0.0.1:${port}/json`).then(response => response.json());
const page = pages.find(item => item.type === "page" && item.url.includes("127.0.0.1:8876"));
if (!page) throw new Error("没有找到 Atherloom 调试页面");
const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let sequence = 0;
const pending = new Map();
socket.onmessage = event => { const message = JSON.parse(event.data); if (message.id && pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); } };
function command(method, params = {}) { const id = ++sequence; socket.send(JSON.stringify({ id, method, params })); return new Promise(resolve => pending.set(id, resolve)); }
async function evaluate(expression) { const result = await command("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }); if (result.result?.exceptionDetails) throw new Error(result.result.exceptionDetails.text); return result.result?.result?.value; }
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const check = async (name, expression) => { const ok = await evaluate(expression); if (!ok) throw new Error(`UI smoke failed: ${name}`); console.log(`PASS ${name}`); };

await command("Runtime.enable");
await command("Emulation.setDeviceMetricsOverride", { width: viewportWidth, height: viewportHeight, deviceScaleFactor: 3, mobile: true });
await check("top bar keeps only call action", `!!document.querySelector('#openCall') && !document.querySelector('#shareChat') && !document.querySelector('#openMemory') && !document.querySelector('#topSettings')`);
await check("call action uses a visible non-emoji icon", `getComputedStyle(document.querySelector('#openCall')).display !== 'none' && !!document.querySelector('#openCall svg')`);
await check("persona picker remains visible on compact phone", `getComputedStyle(document.querySelector('#personaPicker')).display !== 'none'`);
await check("welcome mark uses themeable SVG", `!!document.querySelector('.sun-mark svg') && getComputedStyle(document.querySelector('.sun-mark')).color === 'rgb(201, 100, 66)'`);
await check("versioned service worker updater is present", `document.documentElement.innerHTML.includes('service-worker.js?v=18') && document.documentElement.innerHTML.includes("updateViaCache: 'none'")`);
await check("welcome and composer helper copy stay minimal", `!document.querySelector('#welcome p') && !document.querySelector('#prompt').hasAttribute('placeholder') && !document.querySelector('.disclaimer')`);
await check("morning greeting follows local time", `renderTimeGreeting(new Date(2026,6,19,8,0)) === '早上好，今天想聊些什么？'`);
await check("afternoon greeting follows local time", `renderTimeGreeting(new Date(2026,6,19,16,0)) === '下午好，想聊些什么？'`);
await check("late-night greeting follows local time", `renderTimeGreeting(new Date(2026,6,19,23,30)) === '夜深了，想聊些什么？'`);
await check("call overlay starts hidden", `document.querySelector('#callSpace').hidden === true`);
await evaluate(`window.AtherloomNative={showNotice:message=>window.__androidCallNotice=message};document.querySelector('#openCall').click()`);
await check("Android call guard avoids the blocking overlay", `document.querySelector('#callSpace').hidden === true && window.__androidCallNotice.includes('已阻止')`);
await evaluate(`delete window.AtherloomNative`);
await evaluate(`document.querySelector('#titleButton').click()`); await wait(100);
await check("conversation switcher opens from title", `!document.querySelector('#conversationPopover').hidden && !!document.querySelector('#conversationPopover [data-value="__new__"]')`);
await evaluate(`document.body.click()`); await wait(100);
await check("conversation switcher closes outside", `document.querySelector('#conversationPopover').hidden`);
const hasProviders = await evaluate(`state.providers.length > 0`);
await evaluate(`document.querySelector('#modelPicker').click()`); await wait(100);
if (hasProviders) {
  await check("model popover opens", `document.querySelector('#modelPopover').hidden === false`);
  await evaluate(`document.body.click()`); await wait(100);
  await check("model popover closes outside", `document.querySelector('#modelPopover').hidden === true`);
} else {
  await check("empty model picker opens API settings", `document.querySelector('#settingsPanel').classList.contains('open') && document.querySelector('#tab-providers').classList.contains('active')`);
  await evaluate(`document.querySelector('#settingsPanel [data-close]').click()`); await wait(100);
}
await evaluate(`document.querySelector('#mobileMenu').click()`); await wait(100);
await check("mobile sidebar opens", `document.querySelector('#sidebar').classList.contains('open') && !document.querySelector('#sidebarBackdrop').hidden`);
await evaluate(`document.querySelector('#sidebarBackdrop').click()`); await wait(100);
await check("mobile sidebar closes", `!document.querySelector('#sidebar').classList.contains('open') && document.querySelector('#sidebarBackdrop').hidden`);
await evaluate(`document.querySelector('#openGames').click()`); await wait(500);
await check("playable game cards exist", `!!document.querySelector('[data-game-id="claw_machine"]') && !!document.querySelector('[data-game-id="cloud_slots"]')`);
await evaluate(`document.querySelector('[data-game-id="claw_machine"]').click()`); await wait(300);
await check("claw machine opens", `document.querySelector('#clawStage').hidden === false`);
await evaluate(`document.querySelector('[data-game-id="cloud_slots"]').click()`); await wait(300);
await check("slots open", `document.querySelector('#slotsStage').hidden === false`);
await check("AI game controls enabled", `!document.querySelector('#aiGameControls').hidden && !document.querySelector('#aiPlayOne').disabled && !document.querySelector('#aiPlayThree').disabled`);
await evaluate(`document.querySelector('#closeGames').click(); document.querySelector('#openReading').click()`); await wait(100);
await check("reading room opens", `!document.querySelector('#mediaSpace').hidden && !document.querySelector('#readingRoom').hidden`);
await evaluate(`document.querySelector('#closeMedia').click(); document.querySelector('#openCinema').click()`); await wait(100);
await check("cinema room opens", `!document.querySelector('#mediaSpace').hidden && !document.querySelector('#cinemaRoom').hidden`);
await evaluate(`document.querySelector('#closeMedia').click()`);
socket.close();
