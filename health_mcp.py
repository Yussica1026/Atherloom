from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from enum import Enum
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from backend.health import health_enabled, load_health_summaries


DB_PATH = Path(__file__).resolve().parent / "data" / "local.db"
mcp = FastMCP("health_mcp")


class ResponseFormat(str, Enum):
    JSON = "json"
    MARKDOWN = "markdown"


class HealthRangeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    days: int = Field(default=7, ge=1, le=365, description="回看天数，1 到 365 天")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="返回 markdown 或 json")


def _load(days: int) -> list[dict]:
    if not health_enabled():
        raise RuntimeError("健康密钥未配置，MCP 保持关闭")
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    with closing(connection):
        return load_health_summaries(connection, days)


def _format_json(items: list[dict]) -> str:
    return json.dumps({"diagnostic": False, "count": len(items), "items": items}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="health_get_sleep_summary",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def health_get_sleep_summary(params: HealthRangeInput) -> str:
    """读取用户已授权并同步的睡眠日摘要。

    仅返回 HealthKit 已有记录，不估算缺失睡眠阶段，不提供疾病诊断。
    """
    items = _load(params.days)
    selected = [
        {
            "day": item["day"],
            "sleep_stages": item.get("sleep_stages", []),
            "metrics": {key: value for key, value in item.get("metrics", {}).items() if key.startswith("sleep_")},
            "source": item.get("source"),
        }
        for item in items
    ]
    if params.response_format == ResponseFormat.JSON:
        return _format_json(selected)
    lines = ["# 睡眠摘要", "", "这些是设备记录，不是医疗诊断。", ""]
    for item in selected:
        metrics = item["metrics"]
        lines.append(f"## {item['day']}")
        lines.append(f"- 总睡眠：{metrics.get('sleep_total_minutes', '无记录')} 分钟")
        lines.append(f"- 深睡：{metrics.get('sleep_deep_minutes', '无记录')} 分钟")
        lines.append(f"- REM：{metrics.get('sleep_rem_minutes', '无记录')} 分钟")
    return "\n".join(lines)


@mcp.tool(
    name="health_get_vitals_summary",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def health_get_vitals_summary(params: HealthRangeInput) -> str:
    """读取用户已授权并同步的心率、HRV、血氧和呼吸频率摘要。

    缺失字段表示 HealthKit 没有可见记录；不得据此推断用户拒绝了权限。
    """
    allowed = {
        "resting_heart_rate_bpm", "heart_rate_avg_bpm", "heart_rate_min_bpm",
        "heart_rate_max_bpm", "hrv_ms", "oxygen_saturation_percent", "respiratory_rate_per_min",
    }
    selected = [
        {"day": item["day"], "metrics": {key: value for key, value in item.get("metrics", {}).items() if key in allowed}}
        for item in _load(params.days)
    ]
    if params.response_format == ResponseFormat.JSON:
        return _format_json(selected)
    lines = ["# 身体指标摘要", "", "这些是设备记录，不是医疗诊断。", ""]
    for item in selected:
        metrics = item["metrics"]
        lines.append(f"## {item['day']}")
        lines.append(f"- 静息心率：{metrics.get('resting_heart_rate_bpm', '无记录')} bpm")
        lines.append(f"- HRV：{metrics.get('hrv_ms', '无记录')} ms")
        lines.append(f"- 血氧：{metrics.get('oxygen_saturation_percent', '无记录')}%")
        lines.append(f"- 呼吸频率：{metrics.get('respiratory_rate_per_min', '无记录')} 次/分钟")
    return "\n".join(lines)


@mcp.tool(
    name="health_list_daily_trends",
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def health_list_daily_trends(params: HealthRangeInput) -> str:
    """列出已同步的每日健康趋势，供模型比较变化，不输出诊断或治疗建议。"""
    items = _load(params.days)
    if params.response_format == ResponseFormat.JSON:
        return _format_json(items)
    lines = ["# 每日健康趋势", "", "仅用于观察记录变化。", ""]
    for item in items:
        metrics = item.get("metrics", {})
        compact = " · ".join(f"{key}={value}" for key, value in sorted(metrics.items()))
        lines.append(f"- **{item['day']}**：{compact or '无可用指标'}")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
