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
  const webModels = async provider => {
    const base=provider.base_url.replace(/\/+$/,""),anthropic=provider.protocol==="anthropic",endpoint=`${base}/models`;
    const headers={...(JSON.parse(provider.custom_headers||"{}"))};if(anthropic){headers["x-api-key"]=provider.api_key||"";headers["anthropic-version"]="2023-06-01";}else headers.Authorization=`Bearer ${provider.api_key||""}`;
    const response=await originalFetch(endpoint,{headers}),payload=await response.json().catch(()=>({}));if(!response.ok)throw new Error(`HTTP ${response.status} · ${payload.error?.message||payload.message||"网关拒绝请求"}`);
    return [...new Set((payload.data||[]).map(item=>typeof item==="string"?item:item?.id).filter(Boolean))].sort();
  };
  const providers = () => native ? nativeResult("listProviders") : read("providers", []).map(publicProvider);
  const webChat = async request => {
    const provider=read("providers",[]).find(item=>item.id===request.provider_id);
    if(!provider) throw new Error("API 线路不存在");
    const anthropic=provider.protocol==="anthropic", base=provider.base_url.replace(/\/+$/,"");
    const endpoint=anthropic?(base.endsWith("/v1")?`${base}/messages`:`${base}/v1/messages`):(base.endsWith("/chat/completions")?base:`${base}/chat/completions`);
    const headers={"Content-Type":"application/json",...(JSON.parse(provider.custom_headers||"{}"))};
    if(anthropic){headers["x-api-key"]=provider.api_key;headers["anthropic-version"]="2023-06-01";}else headers.Authorization=`Bearer ${provider.api_key}`;
    const payload={model:provider.model,max_tokens:4096,messages:formatMessages(request.messages,provider.protocol)};if(anthropic&&request.system)payload.system=request.system;
    const response=await originalFetch(endpoint,{method:"POST",headers,body:JSON.stringify(payload)});
    const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(`HTTP ${response.status} · ${data.error?.message||data.message||"网关请求失败"}`);
    const content=anthropic?(data.content||[]).filter(block=>block.type==="text").map(block=>block.text).join(""):data.choices?.[0]?.message?.content;
    return {ok:true,content:content||"",model:provider.model};
  };
  const formatMessages=(items,protocol)=>items.map(item=>{if(item.role!=="user"||!item.attachments?.length)return {role:item.role,content:item.content};const anthropic=protocol==="anthropic";if(anthropic){const blocks=[{type:"text",text:item.content}];for(const file of item.attachments){if(file.kind==="image")blocks.push({type:"image",source:{type:"base64",media_type:file.mime,data:file.data.split(",")[1]}});else if(file.kind==="pdf")blocks.push({type:"document",source:{type:"base64",media_type:"application/pdf",data:file.data.split(",")[1]}});else if(file.text)blocks.push({type:"text",text:`文件：${file.name}\n${file.text}`});}return {role:item.role,content:blocks};}const parts=[{type:"text",text:item.content}];for(const file of item.attachments){if(file.kind==="image")parts.push({type:"image_url",image_url:{url:file.data}});else if(file.text)parts.push({type:"text",text:`文件：${file.name}\n${file.text}`});else parts.push({type:"text",text:`[已选择文件 ${file.name}，当前兼容线路不支持直接传输此格式]`});}return {role:item.role,content:parts};});
  const messages = conversationId => read(`messages:${conversationId}`, []);
  const saveMessages = (conversationId, items) => write(`messages:${conversationId}`, items);
  const effectiveMessages = (conversationId,all=messages(conversationId)) => {const chosen=read(`versions:${conversationId}`,{}),seen=new Set();return all.filter(item=>{if(item.role!=="assistant"||!item.parent_message_id)return true;if(seen.has(item.parent_message_id))return false;seen.add(item.parent_message_id);const versions=all.filter(row=>row.role==="assistant"&&row.parent_message_id===item.parent_message_id),selected=versions.find(row=>row.id===chosen[item.parent_message_id])||versions.at(-1);return item===selected;});};
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
  const defaultClaw=()=>({coins:100,turn:0,position:2,prizes:["云朵兔","星星熊","橘子猫","月亮狗","小海豹"],inventory:{},journal:[]});
  const defaultSlots=()=>({coins:100,turn:0,reels:["✦","◌","◇"],journal:[]});
  const aiGameActions={quiet_fishing:[{action:"cast",amount:1},{action:"buy_bait",amount:5},{action:"sell_all",amount:1},{action:"travel",target:"willow_bay",amount:1},{action:"travel",target:"mist_lake",amount:1},{action:"travel",target:"cloud_coast",amount:1}],claw_machine:[{action:"move_left",amount:1},{action:"move_right",amount:1},{action:"grab",amount:1}],cloud_slots:[{action:"spin",amount:1}]};
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
    if(url.pathname==="/api/favorites"&&method==="GET"){const q=(url.searchParams.get("q")||"").toLowerCase();return json(read("favorites",[]).filter(item=>!q||`${item.text_snapshot} ${item.conversation_title_snapshot}`.toLowerCase().includes(q)));}
    const favoriteMessage=url.pathname.match(/^\/api\/favorites\/([^/]+)$/);
    if(favoriteMessage&&method==="POST"){
      const messageId=decodeURIComponent(favoriteMessage[1]),conversations=read("conversations",[]);let source,conversation;
      for(const item of conversations){source=messages(item.id).find(message=>message.id===messageId);if(source){conversation=item;break;}}
      if(!source)return json({detail:"该消息不可珍藏"},404);const all=read("favorites",[]);let favorite=all.find(item=>item.source_message_id===messageId);
      if(!favorite){favorite={id:uid(),source_message_id:messageId,conversation_id:conversation.id,role:source.role,text_snapshot:source.content,conversation_title_snapshot:conversation.title,original_message_created_at:source.created_at,favorited_at:new Date().toISOString(),owners:[]};all.unshift(favorite);}
      if(!favorite.owners.includes(body.owner||"user"))favorite.owners.push(body.owner||"user");write("favorites",all);return json(favorite);
    }
    if(favoriteMessage&&method==="DELETE"){const owner=url.searchParams.get("owner")||"user",all=read("favorites",[]),favorite=all.find(item=>item.source_message_id===decodeURIComponent(favoriteMessage[1]));if(favorite)favorite.owners=favorite.owners.filter(item=>item!==owner);write("favorites",all.filter(item=>item.owners.length));return json({ok:true});}
    const memoryState=url.pathname.match(/^\/api\/memories\/([^/]+)\/state$/);
    if(memoryState&&method==="PATCH"){const all=read("memories",[]),item=all.find(row=>row.id===decodeURIComponent(memoryState[1]));if(item)Object.assign(item,body,{updated_at:new Date().toISOString()});write("memories",all);return json(item||{detail:"记忆不存在"},item?200:404);}
    const memoryItem=url.pathname.match(/^\/api\/memories\/([^/]+)$/);
    if(memoryItem&&method==="PUT"){const all=read("memories",[]),item=all.find(row=>row.id===decodeURIComponent(memoryItem[1]));if(item)Object.assign(item,body,{updated_at:new Date().toISOString()});write("memories",all);return json(item||{detail:"记忆不存在"},item?200:404);}
    if (url.pathname === "/api/providers" && method === "POST") {
      if (native) return json(nativeResult("saveProvider", body));
      const item={...body,id:uid(),has_api_key:!!body.api_key}; write("providers",[...read("providers",[]),item]); return json(publicProvider(item));
    }
    if (url.pathname === "/api/providers/models" && method === "POST") return json({models:native?nativeResult("listModels",body):await webModels(body)});
    if(url.pathname==="/api/messages/selection"&&method==="PATCH"){const selected=read(`versions:${body.conversation_id}`,{});selected[body.parent_message_id]=body.assistant_message_id;write(`versions:${body.conversation_id}`,selected);return json({ok:true});}
    const deleteMessage=url.pathname.match(/^\/api\/messages\/([^/]+)$/);
    if(deleteMessage&&method==="DELETE"){const id=decodeURIComponent(deleteMessage[1]);for(const conversation of read("conversations",[])){const all=messages(conversation.id),target=all.find(item=>item.id===id);if(!target)continue;saveMessages(conversation.id,all.filter(item=>item.id!==id&&(target.role!=="user"||item.parent_message_id!==id)));const selected=read(`versions:${conversation.id}`,{});if(target.parent_message_id)delete selected[target.parent_message_id];if(target.role==="user")delete selected[target.id];write(`versions:${conversation.id}`,selected);return json({ok:true});}return json({detail:"消息不存在"},404);}
    if (/^\/api\/providers\/[^/]+$/.test(url.pathname) && method === "DELETE") {
      const id=decodeURIComponent(url.pathname.split("/").pop());
      if(native) nativeResult("deleteProvider",id); else write("providers",read("providers",[]).filter(item=>item.id!==id));
      return json({ok:true});
    }
    if (url.pathname === "/api/providers/test" && method === "POST") return json({message:"格式检查通过。保存线路后发送一条消息即可验证网关与模型。"});
    if (url.pathname === "/api/personas" && method === "POST") { const item={...body,id:uid(),created_at:new Date().toISOString()}; write("personas",[...read("personas",[]),item]); return json(item); }
    if (url.pathname === "/api/conversations" && method === "POST") { const item={...body,id:uid(),title:"新对话",summary:"",pinned:0,starred:0,archived:0,created_at:new Date().toISOString(),updated_at:new Date().toISOString()}; write("conversations",[item,...read("conversations",[])]); return json(item); }
    const conversationMessages=url.pathname.match(/^\/api\/conversations\/([^/]+)\/messages$/);
    if(conversationMessages && method==="GET"){const conversationId=decodeURIComponent(conversationMessages[1]),selected=read(`versions:${conversationId}`,{});return json(messages(conversationId).map(item=>({...item,selected:item.role==="assistant"&&selected[item.parent_message_id]===item.id})));}
    const conversationState=url.pathname.match(/^\/api\/conversations\/([^/]+)\/state$/);
    if(conversationState && method==="PATCH") return json(updateConversation(decodeURIComponent(conversationState[1]),body) || {detail:"对话不存在"}, updateConversation ? 200 : 404);
    const conversationItem=url.pathname.match(/^\/api\/conversations\/([^/]+)$/);
    if(conversationItem && method==="PATCH") return json(updateConversation(decodeURIComponent(conversationItem[1]),body) || {detail:"对话不存在"});
    if(url.pathname==="/api/search") { const q=(url.searchParams.get("q")||"").toLowerCase(); return json(read("conversations",[]).filter(item=>item.title.toLowerCase().includes(q)||messages(item.id).some(message=>message.content.toLowerCase().includes(q)))); }
    if(url.pathname==="/api/chat" && method==="POST") {
      const history=messages(body.conversation_id), now=new Date().toISOString();
      let user;
      if(body.reuse_user_message_id) user=history.find(item=>item.id===body.reuse_user_message_id);
      if(!user){user={id:uid(),role:"user",content:body.content,attachments:body.attachments||[],created_at:now};history.push(user);}
      const persona=read("personas",[]).find(item=>item.id===body.persona_id);
      const prompt=[persona?.prompt,`当前时间：${new Date().toLocaleString("zh-CN",{hour12:false})}`].filter(Boolean).join("\n\n");
      try {
        const provider=providers().find(item=>item.id===body.provider_id);const rawMessages=effectiveMessages(body.conversation_id,history).filter(item=>item.role==="user"||item.role==="assistant").map(item=>({role:item.role,content:item.content,attachments:item.attachments||[]}));
        const request={provider_id:body.provider_id,system:prompt,messages:native?formatMessages(rawMessages,provider?.protocol||"openai"):rawMessages};
        const result=native?nativeResult("chat",request):await webChat(request);
        const assistant={id:uid(),role:"assistant",content:result.content||"",model:result.model||"",parent_message_id:user.id,created_at:new Date().toISOString()};
        history.push(assistant);saveMessages(body.conversation_id,history);
        const selected=read(`versions:${body.conversation_id}`,{});selected[user.id]=assistant.id;write(`versions:${body.conversation_id}`,selected);
        const conversation=read("conversations",[]).find(item=>item.id===body.conversation_id);let title="";
        if(conversation?.title==="新对话"){title=(user.content||"新对话").replace(/\s+/g," ").slice(0,24);updateConversation(body.conversation_id,{title});}
        return ndjson([{reasoning_delta:result.reasoning||"",delta:assistant.content},{done:true,assistant_id:assistant.id,user_id:user.id,title}]);
      } catch(error) { saveMessages(body.conversation_id,history); return ndjson([{error:error.message}]); }
    }
    if (url.pathname === "/api/games") return json([{id:"quiet_fishing",name:"云汀钓记",icon:"◌",status:"playable",description:"离线也能保存进度的原创钓鱼游戏。"},{id:"claw_machine",name:"抓娃娃机",icon:"◇",status:"playable",description:"移动爪子、选择目标并收集娃娃。"},{id:"cloud_slots",name:"云纹老虎机",icon:"✦",status:"playable",description:"只使用本地云贝的确定性三轴小游戏。"}]);
    const aiTurn=url.pathname.match(/^\/api\/games\/([^/]+)\/ai-turn$/);
    if(aiTurn&&method==="POST"){
      const gameId=aiTurn[1],allowed=aiGameActions[gameId];if(!allowed)return json({detail:"游戏尚未开放 AI 游玩"},404);const decisions=[];let spent=0,finalState;
      for(let turn=0;turn<(body.turns||1);turn++){const key=`game:${gameId}`,fallback=gameId==="quiet_fishing"?defaultGame():gameId==="claw_machine"?defaultClaw():defaultSlots(),current=read(key,fallback),persona=read("personas",[]).find(item=>item.id===body.persona_id),instruction=`${persona?.prompt?persona.prompt+"\n\n":""}你正在 Atherloom 中玩游戏 ${gameId}。\n当前状态：${JSON.stringify(current)}\n允许动作：${JSON.stringify(allowed)}\n剩余可花云贝预算：${(body.max_spend||30)-spent}。\n只返回 JSON：{"action":"白名单动作","amount":1,"target":"需要时填写","comment":"一句当轮想法"}。`;
        try{const provider=providers().find(item=>item.id===body.provider_id),request={provider_id:body.provider_id,messages:[{role:"user",content:instruction}],system:""},answer=native?nativeResult("chat",request):await webChat(request),match=(answer.content||"").match(/\{[\s\S]*\}/);if(!match)throw new Error("模型没有返回游戏动作");const choice=JSON.parse(match[0]),valid=allowed.find(item=>item.action===choice.action&&(!item.target||item.target===choice.target));if(!valid)throw new Error("模型选择了白名单外动作");const cost=gameId==="claw_machine"&&valid.action==="grab"?10:gameId==="cloud_slots"?5:gameId==="quiet_fishing"&&valid.action==="buy_bait"?25:0;if(spent+cost>(body.max_spend||30))break;const response=await window.fetch(`/api/games/${gameId}/action`,{method:"POST",body:JSON.stringify(valid)});if(!response.ok)throw new Error((await response.json()).detail);const played=await response.json();spent+=cost;if(choice.comment){played.state.journal=[...played.state.journal,`AI：${String(choice.comment).slice(0,160)}`].slice(-30);write(key,played.state);}finalState=played.state;decisions.push({choice:valid,comment:choice.comment||"",events:played.events});}catch(error){return json({detail:error.message},502);}
      }return json({state:finalState,decisions,spent});
    }
    if(/\/api\/games\/claw_machine\/state/.test(url.pathname))return json({game_id:"claw_machine",state:read("game:claw_machine",defaultClaw()),waters:{}});
    if(/\/api\/games\/claw_machine\/action/.test(url.pathname)&&method==="POST"){const state=read("game:claw_machine",defaultClaw()),events=[];if(body.action==="move_left"){state.position=Math.max(0,state.position-1);events.push("爪子向左移动");}else if(body.action==="move_right"){state.position=Math.min(state.prizes.length-1,state.position+1);events.push("爪子向右移动");}else if(body.action==="grab"){if(state.coins<10)return json({detail:"云贝不够"},409);state.coins-=10;state.turn++;const prize=state.prizes[state.position],success=(state.turn*17+state.position*23)%100<58;if(success){state.inventory[prize]=(state.inventory[prize]||0)+1;events.push(`抓到了${prize}！`);}else events.push(`${prize}晃了一下，又掉回去了`);}state.journal=[...state.journal,...events].slice(-30);write("game:claw_machine",state);return json({state,events});}
    if(/\/api\/games\/cloud_slots\/state/.test(url.pathname))return json({game_id:"cloud_slots",state:read("game:cloud_slots",defaultSlots()),waters:{}});
    if(/\/api\/games\/cloud_slots\/action/.test(url.pathname)&&method==="POST"){const state=read("game:cloud_slots",defaultSlots()),events=[],symbols=["✦","◌","◇","☾","❀"],count=body.amount||1,cost=count*5;if(state.coins<cost)return json({detail:"云贝不够"},409);state.coins-=cost;for(let n=0;n<count;n++){state.turn++;state.reels=[symbols[(state.turn*3)%5],symbols[(state.turn*7+1)%5],symbols[(state.turn*11+2)%5]];const unique=new Set(state.reels).size,payout=unique===1?40:unique===2?10:0;state.coins+=payout;events.push(`${state.reels.join(" · ")}，${payout?`赢得 ${payout} 云贝`:"没有连线"}`);}state.journal=[...state.journal,...events].slice(-30);write("game:cloud_slots",state);return json({state,events});}
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
