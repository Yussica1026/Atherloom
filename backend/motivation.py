from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

DRIVES = {
    "connection": {"label": "联结", "baseline": 35, "growth": .35, "decay": .25, "threshold": 70},
    "curiosity": {"label": "好奇", "baseline": 45, "growth": .2, "decay": .1, "threshold": 60},
    "reflection": {"label": "反思", "baseline": 40, "growth": .15, "decay": .2, "threshold": 55},
    "duty": {"label": "责任", "baseline": 45, "growth": .1, "decay": .2, "threshold": 70},
    "social": {"label": "交流", "baseline": 40, "growth": .1, "decay": .15, "threshold": 65},
    "fatigue": {"label": "疲劳", "baseline": 25, "growth": 0, "decay": .3, "threshold": 80},
    "closeness": {"label": "亲近", "baseline": 35, "growth": .2, "decay": .1, "threshold": 70},
    "stress": {"label": "压力", "baseline": 25, "growth": 0, "decay": .2, "threshold": 80},
    "joy": {"label": "愉悦", "baseline": 35, "growth": 0, "decay": .15, "threshold": 80},
}

EVENTS = {
    "contact_message": {"connection": -5, "closeness": 3, "joy": 2},
    "contact_silent": {"connection": 8, "stress": 3},
    "task_done": {"duty": -15, "stress": -5, "curiosity": 5},
    "conversation": {"social": -8, "curiosity": 2},
    "diary_written": {"reflection": -10, "stress": -3},
    "conflict": {"stress": 25, "connection": 12, "closeness": 10},
    "reconcile": {"stress": -20, "connection": -5, "closeness": 8},
    "heavy_work": {"fatigue": 15, "duty": 5, "stress": 5},
    "rest": {"fatigue": -20, "stress": -5},
    "discovery": {"curiosity": -10, "reflection": 5, "joy": 5},
    "happy_moment": {"joy": 15, "stress": -5, "closeness": 3},
    "creative_done": {"joy": 10, "curiosity": -5},
}

COUPLING = {
    ("connection", "closeness"): .3, ("closeness", "connection"): -.2,
    ("stress", "fatigue"): .4, ("fatigue", "curiosity"): -.3,
    ("curiosity", "duty"): .2, ("duty", "stress"): .1,
    ("reflection", "stress"): -.2, ("social", "connection"): -.1,
    ("connection", "stress"): .1, ("stress", "closeness"): .2,
    ("closeness", "stress"): -.3, ("joy", "stress"): -.3,
    ("joy", "fatigue"): -.2, ("joy", "curiosity"): .2,
    ("stress", "joy"): -.3, ("fatigue", "joy"): -.2,
}

THOUGHTS = {
    "connection": "想确认重要的人是否安好",
    "curiosity": "想探索一个还不熟悉的问题",
    "reflection": "想整理最近发生的事情",
    "duty": "还有一件值得完成的任务",
    "social": "想和信任的人认真聊聊",
    "fatigue": "需要暂时休息并降低负荷",
    "closeness": "想靠近重要的人并给予回应",
    "stress": "需要先处理压力来源",
    "joy": "想把此刻的愉快分享出去",
}


def clamp(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 2)


def default_state() -> dict[str, Any]:
    return {
        "drives": {key: float(config["baseline"]) for key, config in DRIVES.items()},
        "baselines": {key: float(config["baseline"]) for key, config in DRIVES.items()},
        "thoughts": [], "tick_count": 0, "last_tick": datetime.now(timezone.utc).isoformat(),
    }


def normalize(state: dict[str, Any] | None) -> dict[str, Any]:
    base = default_state()
    if not state:
        return base
    for key in DRIVES:
        base["drives"][key] = clamp(state.get("drives", {}).get(key, base["drives"][key]))
        base["baselines"][key] = clamp(state.get("baselines", {}).get(key, base["baselines"][key]))
    base["thoughts"] = list(state.get("thoughts", []))[-20:]
    base["tick_count"] = int(state.get("tick_count", 0))
    base["last_tick"] = str(state.get("last_tick", base["last_tick"]))
    return base


def apply_event(state: dict[str, Any], event: str) -> list[dict[str, Any]]:
    normalized = normalize(state)
    state.clear()
    state.update(normalized)
    changes = []
    for drive, delta in EVENTS.get(event, {}).items():
        before = state["drives"][drive]
        multiplier = 1 + ((before if delta < 0 else 100 - before) / 100) * .5
        after = clamp(before + delta * multiplier)
        state["drives"][drive] = after
        changes.append({"drive": drive, "before": before, "after": after})
    return changes


def tick(state: dict[str, Any]) -> dict[str, Any]:
    state = normalize(state)
    pending = {key: 0.0 for key in DRIVES}
    for key, config in DRIVES.items():
        value, baseline = state["drives"][key], state["baselines"][key]
        delta = config["growth"] - (config["decay"] if value > baseline else -config["decay"] if value < baseline else 0)
        state["drives"][key] = clamp(value + delta)
    for (source, target), coefficient in COUPLING.items():
        pending[target] += (state["drives"][source] - state["baselines"][source]) / 100 * coefficient * 10
    for key, delta in pending.items():
        state["drives"][key] = clamp(state["drives"][key] + delta)
        state["baselines"][key] = clamp(.995 * state["baselines"][key] + .005 * state["drives"][key])
    generated = []
    for key, value in state["drives"].items():
        if value > 30 and random.random() < (value - 30) / 100:
            content = THOUGHTS[key]
            existing = next((item for item in state["thoughts"] if item["content"] == content), None)
            if existing:
                existing["count"] += 1
                existing["obsession"] = existing["count"] >= 3
            else:
                item = {"id": f"thought-{state['tick_count'] + 1}-{key}", "content": content, "source": key, "count": 1, "obsession": False}
                state["thoughts"].append(item); generated.append(item)
    state["thoughts"] = state["thoughts"][-20:]
    state["tick_count"] += 1
    state["last_tick"] = datetime.now(timezone.utc).isoformat()
    urgency = max(state["drives"]["connection"], state["drives"]["stress"])
    return {"state": state, "generated": generated, "next_interval": int(2700 - 1800 * urgency / 100)}


def context_summary(state: dict[str, Any]) -> str:
    state = normalize(state)
    active = sorted(state["drives"].items(), key=lambda item: item[1], reverse=True)[:3]
    active_text = "、".join(f"{DRIVES[key]['label']} {value:.0f}/100" for key, value in active)
    thoughts = [item["content"] for item in state["thoughts"] if item.get("obsession")][-2:]
    note = f"；当前持续念头：{'；'.join(thoughts)}" if thoughts else ""
    return f"<motivation_state>当前较突出的内部驱动：{active_text}{note}。这是行为参考，不是必须表演的情绪，也不得绕过工具权限。</motivation_state>"
