const port = process.argv[2] || "9223";
const pages = await fetch(`http://127.0.0.1:${port}/json`).then(response => response.json());
const page = pages.find(item => item.type === "page" && item.url.includes("127.0.0.1:8876"));
if (!page) throw new Error("没有找到 Atherloom 调试页面");
const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let sequence = 0;
const pending = new Map();
socket.onmessage = event => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) {
    pending.get(message.id)(message);
    pending.delete(message.id);
  }
};
function command(method, params = {}) {
  const id = ++sequence;
  socket.send(JSON.stringify({ id, method, params }));
  return new Promise(resolve => pending.set(id, resolve));
}
async function evaluate(expression) {
  const result = await command("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.result?.exceptionDetails) throw new Error(result.result.exceptionDetails.exception?.description || result.result.exceptionDetails.text);
  return result.result?.result?.value;
}
const check = async (name, expression) => {
  const ok = await evaluate(expression);
  if (!ok) throw new Error(`Targeted UI failed: ${name}`);
  console.log(`PASS ${name}`);
};

await command("Runtime.enable");
await command("Network.clearBrowserCache");
await command("Storage.clearDataForOrigin", { origin: "http://127.0.0.1:8876", storageTypes: "service_workers,cache_storage" });
await command("Page.navigate", { url: `http://127.0.0.1:8876/?targeted=${Date.now()}` });
await new Promise(resolve => setTimeout(resolve, 1200));

await check("0509 assets loaded", `document.querySelector('script[src*="app.js?v=0509"]')&&document.querySelector('link[href*="app.css?v=0509"]')`);
await evaluate(`dismissLaunchScreen()`);
await check("speaker paragraphs are grouped", `renderRoleplayProse('旁白：一\\n旁白：二').match(/<b>旁白<\\/b>/g).length===1`);
await check("long speaker labels use aligned two-column layout", `(()=>{const host=document.createElement('div');host.innerHTML=renderRoleplayProse('三皇子微微一怔，随即垂眼笑了：台词');document.body.append(host);const row=host.querySelector('.roleplay-speaker'),label=row.querySelector('b'),ok=getComputedStyle(row).gridTemplateColumns.split(' ')[0]!=='72px'&&parseFloat(getComputedStyle(label).fontSize)>=12;host.remove();return ok})()`);
await check("pending player input remains visible", `(()=>{roleplayState.current={id:'pending-story',title:'排版测试',player_name:'叶枔枖',premise:'测试',status:'active',cast:[],state:{turn_number:0},turns:[{turn_number:0,player_input:'',prose:'旁白：开场',checkpoint:{}}]};roleplayState.busy=true;roleplayState.pendingInput='我离开此处走向其他地方';renderRoleplayStage();const ok=!document.querySelector('#roleplayTurnForm').hidden&&document.querySelector('.roleplay-player-card')?.textContent.includes('我离开此处')&&document.querySelector('#roleplayInput').disabled;roleplayState.busy=false;roleplayState.pendingInput='';return ok})()`);
await check("switcher exposes persona-scoped bulk clear", `(()=>{state.conversations=[{id:'a',title:'甲',persona_id:'p',archived:false},{id:'b',title:'乙',persona_id:'other',archived:false}];state.persona='p';const button=document.querySelector('#titleButton');openConversationSwitcher({stopPropagation(){},currentTarget:button});return document.querySelector('[data-value="__clear__"]')?.textContent.includes('（1）')&&!document.querySelector('#conversationPopover').textContent.includes('乙')})()`);
await check("pixel homestead opens without system emoji", `(async()=>{closePopovers();document.querySelector('#gameLibrary').hidden=false;gameState.catalog=await api('/api/games');await openGame('homestead');const text=document.querySelector('#homesteadStage').textContent;return !document.querySelector('#homesteadStage').hidden&&document.querySelectorAll('.pixel-flower').length>=4&&document.querySelectorAll('.pixel-pet').length===3&&!/[🐱🐶🐰🌻🌿🌹🪻]/u.test(text)})()`);
await new Promise(resolve => setTimeout(resolve, 450));
const screenshot = await command("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
writeFileSync("tests/homestead-0509.png", Buffer.from(screenshot.result.data, "base64"));

socket.close();
// Targeted browser regression coverage for the 0509 homestead preview.
import { writeFileSync } from "node:fs";
