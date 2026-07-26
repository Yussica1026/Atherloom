from __future__ import annotations

import base64
import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


SYNC_SECRET_ENV = "ATHERLOOM_HEALTH_SYNC_SECRET"
STORAGE_KEY_ENV = "ATHERLOOM_HEALTH_STORAGE_KEY"


def _key_from_env(name: str) -> bytes:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise RuntimeError(f"{name} 未配置，健康同步保持关闭")
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except ValueError as error:
        raise RuntimeError(f"{name} 不是有效的 Base64 密钥") from error
    if len(key) != 32:
        raise RuntimeError(f"{name} 必须解码为 32 字节")
    return key


def health_enabled() -> bool:
    try:
        _key_from_env(SYNC_SECRET_ENV)
        _key_from_env(STORAGE_KEY_ENV)
        return True
    except RuntimeError:
        return False


def decrypt_sync_envelope(device_id: str, day: str, nonce_b64: str, ciphertext_b64: str) -> dict[str, Any]:
    nonce = base64.b64decode(nonce_b64, validate=True)
    ciphertext = base64.b64decode(ciphertext_b64, validate=True)
    plaintext = AESGCM(_key_from_env(SYNC_SECRET_ENV)).decrypt(
        nonce,
        ciphertext,
        f"{device_id}:{day}".encode("utf-8"),
    )
    payload = json.loads(plaintext)
    if payload.get("day") != day:
        raise ValueError("加密载荷日期与请求日期不一致")
    return payload


def encrypt_for_storage(device_id: str, day: str, payload: dict[str, Any]) -> tuple[bytes, bytes]:
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key_from_env(STORAGE_KEY_ENV)).encrypt(
        nonce,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        f"{device_id}:{day}".encode("utf-8"),
    )
    return nonce, ciphertext


def decrypt_stored_record(device_id: str, day: str, nonce: bytes, ciphertext: bytes) -> dict[str, Any]:
    plaintext = AESGCM(_key_from_env(STORAGE_KEY_ENV)).decrypt(
        nonce,
        ciphertext,
        f"{device_id}:{day}".encode("utf-8"),
    )
    return json.loads(plaintext)


def load_health_summaries(connection: sqlite3.Connection, days: int = 30) -> list[dict[str, Any]]:
    cutoff = (date.today() - timedelta(days=max(1, min(days, 365)) - 1)).isoformat()
    rows = connection.execute(
        """SELECT device_id,day,nonce,ciphertext,updated_at
           FROM health_daily_summaries WHERE day>=? ORDER BY day DESC,updated_at DESC""",
        (cutoff,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    seen_days: set[str] = set()
    for row in rows:
        if row["day"] in seen_days:
            continue
        try:
            payload = decrypt_stored_record(row["device_id"], row["day"], row["nonce"], row["ciphertext"])
        except (ValueError, json.JSONDecodeError):
            continue
        payload["device_id"] = row["device_id"]
        payload["updated_at"] = row["updated_at"]
        result.append(payload)
        seen_days.add(row["day"])
    return result


def normalize_health_payload(payload: dict[str, Any]) -> dict[str, Any]:
    day = date.fromisoformat(str(payload["day"])).isoformat()
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    allowed_metrics = {
        "sleep_total_minutes", "sleep_core_minutes", "sleep_deep_minutes", "sleep_rem_minutes",
        "sleep_awake_minutes", "resting_heart_rate_bpm", "heart_rate_avg_bpm",
        "heart_rate_min_bpm", "heart_rate_max_bpm", "hrv_ms", "oxygen_saturation_percent",
        "respiratory_rate_per_min", "steps", "active_energy_kcal", "weight_kg",
    }
    clean_metrics: dict[str, float] = {}
    for key, value in metrics.items():
        if key not in allowed_metrics or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if number == number and abs(number) < 1_000_000:
            clean_metrics[key] = round(number, 3)
    stages = payload.get("sleep_stages") if isinstance(payload.get("sleep_stages"), list) else []
    clean_stages = []
    for item in stages[:200]:
        if not isinstance(item, dict):
            continue
        stage = str(item.get("stage", "unknown"))[:24]
        start = str(item.get("start", ""))[:40]
        end = str(item.get("end", ""))[:40]
        if stage and start and end:
            clean_stages.append({"stage": stage, "start": start, "end": end})
    return {
        "day": day,
        "timezone": str(payload.get("timezone") or "")[:80],
        "source": str(payload.get("source") or "HealthKit")[:120],
        "metrics": clean_metrics,
        "sleep_stages": clean_stages,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
