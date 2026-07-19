const port = process.argv[2] || "9223";
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
await check("call overlay starts hidden", `document.querySelector('#callSpace').hidden === true`);
await evaluate(`document.querySelector('#modelPicker').click()`); await wait(100);
await check("model popover opens", `document.querySelector('#modelPopover').hidden === false`);
await evaluate(`document.body.click()`); await wait(100);
await check("model popover closes outside", `document.querySelector('#modelPopover').hidden === true`);
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
await evaluate(`document.querySelector('#closeGames').click(); document.querySelector('#openReading').click()`); await wait(100);
await check("reading room opens", `!document.querySelector('#mediaSpace').hidden && !document.querySelector('#readingRoom').hidden`);
await evaluate(`document.querySelector('#closeMedia').click(); document.querySelector('#openCinema').click()`); await wait(100);
await check("cinema room opens", `!document.querySelector('#mediaSpace').hidden && !document.querySelector('#cinemaRoom').hidden`);
await evaluate(`document.querySelector('#closeMedia').click()`);
socket.close();
