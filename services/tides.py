# ============================================================
# services/tides.py — 潮汐服务（WorldTides + 官方表修正 + 本地估算回退）
# ============================================================

import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import streamlit as st

from config import (
    LUNAR_DRIFT_PER_DAY,
    SEMI_DIURNAL_MINUTES,
    TIDE_REF_DAY,
    TIDE_REF_HOUR,
    TIDE_REF_MINUTE,
    TIDE_REF_MONTH,
    TIDE_REF_YEAR,
)

_REFERENCE_HIGH = datetime(
    TIDE_REF_YEAR, TIDE_REF_MONTH, TIDE_REF_DAY, TIDE_REF_HOUR, TIDE_REF_MINUTE
)

_WORLDTIDES_URL = "https://www.worldtides.info/api/v3"
_TIDECHECK_URL = "https://tidecheck.com"
_SYD_TZ = ZoneInfo("Australia/Sydney")
_MIN_EVENT_GAP_MINUTES = 300  # 5h, avoid near-duplicate extremes
_SYDNEY_TIDE_LAT = -33.8610
_SYDNEY_TIDE_LON = 151.2120

# Circular Quay baseline predictions for the current app-critical window.
# BoM blocks automated scraping, so keep local overrides ahead of the rough
# astronomical fallback. The 2026-05-22 row is aligned to the user's reference
# tide app for Circular Quay.
_CIRCULAR_QUAY_TIDES = {
    "2026-05-21": [("06:17", 0.39, False), ("12:20", 1.37, True), ("17:45", 0.70, False)],
    "2026-05-22": [("00:18", 1.78, True), ("07:17", 0.34, False), ("13:22", 1.25, True), ("18:48", 0.63, False)],
    "2026-05-23": [("01:19", 1.77, True), ("08:18", 0.51, False), ("14:29", 1.39, True), ("20:02", 0.78, False)],
    "2026-05-24": [("02:24", 1.67, True), ("09:11", 0.55, False), ("15:28", 1.44, True), ("21:15", 0.78, False)],
    "2026-05-25": [("03:27", 1.58, True), ("09:59", 0.57, False), ("16:22", 1.52, True), ("22:24", 0.76, False)],
    "2026-05-26": [("04:24", 1.50, True), ("10:41", 0.59, False), ("17:10", 1.60, True), ("23:26", 0.72, False)],
    "2026-05-27": [("05:15", 1.45, True), ("11:18", 0.61, False), ("17:53", 1.67, True)],
    "2026-05-28": [("00:19", 0.67, False), ("06:03", 1.41, True), ("11:55", 0.62, False), ("18:32", 1.73, True)],
    "2026-05-29": [("01:05", 0.62, False), ("06:47", 1.39, True), ("12:30", 0.64, False), ("19:08", 1.78, True)],
    "2026-05-30": [("01:45", 0.58, False), ("07:30", 1.37, True), ("13:04", 0.65, False), ("19:44", 1.82, True)],
    "2026-05-31": [("02:24", 0.55, False), ("08:11", 1.36, True), ("13:39", 0.67, False), ("20:18", 1.84, True)],
    "2026-06-01": [("03:00", 0.53, False), ("08:51", 1.35, True), ("14:15", 0.69, False), ("20:54", 1.84, True)],
    "2026-06-02": [("03:39", 0.53, False), ("09:31", 1.33, True), ("14:52", 0.70, False), ("21:30", 1.83, True)],
    "2026-06-03": [("04:18", 0.55, False), ("10:12", 1.32, True), ("15:31", 0.72, False), ("22:08", 1.80, True)],
}


def _estimate_tides_for_date(target_date: datetime, delay_minutes: int = 0) -> list[dict]:
    """天文近似算法：返回落在目标自然日内的潮汐事件。"""
    days_since_ref = (target_date.date() - _REFERENCE_HIGH.date()).days
    midnight = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    high_cycle_minutes = SEMI_DIURNAL_MINUTES * 2
    first_high_minutes = (
        TIDE_REF_HOUR * 60
        + TIDE_REF_MINUTE
        + days_since_ref * LUNAR_DRIFT_PER_DAY
        + delay_minutes
    ) % high_cycle_minutes
    base_high = midnight + timedelta(minutes=first_high_minutes)

    tides = []
    for step in range(-3, 5):
        t = base_high + timedelta(minutes=step * SEMI_DIURNAL_MINUTES)
        if t.date() != target_date.date():
            continue
        is_high = step % 2 == 0
        tides.append(
            {
                "time": t,
                "is_high": is_high,
                "label": "🟢 满潮" if is_high else "🔵 干潮",
                "height_m": None,
                "source": "estimate",
            }
        )
    return sorted(tides, key=lambda x: x["time"])


def _official_circular_quay_for_date(target_date: datetime, delay_minutes: int = 0) -> list[dict]:
    rows = _CIRCULAR_QUAY_TIDES.get(target_date.strftime("%Y-%m-%d"), [])
    tides = []
    for time_text, height_m, is_high in rows:
        hour, minute = [int(x) for x in time_text.split(":")]
        event_time = target_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if delay_minutes:
            event_time += timedelta(minutes=delay_minutes)
        tides.append(
            {
                "time": event_time,
                "is_high": is_high,
                "label": "🟢 满潮" if is_high else "🔵 干潮",
                "height_m": height_m,
                "source": "circular_quay",
            }
        )
    return tides


def _worldtides_key() -> str:
    try:
        return str(st.secrets.get("worldtides_api_key", "")).strip()
    except Exception:
        return os.environ.get("WORLDTIDES_API_KEY", "").strip()


def _secret_or_env(secret_name: str, env_name: str) -> str:
    try:
        value = str(st.secrets.get(secret_name, "")).strip()
        if value:
            return value
    except Exception:
        pass
    return os.environ.get(env_name, "").strip()


def _tidecheck_key() -> str:
    return _secret_or_env("tidecheck_api_key", "TIDECHECK_API_KEY")


def _tidecheck_station_id() -> str:
    return _secret_or_env("tidecheck_station_id", "TIDECHECK_STATION_ID")


def _to_epoch_seconds(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_SYD_TZ)
    return int(dt.timestamp())


def _from_epoch_seconds(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), tz=_SYD_TZ).replace(tzinfo=None)


def _from_utc_iso_to_sydney(value: str) -> datetime:
    iso_value = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(iso_value).astimezone(_SYD_TZ).replace(tzinfo=None)


@st.cache_data(ttl=86400, show_spinner=False)
def _fetch_tidecheck_nearest_station(lat: float, lon: float) -> str:
    key = _tidecheck_key()
    if not key:
        return ""

    response = requests.get(
        f"{_TIDECHECK_URL}/api/stations/nearest",
        params={"lat": f"{lat:.6f}", "lng": f"{lon:.6f}"},
        headers={"X-API-Key": key},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if not data:
        return ""
    return str(data[0].get("id", "")).strip()


@st.cache_data(ttl=21600, show_spinner=False)
def _fetch_tidecheck_extremes(station_id: str, date_key: str) -> list[dict]:
    key = _tidecheck_key()
    if not key or not station_id:
        return []

    response = requests.get(
        f"{_TIDECHECK_URL}/api/station/{station_id}/tides",
        params={"datum": "LAT", "days": 1, "start": date_key},
        headers={"X-API-Key": key},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    parsed = []
    for item in data.get("extremes", []):
        typ = str(item.get("type", "")).lower()
        is_high = typ in {"high", "h"}
        parsed.append(
            {
                "time": _from_utc_iso_to_sydney(item.get("time")),
                "is_high": is_high,
                "label": "🟢 满潮" if is_high else "🔵 干潮",
                "height_m": item.get("height"),
                "source": "tidecheck",
            }
        )

    parsed.sort(key=lambda x: x["time"])
    return parsed


def _get_tidecheck_for_date(target_date: datetime, delay_minutes: int = 0) -> list[dict]:
    if not _tidecheck_key():
        return []

    station_id = _tidecheck_station_id()
    if not station_id:
        station_id = _fetch_tidecheck_nearest_station(_SYDNEY_TIDE_LAT, _SYDNEY_TIDE_LON)
    if not station_id:
        return []

    events = _fetch_tidecheck_extremes(station_id, target_date.strftime("%Y-%m-%d"))
    picked = _pick_events_for_date(events, target_date)
    if not picked:
        return []
    if delay_minutes:
        picked = [
            {
                **event,
                "time": event["time"] + timedelta(minutes=delay_minutes),
            }
            for event in picked
        ]
    return picked


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_worldtides_extremes(lat: float, lon: float, date_key: str) -> list[dict]:
    """仅请求 extremes，省额度。date_key 用于缓存分片。"""
    key = _worldtides_key()
    if not key:
        return []

    target = datetime.strptime(date_key, "%Y-%m-%d")
    start = datetime(target.year, target.month, target.day, tzinfo=_SYD_TZ) - timedelta(hours=12)
    end = start + timedelta(days=2)

    params = {
        "key": key,
        "lat": f"{lat:.6f}",
        "lon": f"{lon:.6f}",
        "start": _to_epoch_seconds(start),
        "end": _to_epoch_seconds(end),
        "extremes": "",
        "localtime": "true",
    }

    response = requests.get(_WORLDTIDES_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise RuntimeError(data.get("error"))

    parsed = []
    for item in data.get("extremes", []):
        dt = _from_epoch_seconds(item.get("dt"))
        typ = str(item.get("type", "")).lower()
        is_high = typ == "high"
        parsed.append(
            {
                "time": dt,
                "is_high": is_high,
                "label": "🟢 满潮" if is_high else "🔵 干潮",
                "height_m": item.get("height"),
                "source": "worldtides",
            }
        )

    parsed.sort(key=lambda x: x["time"])
    return parsed


def _pick_events_for_date(all_events: list[dict], target_date: datetime) -> list[dict]:
    events = [e for e in all_events if e["time"].date() == target_date.date()]
    if not events:
        return []

    deduped = []
    for ev in events:
        if not deduped:
            deduped.append(ev)
            continue
        prev = deduped[-1]
        gap_min = (ev["time"] - prev["time"]).total_seconds() / 60.0
        if gap_min < _MIN_EVENT_GAP_MINUTES and ev["is_high"] == prev["is_high"]:
            # Keep earlier one for stability.
            continue
        deduped.append(ev)
    return deduped


def get_tides_for_date(
    target_date: datetime,
    delay_minutes: int = 0,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> list[dict]:
    """
    返回某天潮汐事件。优先 WorldTides，其次 Circular Quay 基准表，
    最后回退到本地估算。

    参数：
        target_date    目标日期
        delay_minutes  相对 Circular Quay 的本地潮时偏移
        lat/lon        可选，提供时才会尝试 WorldTides
    """
    tidecheck_events = _get_tidecheck_for_date(target_date, delay_minutes=delay_minutes)
    if len(tidecheck_events) >= 2:
        return tidecheck_events

    if lat is not None and lon is not None and _worldtides_key():
        try:
            all_events = _fetch_worldtides_extremes(float(lat), float(lon), target_date.strftime("%Y-%m-%d"))
            picked = _pick_events_for_date(all_events, target_date)
            if len(picked) >= 2:
                return picked
        except Exception:
            pass

    official = _official_circular_quay_for_date(target_date, delay_minutes=delay_minutes)
    if official:
        return official

    return _estimate_tides_for_date(target_date, delay_minutes=delay_minutes)


def get_tide_accuracy_hint() -> str:
    """Return a short UX hint about current tide data confidence."""
    if _tidecheck_key():
        return "潮汐精度：TideCheck harmonic predictions（API 缓存 6 小时）"
    if _worldtides_key():
        return "潮汐精度：WorldTides 实时极值（通常约 ±5 分钟）"
    today = datetime.now(_SYD_TZ)
    if today.strftime("%Y-%m-%d") in _CIRCULAR_QUAY_TIDES:
        return "潮汐精度：Circular Quay 基准潮汐（本地钓点按延迟修正）"
    return "潮汐精度：天文估算（通常约 ±30–60 分钟）"
