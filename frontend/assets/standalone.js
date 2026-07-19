(() => {
  const standalone = new URLSearchParams(location.search).has("standalone") || location.hostname === "appassets.androidplatform.net" || location.hostname.endsWith("github.io");
  if (!standalone) return;
  const originalFetch = window.fetch.bind(window);
  const read = (key, fallback) => { try { return JSON.parse(localStorage.getItem(`atherloom:${key}`)) ?? fallback; } catch { return fallback; } };
  const write = (key, value) => { localStorage.setItem(`atherloom:${key}`, JSON.stringify(value)); return value; };
  const json = (value, status = 200) => Promise.resolve(new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
  const uid = () => crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  const waters = {
    willow_bay: { name: "柳湾", unlock: 0 }, mist_lake: { name: "雾湖", unlock: 220 }, cloud_coast: { name: "云海岸", unlock: 620 }
  };
  const fish = { willow_bay: [["银尾鲫",8],["青纹鲈",18],["月斑鳜",55]], mist_lake: [["雾鳞鱼",22],["琉璃鳟",46],["星灯鲤",120]], cloud_coast: [["风翼鲷",50],["潮鸣鲭",95],["极光鳐",260]] };
  const defaultGame = () => ({ coins:120,bait:8,water:"willow_bay",turn:0,catch:{},journal:[],unlocked:["willow_bay"] });
  const settings = () => read("settings", { auto_title_mode:"local",summary_enabled:true,summary_trigger_rounds:24,summary_prompt:"请忠实总结较早对话。",default_summary_prompt:"请忠实总结较早对话。",display_name:"",tool_permissions:{web_search:"allow",memory_read:"allow",memory_write:"ask",diary_write:"ask",delete:"ask"},font_scale:100,message_density:"comfortable",code_theme:"auto",memory_strategy:"hybrid" });
  window.fetch = async (input, options = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (!url.pathname.startsWith("/api/")) return originalFetch(input, options);
    const method = (options.method || "GET").toUpperCase();
    const body = options.body ? JSON.parse(options.body) : {};
    if (url.pathname === "/api/bootstrap") return json({ providers:read("providers",[]),personas:read("personas",[]),conversations:read("conversations",[]),settings:settings() });
    if (url.pathname === "/api/settings" && method === "PUT") return json(write("settings", body));
    if (url.pathname === "/api/memories" && method === "GET") return json(read("memories",[]));
    if (url.pathname === "/api/memories" && method === "POST") { const item={...body,id:uid(),starred:false,archived:false,created_at:new Date().toISOString(),updated_at:new Date().toISOString()}; write("memories",[item,...read("memories",[])]); return json(item); }
    if (url.pathname === "/api/providers" && method === "POST") { const item={...body,id:uid(),has_api_key:!!body.api_key}; delete item.api_key; write("providers",[...read("providers",[]),item]); return json(item); }
    if (url.pathname === "/api/personas" && method === "POST") { const item={...body,id:uid(),created_at:new Date().toISOString()}; write("personas",[...read("personas",[]),item]); return json(item); }
    if (url.pathname === "/api/conversations" && method === "POST") { const item={...body,id:uid(),title:"新对话",summary:"",pinned:0,starred:0,archived:0,created_at:new Date().toISOString(),updated_at:new Date().toISOString()}; write("conversations",[item,...read("conversations",[])]); return json(item); }
    if (url.pathname === "/api/games") return json([{id:"quiet_fishing",name:"云汀钓记",icon:"◌",status:"playable",description:"离线也能保存进度的原创钓鱼游戏。"},{id:"claw_machine",name:"抓娃娃机",icon:"◇",status:"coming",description:"可视化抓娃娃玩法正在接入。"},{id:"text_arcade",name:"文字街机",icon:"✦",status:"adapter",description:"文字游戏扩展席位。"}]);
    if (/\/api\/games\/quiet_fishing\/state/.test(url.pathname)) return json({game_id:"quiet_fishing",state:read("game:quiet_fishing",defaultGame()),waters});
    if (/\/api\/games\/quiet_fishing\/action/.test(url.pathname) && method === "POST") {
      const state=read("game:quiet_fishing",defaultGame()), events=[];
      if(body.action==="cast"){const count=Math.min(body.amount,state.bait);if(!count)return json({detail:"鱼饵用完了"},409);for(let i=0;i<count;i++){state.bait--;state.turn++;const pool=fish[state.water],pick=pool[state.turn%pool.length];state.catch[pick[0]]=(state.catch[pick[0]]||0)+1;events.push(`钓到了${pick[0]}，价值 ${pick[1]} 枚云贝`);}}
      else if(body.action==="buy_bait"){const cost=body.amount*5;if(state.coins<cost)return json({detail:"云贝不够"},409);state.coins-=cost;state.bait+=body.amount;events.push(`买了 ${body.amount} 份鱼饵`);}
      else if(body.action==="sell_all"){let income=0;for(const [name,count] of Object.entries(state.catch))for(const pool of Object.values(fish))for(const item of pool)if(item[0]===name)income+=item[1]*count;state.coins+=income;state.catch={};events.push(`渔获卖出，得到 ${income} 枚云贝`);}
      else if(body.action==="travel"){const place=waters[body.target];if(!place)return json({detail:"未知水域"},422);if(!state.unlocked.includes(body.target)){if(state.coins<place.unlock)return json({detail:"云贝不够"},409);state.coins-=place.unlock;state.unlocked.push(body.target);}state.water=body.target;events.push(`来到了${place.name}`);}
      state.journal=[...state.journal,...events].slice(-30);write("game:quiet_fishing",state);return json({state,events});
    }
    if (url.pathname.includes("/messages")) return json([]);
    return json({detail:"Standalone 功能仍在接入"},501);
  };
})();
