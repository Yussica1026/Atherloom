"""Portable garden and pet simulation core for Atherloom.

The module has no database, model-provider, or user-data dependency so it can
be published and embedded independently.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


FLOWERS = {
    "sunbell": {"name": "铃阳花", "grow_hours": 48, "seed_cost": 12},
    "moonmint": {"name": "月薄荷", "grow_hours": 36, "seed_cost": 10},
    "cloudrose": {"name": "云朵玫瑰", "grow_hours": 72, "seed_cost": 18},
    "starbell": {"name": "星铃兰", "grow_hours": 60, "seed_cost": 16},
}
PET_KINDS = {
    "cloud_cat": {"name": "云朵猫"},
    "shiba": {"name": "栗子犬"},
    "lop": {"name": "垂耳兔"},
}
SCHOOL_SUBJECTS = {
    "letters": "识字",
    "music": "音乐",
    "painting": "绘画",
    "manners": "礼貌",
    "nature": "自然观察",
}
PET_COOLDOWNS = {"play": 30 * 60, "school": 2 * 60 * 60}


def _cooldown_ready(pet: dict[str, Any], action: str, moment: datetime) -> bool:
    until = pet.setdefault("cooldowns", {}).get(action)
    if not until:
        return True
    try:
        ready_at = datetime.fromisoformat(until)
        if ready_at.tzinfo is None:
            ready_at = ready_at.replace(tzinfo=timezone.utc)
        return moment >= ready_at
    except (TypeError, ValueError):
        return True


def _start_cooldown(pet: dict[str, Any], action: str, moment: datetime) -> None:
    from datetime import timedelta
    pet.setdefault("cooldowns", {})[action] = (moment + timedelta(seconds=PET_COOLDOWNS[action])).isoformat()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_state(now: str | None = None) -> dict[str, Any]:
    stamp = now or iso_now()
    return {
        "version": 1,
        "coins": 120,
        "last_settled_at": stamp,
        "garden": [None, None, None, None],
        "pet": None,
        "inventory": {"pet_food": 4, "soap": 2, "fertilizer": 2},
        "events": [{"kind": "welcome", "text": "一座安静的小庭院准备好了。", "created_at": stamp}],
        "management": {"enabled": False, "max_actions_per_day": 4, "daily_budget": 30, "spent_today": 0, "day": stamp[:10]},
    }


def _event(state: dict[str, Any], kind: str, text: str, stamp: str) -> None:
    key = f"{kind}:{text}"
    recent = state.setdefault("events", [])[-20:]
    if any(item.get("key") == key for item in recent):
        return
    state["events"] = (state["events"] + [{"kind": kind, "text": text, "key": key, "created_at": stamp}])[-60:]


def settle(source: dict[str, Any], now: datetime | None = None) -> tuple[dict[str, Any], list[str]]:
    state = deepcopy(source)
    moment = now or datetime.now(timezone.utc)
    previous = datetime.fromisoformat(state.get("last_settled_at") or moment.isoformat())
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    hours = max(0.0, min(24 * 30, (moment - previous).total_seconds() / 3600))
    if hours < 1 / 60:
        return state, []
    stamp, emitted = moment.isoformat(), []
    for plant in state.get("garden", []):
        if not plant or plant.get("status") == "dead":
            continue
        old_moisture, old_vitality = float(plant["moisture"]), float(plant["vitality"])
        plant["moisture"] = max(0.0, old_moisture - 2.4 * hours)
        if plant["moisture"] >= 20:
            plant["growth"] = min(100.0, float(plant["growth"]) + 100 * hours / FLOWERS[plant["species"]]["grow_hours"])
            plant["vitality"] = min(100.0, old_vitality + 0.15 * hours)
        else:
            plant["vitality"] = max(0.0, old_vitality - (1.2 if plant["moisture"] else 2.2) * hours)
        plant["status"] = "dead" if plant["vitality"] <= 0 else "wilted" if plant["vitality"] < 35 else "blooming" if plant["growth"] >= 100 else "growing"
        if old_moisture >= 20 > plant["moisture"]:
            text = f"{plant['name']}开始口渴，叶尖轻轻垂了下来。"; _event(state, "plant_thirsty", text, stamp); emitted.append(text)
        if old_vitality > 0 and plant["status"] == "dead":
            text = f"{plant['name']}因为太久没有得到照料，已经枯死了。"; _event(state, "plant_dead", text, stamp); emitted.append(text)
        elif plant["status"] == "blooming" and not plant.get("bloom_announced"):
            plant["bloom_announced"] = True
            text = f"{plant['name']}第一次开花了。"; _event(state, "first_bloom", text, stamp); emitted.append(text)
    pet = state.get("pet")
    if pet:
        pet.setdefault("cooldowns", {})
        pet.setdefault("thought", f"今天也想和你待在一起。")
        pet["hunger"] = max(0.0, float(pet["hunger"]) - 1.8 * hours)
        pet["happiness"] = max(0.0, float(pet["happiness"]) - 0.65 * hours)
        pet["hygiene"] = max(0.0, float(pet["hygiene"]) - 0.45 * hours)
        pet["energy"] = min(100.0, float(pet["energy"]) + 1.1 * hours)
        neglected = pet["hunger"] < 25 or pet["happiness"] < 25
        pet["neglect_hours"] = float(pet.get("neglect_hours", 0)) + hours if neglected else max(0.0, float(pet.get("neglect_hours", 0)) - hours * 2)
        old_mood = pet.get("mood", "happy")
        pet["mood"] = "depressed" if pet["neglect_hours"] >= 48 else "lonely" if pet["neglect_hours"] >= 12 else "hungry" if pet["hunger"] < 30 else "happy"
        if old_mood != pet["mood"]:
            labels = {"hungry": f"{pet['name']}饿了，正在饭碗边等你。", "lonely": f"{pet['name']}已经孤单了一阵，很想有人陪。", "depressed": f"{pet['name']}因长期缺少照料变得抑郁，需要持续陪伴才能慢慢恢复。", "happy": f"{pet['name']}重新有精神了。"}
            thoughts = {
                "hungry": "饭碗是不是在和我捉迷藏？",
                "lonely": "门外有脚步声吗……会是你吗？",
                "depressed": "我先安静趴一会儿，也许你会回来。",
                "happy": "你回来以后，小屋好像亮了一点。",
            }
            pet["thought"] = thoughts[pet["mood"]]
            text = labels[pet["mood"]]; _event(state, f"pet_{pet['mood']}", text, stamp); emitted.append(text)
    management = state.setdefault("management", {})
    if management.get("day") != moment.date().isoformat():
        management["day"], management["spent_today"] = moment.date().isoformat(), 0
    state["last_settled_at"] = stamp
    return state, emitted


def allowed_actions(state: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    moment = now or datetime.now(timezone.utc)
    actions: list[dict[str, Any]] = []
    for index, plant in enumerate(state.get("garden", [])):
        if plant is None:
            actions.extend({"action": "plant", "target": index, "species": species, "cost": data["seed_cost"]} for species, data in FLOWERS.items())
        elif plant.get("status") != "dead":
            if plant["moisture"] < 85: actions.append({"action": "water", "target": index, "cost": 0})
            if state["inventory"].get("fertilizer", 0): actions.append({"action": "fertilize", "target": index, "cost": 0})
            if plant["growth"] >= 100: actions.append({"action": "harvest", "target": index, "cost": 0})
        else:
            actions.append({"action": "clear_plant", "target": index, "cost": 0})
    pet = state.get("pet")
    if not pet:
        actions.extend({"action": "adopt", "kind": kind, "cost": 20} for kind in PET_KINDS)
    else:
        pet.setdefault("cooldowns", {})
        if state["inventory"].get("pet_food", 0): actions.append({"action": "feed", "cost": 0})
        if pet["energy"] < 95: actions.append({"action": "rest", "cost": 0})
        if pet["energy"] >= 10 and _cooldown_ready(pet, "play", moment): actions.append({"action": "play", "cost": 0})
        if state["inventory"].get("soap", 0): actions.append({"action": "bathe", "cost": 0})
        if pet["energy"] >= 20 and _cooldown_ready(pet, "school", moment):
            actions.extend({"action": "school", "subject": subject, "cost": 8} for subject in SCHOOL_SUBJECTS)
    return actions


def act(source: dict[str, Any], payload: dict[str, Any], now: datetime | None = None) -> tuple[dict[str, Any], list[str]]:
    moment = now or datetime.now(timezone.utc)
    state, _ = settle(source, moment)
    action, stamp, events = str(payload.get("action", "")), moment.isoformat(), []
    target = int(payload.get("target", 0) or 0)
    if action == "plant":
        species = str(payload.get("species", ""))
        if species not in FLOWERS or target not in range(len(state["garden"])) or state["garden"][target] is not None: raise ValueError("这个花盆现在不能播种")
        data = FLOWERS[species]
        if state["coins"] < data["seed_cost"]: raise ValueError("云贝不够购买种子")
        state["coins"] -= data["seed_cost"]; state["garden"][target] = {"id": f"plant-{int(moment.timestamp()*1000)}-{target}", "species": species, "name": data["name"], "moisture": 72.0, "vitality": 100.0, "growth": 0.0, "status": "growing", "planted_at": stamp}
        events.append(f"种下了{data['name']}。")
    elif action == "water":
        plant = state["garden"][target]; 
        if not plant or plant["status"] == "dead": raise ValueError("这里没有可以浇水的花")
        plant["moisture"] = min(100.0, plant["moisture"] + 55); plant["vitality"] = min(100.0, plant["vitality"] + 5); events.append(f"给{plant['name']}浇了水。")
    elif action == "fertilize":
        plant = state["garden"][target]
        if not plant or plant["status"] == "dead" or state["inventory"].get("fertilizer", 0) < 1: raise ValueError("现在不能施肥")
        state["inventory"]["fertilizer"] -= 1; plant["growth"] = min(100.0, plant["growth"] + 18); events.append(f"给{plant['name']}施了肥。")
    elif action == "harvest":
        plant = state["garden"][target]
        if not plant or plant["growth"] < 100: raise ValueError("花还没有成熟")
        state["coins"] += 24; plant["growth"] = 45; plant["bloom_announced"] = False; events.append(f"收获了{plant['name']}，得到 24 云贝。")
    elif action == "clear_plant":
        if not state["garden"][target]: raise ValueError("花盆已经是空的")
        state["garden"][target] = None; events.append("清理了花盆，泥土可以重新播种。")
    elif action == "adopt":
        kind, name = str(payload.get("kind", "")), str(payload.get("name", "")).strip()
        if state.get("pet") or kind not in PET_KINDS: raise ValueError("现在不能领养这只宠物")
        if state["coins"] < 20: raise ValueError("领养需要 20 云贝")
        state["coins"] -= 20; state["pet"] = {"kind": kind, "name": name or PET_KINDS[kind]["name"], "hunger": 82.0, "happiness": 80.0, "hygiene": 88.0, "energy": 75.0, "mood": "happy", "neglect_hours": 0.0, "skills": {}, "cooldowns": {}, "thought": "这里闻起来很新……你会常来看我吗？", "adopted_at": stamp}; events.append(f"{state['pet']['name']}来到小屋了。")
    elif action == "rename_pet":
        pet = state.get("pet")
        name = str(payload.get("name", "")).strip()
        if not pet or not name:
            raise ValueError("请先领养宠物并填写名字")
        pet["name"] = name[:24]; events.append(f"小伙伴有了新名字：{pet['name']}。")
    elif action in {"feed", "rest", "play", "bathe", "school"}:
        pet = state.get("pet")
        if not pet: raise ValueError("还没有领养宠物")
        pet.setdefault("cooldowns", {})
        if action == "feed":
            if state["inventory"].get("pet_food", 0) < 1: raise ValueError("宠物粮用完了")
            state["inventory"]["pet_food"] -= 1; pet["hunger"] = min(100.0, pet["hunger"] + 45); pet["thought"] = "肚子暖暖的，连尾巴都想打拍子。"; events.append(f"给{pet['name']}喂了饭。")
        elif action == "rest":
            if pet["energy"] >= 95: raise ValueError("它现在精神很好，还不需要休息")
            pet["energy"] = min(100.0, pet["energy"] + 45); pet["happiness"] = min(100.0, pet["happiness"] + 4); pet["thought"] = "被子晒过太阳，我要把鼻尖藏进去睡一会儿。"; events.append(f"{pet['name']}回到小窝休息，恢复了精神。")
        elif action == "play":
            if pet["energy"] < 10: raise ValueError("它太累了，先让它休息")
            if not _cooldown_ready(pet, "play", moment): raise ValueError("刚玩过一会儿，等它喘口气再继续")
            pet["energy"] -= 10; pet["happiness"] = min(100.0, pet["happiness"] + 28); pet["thought"] = "刚才那一下我差点就抓到了！下次一定。"; _start_cooldown(pet, "play", moment); events.append(f"陪{pet['name']}玩了一会儿。")
        elif action == "bathe":
            if state["inventory"].get("soap", 0) < 1: raise ValueError("洗护用品用完了")
            state["inventory"]["soap"] -= 1; pet["hygiene"] = 100.0; pet["thought"] = "虽然水花有点讨厌，但现在的毛闻起来真不错。"; events.append(f"给{pet['name']}洗得香香的。")
        else:
            subject = str(payload.get("subject", ""))
            if subject not in SCHOOL_SUBJECTS or pet["energy"] < 20 or state["coins"] < 8: raise ValueError("今天还不能去上这门课")
            if not _cooldown_ready(pet, "school", moment): raise ValueError("刚下课不久，下一堂课还没开始")
            state["coins"] -= 8; pet["energy"] -= 18; pet["skills"][subject] = int(pet["skills"].get(subject, 0)) + 1; pet["thought"] = f"原来{SCHOOL_SUBJECTS[subject]}里藏着这么多小秘密。"; _start_cooldown(pet, "school", moment); events.append(f"{pet['name']}去上了{SCHOOL_SUBJECTS[subject]}课，带回一颗小星星。")
        if pet["hunger"] >= 25 and pet["happiness"] >= 25:
            pet["neglect_hours"] = max(0.0, pet.get("neglect_hours", 0) - 12)
            pet["mood"] = "happy" if pet["neglect_hours"] < 12 else pet["mood"]
    elif action == "configure_management":
        state["management"].update({
            "enabled": bool(payload.get("enabled")),
            "max_actions_per_day": max(1, min(12, int(payload.get("max_actions_per_day", 4)))),
            "daily_budget": max(0, min(500, int(payload.get("daily_budget", 30)))),
        }); events.append("AI 管理授权已经更新。")
    else:
        raise ValueError("未知庭院动作")
    for text in events:
        _event(state, action, text, stamp)
    state["last_settled_at"] = stamp
    return state, events


def auto_manage(source: dict[str, Any], now: datetime | None = None) -> tuple[dict[str, Any], list[str], dict[str, Any] | None]:
    state, _ = settle(source, now)
    config = state.get("management", {})
    if not config.get("enabled"):
        return state, [], None
    choices = allowed_actions(state, now)
    priority = {"feed": 0, "water": 1, "rest": 2, "play": 3, "bathe": 4, "harvest": 5, "clear_plant": 6, "plant": 7, "school": 8, "fertilize": 9, "adopt": 10}
    choices.sort(key=lambda item: priority.get(item["action"], 99))
    remaining = int(config.get("daily_budget", 0)) - int(config.get("spent_today", 0))
    choice = next((item for item in choices if int(item.get("cost", 0)) <= remaining), None)
    if not choice:
        return state, [], None
    payload = dict(choice)
    if payload["action"] == "adopt": payload["name"] = PET_KINDS[payload["kind"]]["name"]
    state, events = act(state, payload, now)
    state["management"]["spent_today"] = int(state["management"].get("spent_today", 0)) + int(choice.get("cost", 0))
    return state, events, choice
