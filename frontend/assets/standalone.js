(() => {
  const standalone = new URLSearchParams(location.search).has("standalone") || location.hostname === "appassets.androidplatform.net" || location.hostname.endsWith("github.io");
  if (!standalone) return;
  const originalFetch = window.fetch.bind(window);
  const read = (key, fallback) => { try { return JSON.parse(localStorage.getItem(`atherloom:${key}`)) ?? fallback; } catch { return fallback; } };
  const write = (key, value) => { localStorage.setItem(`atherloom:${key}`, JSON.stringify(value)); return value; };
  const json = (value, status = 200) => Promise.resolve(new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
  const ndjson = value => Promise.resolve(new Response(value.map(item => JSON.stringify(item)).join("\n") + "\n", { headers: { "Content-Type": "application/x-ndjson; charset=utf-8" } }));
  const uid = () => crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  const native = window.AtherloomNative;
  const nativeResult = (method, payload) => {
    if (!native?.[method]) throw new Error("当前安装包缺少原生安全桥接，请更新 Atherloom");
    const result = JSON.parse(payload === undefined ? native[method]() : native[method](typeof payload === "string" ? payload : JSON.stringify(payload)));
    if (result && !Array.isArray(result) && result.ok === false) throw new Error(result.error || "原生操作失败");
    return result;
  };
  const publicProvider = item => { const copy={...item,has_api_key:!!item.api_key||!!item.has_api_key}; delete copy.api_key; return copy; };
  const providers = () => native ? nativeResult("listProviders") : read("providers", []).map(publicProvider);
  const webChat = async request => {
    const provider=read("providers",[]).find(item=>item.id===request.provider_id);
    if(!provider) throw new Error("API 线路不存在");
    const anthropic=provider.protocol==="anthropic", base=provider.base_url.replace(/\/+$/,"");
    const endpoint=anthropic?(base.endsWith("/v1")?`${base}/messages`:`${base}/v1/messages`):(base.endsWith("/chat/completions")?base:`${base}/chat/completions`);
    const headers={"Content-Type":"application/json",...(JSON.parse(provider.custom_headers||"{}"))};
    if(anthropic){headers["x-api-key"]=provider.api_key;headers["anthropic-version"]="2023-06-01";}else headers.Authorization=`Bearer ${provider.api_key}`;
    const payload={model:provider.model,max_tokens:4096,messages:request.messages};if(anthropic&&request.system)payload.system=request.system;
    const response=await originalFetch(endpoint,{method:"POST",headers,body:JSON.stringify(payload)});
    const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(`HTTP ${response.status} · ${data.error?.message||data.message||"网关请求失败"}`);
    const content=anthropic?(data.content||[]).filter(block=>block.type==="text").map(block=>block.text).join(""):data.choices?.[0]?.message?.content;
    return {ok:true,content:content||"",model:provider.model};
  };
  const messages = conversationId => read(`messages:${conversationId}`, []);
  const saveMessages = (conversationId, items) => write(`messages:${conversationId}`, items);
  const updateConversation = (id, changes) => {
    const all = read("conversations", []), item = all.find(row => row.id === id);
    if (!item) return null;
    Object.assign(item, changes, { updated_at: new Date().toISOString() }); write("conversations", all); return item;
  };
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
    if (url.pathname === "/api/bootstrap") return json({ providers:providers(),personas:read("personas",[]),conversations:read("conversations",[]),settings:settings() });
    if (url.pathname === "/api/settings" && method === "PUT") return json(write("settings", body));
    if (url.pathname === "/api/memories" && method === "GET") { const q=(url.searchParams.get("q")||"").toLowerCase(); return json(read("memories",[]).filter(item=>!item.trash&&(!q||`${item.title} ${item.content} ${item.kind}`.toLowerCase().includes(q)))); }
    if (url.pathname === "/api/memories" && method === "POST") { const item={...body,id:uid(),starred:false,archived:false,created_at:new Date().toISOString(),updated_at:new Date().toISOString()}; write("memories",[item,...read("memories",[])]); return json(item); }
    const memoryState=url.pathname.match(/^\/api\/memories\/([^/]+)\/state$/);
    if(memoryState&&method==="PATCH"){const all=read("memories",[]),item=all.find(row=>row.id===decodeURIComponent(memoryState[1]));if(item)Object.assign(item,body,{updated_at:new Date().toISOString()});write("memories",all);return json(item||{detail:"记忆不存在"},item?200:404);}
    const memoryItem=url.pathname.match(/^\/api\/memories\/([^/]+)$/);
    if(memoryItem&&method==="PUT"){const all=read("memories",[]),item=all.find(row=>row.id===decodeURIComponent(memoryItem[1]));if(item)Object.assign(item,body,{updated_at:new Date().toISOString()});write("memories",all);return json(item||{detail:"记忆不存在"},item?200:404);}
    if (url.pathname === "/api/providers" && method === "POST") {
      if (native) return json(nativeResult("saveProvider", body));
      const item={...body,id:uid(),has_api_key:!!body.api_key}; write("providers",[...read("providers",[]),item]); return json(publicProvider(item));
    }
    if (/^\/api\/providers\/[^/]+$/.test(url.pathname) && method === "DELETE") {
      const id=decodeURIComponent(url.pathname.split("/").pop());
      if(native) nativeResult("deleteProvider",id); else write("providers",read("providers",[]).filter(item=>item.id!==id));
      return json({ok:true});
    }
    if (url.pathname === "/api/providers/test" && method === "POST") return json({message:"格式检查通过。保存线路后发送一条消息即可验证网关与模型。"});
    if (url.pathname === "/api/personas" && method === "POST") { const item={...body,id:uid(),created_at:new Date().toISOString()}; write("personas",[...read("personas",[]),item]); return json(item); }
    if (url.pathname === "/api/conversations" && method === "POST") { const item={...body,id:uid(),title:"新对话",summary:"",pinned:0,starred:0,archived:0,created_at:new Date().toISOString(),updated_at:new Date().toISOString()}; write("conversations",[item,...read("conversations",[])]); return json(item); }
    const conversationMessages=url.pathname.match(/^\/api\/conversations\/([^/]+)\/messages$/);
    if(conversationMessages && method==="GET") return json(messages(decodeURIComponent(conversationMessages[1])));
    const conversationState=url.pathname.match(/^\/api\/conversations\/([^/]+)\/state$/);
    if(conversationState && method==="PATCH") return json(updateConversation(decodeURIComponent(conversationState[1]),body) || {detail:"对话不存在"}, updateConversation ? 200 : 404);
    const conversationItem=url.pathname.match(/^\/api\/conversations\/([^/]+)$/);
    if(conversationItem && method==="PATCH") return json(updateConversation(decodeURIComponent(conversationItem[1]),body) || {detail:"对话不存在"});
    if(url.pathname==="/api/search") { const q=(url.searchParams.get("q")||"").toLowerCase(); return json(read("conversations",[]).filter(item=>item.title.toLowerCase().includes(q)||messages(item.id).some(message=>message.content.toLowerCase().includes(q)))); }
    if(url.pathname==="/api/chat" && method==="POST") {
      const history=messages(body.conversation_id), now=new Date().toISOString();
      let user;
      if(body.reuse_user_message_id) user=history.find(item=>item.id===body.reuse_user_message_id);
      if(!user){user={id:uid(),role:"user",content:body.content,created_at:now};history.push(user);}
      const persona=read("personas",[]).find(item=>item.id===body.persona_id);
      const prompt=[persona?.prompt,`当前时间：${new Date().toLocaleString("zh-CN",{hour12:false})}`].filter(Boolean).join("\n\n");
      try {
        const request={provider_id:body.provider_id,system:prompt,messages:history.filter(item=>item.role==="user"||item.role==="assistant").map(item=>({role:item.role,content:item.content}))};
        const result=native?nativeResult("chat",request):await webChat(request);
        const assistant={id:uid(),role:"assistant",content:result.content||"",model:result.model||"",parent_message_id:user.id,created_at:new Date().toISOString()};
        history.push(assistant);saveMessages(body.conversation_id,history);
        const conversation=read("conversations",[]).find(item=>item.id===body.conversation_id);let title="";
        if(conversation?.title==="新对话"){title=(user.content||"新对话").replace(/\s+/g," ").slice(0,24);updateConversation(body.conversation_id,{title});}
        return ndjson([{delta:assistant.content},{done:true,assistant_id:assistant.id,user_id:user.id,title}]);
      } catch(error) { saveMessages(body.conversation_id,history); return ndjson([{error:error.message}]); }
    }
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
    return json({detail:"Standalone 功能仍在接入"},501);
  };
})();
