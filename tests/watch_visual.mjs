import { writeFileSync } from "node:fs";

const pages = await fetch("http://127.0.0.1:9223/json").then(response => response.json());
const target = pages.find(item => item.type === "page" && item.url.includes("127.0.0.1:8876"));
if (!target) throw new Error("Atherloom debug page is not available");
const socket = new WebSocket(target.webSocketDebuggerUrl);
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
const command = (method, params = {}) => new Promise(resolve => {
  const id = ++sequence;
  pending.set(id, resolve);
  socket.send(JSON.stringify({ id, method, params }));
});
await command("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
await command("Network.clearBrowserCache");
await command("Storage.clearDataForOrigin", { origin: "http://127.0.0.1:8876", storageTypes: "service_workers,cache_storage" });
await command("Page.navigate", { url: `http://127.0.0.1:8876/?watch-visual=${Date.now()}` });
await new Promise(resolve => setTimeout(resolve, 1000));
const inspected = await command("Runtime.evaluate", {
  expression: `(()=>{dismissLaunchScreen();document.querySelector('#openCinema').click();const stage=document.querySelector('.watch-stage').getBoundingClientRect(),player=document.querySelector('#moviePlayer').getBoundingClientRect(),form=document.querySelector('#watchQuestionForm').getBoundingClientRect();return {stageWidth:stage.width,playerWidth:player.width,playerHeight:player.height,formBottom:form.bottom,overflow:document.documentElement.scrollWidth>innerWidth}})()`,
  returnByValue: true,
});
const layout = inspected.result.result.value;
if (layout.overflow || layout.playerWidth < 340 || layout.formBottom > 844) throw new Error(`Invalid watch layout: ${JSON.stringify(layout)}`);
const screenshot = await command("Page.captureScreenshot", { format: "png", fromSurface: true });
writeFileSync("tests/watch-room-mobile.png", Buffer.from(screenshot.result.data, "base64"));
socket.close();
console.log(JSON.stringify(layout));
