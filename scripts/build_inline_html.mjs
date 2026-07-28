import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const read = file => fs.readFileSync(path.join(root, file), "utf8");
const safeScript = source => source.replaceAll("</script", "<\\/script");
let html = read("frontend/index.html");
const css = read("frontend/assets/app.css");
const standalone = safeScript(read("frontend/assets/standalone.js"));
const app = safeScript(read("frontend/assets/app.js"));

html = html
  .replace(/  <link rel="stylesheet" href="assets\/app\.css\?v=[^"]+">[\s\S]*?  <\/script>\r?\n/, `  <style data-atherloom-bundled="0491">\n${css}\n  </style>\n`)
  .replace(/  <script src="assets\/standalone\.js\?v=[^"]+"><\/script>/, `  <script data-atherloom-bundled="standalone">\n${standalone}\n  </script>`)
  .replace(/  <script src="assets\/app\.js\?v=[^"]+" defer><\/script>/, `  <script data-atherloom-bundled="app">\n${app}\n  </script>`)
  .replace(/  <script>\r?\n    if \('serviceWorker' in navigator\)[\s\S]*?  <\/script>\r?\n<\/body>/, "</body>");

fs.writeFileSync(path.join(root, "frontend/inline.html"), html, "utf8");
console.log(`Built inline.html (${Buffer.byteLength(html)} bytes)`);
