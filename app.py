# ============================================================
# app.py — 上鱼啦 Beta 主程序
# ============================================================

import math
import re
import base64
import html
import time
from pathlib import Path
import numpy as np
import streamlit as st
import plotly.graph_objects as go
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import folium
from streamlit_folium import st_folium

import config as cfg
from services.weather import get_marine_forecast
from services.tides import get_tides_for_date, get_tide_source_label, is_estimated_tide
from services.fuel import get_nearby_fuel
from services.stats import (
    record_visit_start, record_visit_end, render_stats_panel,
    get_or_create_session_id,
)
from services import log as fishing_log
from data.loader import load_spots
from domain.safety import assess_safety
from domain.recommendation import log_reputation_score, recommendation_score

ALL_METHODS = getattr(cfg, "ALL_METHODS", [])
ALL_FISH = getattr(cfg, "ALL_FISH", [])
OCEAN_SWELL_DANGER = getattr(cfg, "OCEAN_SWELL_DANGER", 1.3)
OCEAN_WIND_DANGER = getattr(cfg, "OCEAN_WIND_DANGER", 20)
SHELTERED_SWELL_WARN = getattr(cfg, "SHELTERED_SWELL_WARN", 1.4)
SHELTERED_WIND_WARN = getattr(cfg, "SHELTERED_WIND_WARN", 22)
REGION_FILTER_MAP = getattr(cfg, "REGION_FILTER_MAP", {})
FISH_LEGAL_SIZE = getattr(cfg, "FISH_LEGAL_SIZE", {})
FISH_COOKING = getattr(cfg, "FISH_COOKING", {})

st.set_page_config(
    page_title="上鱼啦 Beta",
    page_icon="🐟",
    layout="wide",
)

# 追踪访问开始
record_visit_start()

spots = load_spots()
MAP_MARKER_LIMIT = 120
MAP_MARKER_LIMIT_MOBILE = 70
FAST_SPOT_LIMIT = 30
DESKTOP_SPOT_LIMIT = 30
HERO_SPOT_LIMIT = 80
SYD_TZ = ZoneInfo("Australia/Sydney")
LOG_MAX_PHOTOS = 4
LOG_MAX_PHOTO_BYTES = 3 * 1024 * 1024

_CSS_PATH = Path(__file__).with_name("styles.css")
st.markdown(f"<style>{_CSS_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

_MASCOT_PATH = Path(__file__).with_name("assets").joinpath("logo-duckfish-transparent.png")
if _MASCOT_PATH.exists():
    _MASCOT_B64 = base64.b64encode(_MASCOT_PATH.read_bytes()).decode("ascii")
    _MASCOT_DATA_URL = f"data:image/png;base64,{_MASCOT_B64}"
else:
    _MASCOT_DATA_URL = ""


# ── 侧边栏 ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        '<div style="display:flex;gap:12px;align-items:center;padding:20px 0 16px">'
        + (
            f'<div style="width:52px;height:52px;border-radius:50%;background:transparent;'
            f'border:none;display:flex;align-items:center;justify-content:center;overflow:hidden">'
            f'<img src="{_MASCOT_DATA_URL}" style="width:100%;height:100%;object-fit:contain" alt="logo"/></div>'
            if _MASCOT_DATA_URL else
            '<div style="width:42px;height:42px;border-radius:10px;background:rgba(255,255,255,0.08);'
            'border:1px solid rgba(255,255,255,0.12);display:flex;align-items:center;'
            'justify-content:center;font-size:22px">🐟</div>'
        )
        +
        '<div><div class="brand-q" style="font-size:20px;font-weight:400;'
        'color:var(--text-on-dark);line-height:1.05">上鱼啦'
        '<span style="font-family:var(--mono);font-size:10px;font-weight:500;letter-spacing:1.2px;'
        'opacity:0.78;margin-left:6px;vertical-align:middle">BETA</span></div>'
        '<div style="margin-top:4px">'
        '<span style="font-family:var(--mono);font-size:10px;letter-spacing:1.5px;'
        'color:var(--gold);background:rgba(198,146,48,0.18);padding:2px 7px;border-radius:4px">'
        'Sydney</span>'
        '</div></div></div>',
        unsafe_allow_html=True,
    )
    # 处理跨控件导航请求（必须在 radio 实例化之前写入 session_state）
    if "_nav_to" in st.session_state:
        st.session_state["selected_page"] = st.session_state.pop("_nav_to")
    selected_page = st.radio(
        "页面",
        ["🎣 钓点推荐", "📖 渔获日记"],
        label_visibility="collapsed",
        horizontal=True,
        key="selected_page",
    )
    st.markdown(
        '<div style="height:1px;background:rgba(255,255,255,0.08);margin:2px 0 14px"></div>',
        unsafe_allow_html=True,
    )

    if selected_page == "🎣 钓点推荐":
        st.markdown(
            '<div style="font-size:11px;font-family:var(--mono);letter-spacing:2px;'
            'color:var(--gold);opacity:0.8;margin-bottom:8px">智能筛选</div>',
            unsafe_allow_html=True,
        )
        selected_methods = st.multiselect("钓法", ALL_METHODS, key="sel_methods", placeholder="选择钓法 · Method")
        selected_fish    = st.multiselect("目标鱼种", ALL_FISH, key="sel_fish", placeholder="选择鱼种 · Species")
        selected_region  = st.selectbox(
            "地理区域",
            ["全部 · All Sydney"] + list(REGION_FILTER_MAP.keys()),
        )
        water_type       = st.selectbox("水域类型", ["全部 · All", "🌊 外海 Ocean", "🚤 船钓 Boat", "⚓ 内湾 Harbour", "🔀 咸淡水 Brackish", "🏞️ 淡水 Freshwater"])
        safe_only        = st.checkbox("隐藏危险钓点 ⚠", value=False)
        family_only      = st.checkbox("仅看家庭友好 👨‍👩‍👧 (⭐⭐⭐⭐+)", value=False)
        sort_by          = st.selectbox(
            "钓点排序",
            ["推荐优先", "家庭友好优先"],
            key="sel_sort",
        )
        if st.button("↺ 重置所有筛选", use_container_width=True):
            st.session_state["sel_methods"] = []
            st.session_state["sel_fish"] = []
            st.rerun()
    else:
        selected_methods = []
        selected_fish    = []
        selected_region  = "全部 · All Sydney"
        water_type       = "全部 · All"
        safe_only        = False
        family_only      = False
        sort_by          = "推荐优先"

    st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
    st.markdown(
        '<div style="border:1px solid rgba(255,255,255,0.1);border-radius:10px;'
        'padding:10px 12px;font-size:11.5px;color:var(--muted-on-dark);line-height:1.7">'
        '🎫 <b style="color:var(--text-on-dark)">NSW 钓鱼执照</b><br>'
        '多数人在 NSW 淡水/海水<br>作钓需付费并携带收据<br>'
        '<a href="https://www.service.nsw.gov.au/services/recreational-fishing-licence" '
        'target="_blank" style="color:var(--gold)">查看官方费用与豁免 →</a>'
        '</div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div style="height:20px"></div>', unsafe_allow_html=True)
    st.markdown(
        '<div style="font-size:11px;font-family:var(--mono);letter-spacing:2px;'
        'color:rgba(234,241,248,0.5);margin-bottom:10px">DATA SOURCES</div>'
        '<div style="font-size:12px;line-height:1.8;color:var(--muted-on-dark)">'
        '🛰 <b style="color:var(--text-on-dark)">天气</b> · Open-Meteo'
        '<span style="opacity:0.6;font-size:11px"> (缓存 1 小时)</span><br>'
        '🌊 <b style="color:var(--text-on-dark)">海况</b> · 坐标分区'
        '<span style="opacity:0.6;font-size:11px"> (动态获取)</span><br>'
        '⏱ <b style="color:var(--text-on-dark)">潮汐</b> · 天文推算'
        '<span style="opacity:0.6;font-size:11px"> (±30–60 min)</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="font-family:var(--mono);font-size:10px;color:rgba(234,241,248,0.3);'
        'margin-top:20px;text-align:center">© 2026 · Built with Streamlit</div>',
        unsafe_allow_html=True,
    )


# ── 工具函数 ──────────────────────────────────────────────────────────────

def section_head(kicker: str, title: str, accent: str = "") -> None:
    st.markdown(
        f'<div style="margin:4px 0 14px">'
        f'<div style="font-family:var(--mono);font-size:11px;color:var(--subtle);'
        f'letter-spacing:2px;text-transform:uppercase;margin-bottom:4px">{kicker}</div>'
        f'<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">'
        f'<h2 style="margin:0;font-family:var(--serif-zh);font-weight:600;font-size:22px;color:var(--text)">{title}</h2>'
        f'<span style="font-size:13px;color:var(--muted);line-height:1.4">{accent}</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )


def _pill(text: str, tone: str = "blue", sm: bool = True) -> str:
    size_css = "padding:2px 9px;font-size:11.5px" if sm else "padding:4px 11px;font-size:12.5px"
    colors = {
        "blue":   ("background:#e6efff", "color:#2a5fb0"),
        "gold":   ("background:#fbf3e1", "color:#a37618"),
        "coral":  ("background:#fbeae8", "color:#b1453b"),
        "amber":  ("background:#fcf2e0", "color:#a06c20"),
        "sage":   ("background:#e7f3ec", "color:#3a7f5d"),
        "violet": ("background:#efeaff", "color:#6e4ebe"),
    }
    bg, fg = colors.get(tone, colors["blue"])
    return (f'<span style="{bg};{fg};{size_css};border-radius:999px;'
            f'font-weight:500;margin:2px;display:inline-flex;align-items:center;gap:4px">'
            f'{html.escape(str(text))}</span>')


def _status_text(safety: dict) -> str:
    return {"sage": "推荐", "amber": "谨慎", "coral": "危险"}.get(safety.get("color"), "参考")


def _status_badge(safety: dict) -> str:
    tone = safety.get("color", "sage")
    text = _status_text(safety)
    colors = {
        "sage": ("#e7f3ec", "#3a7f5d", "#4f9b76"),
        "amber": ("#fcf2e0", "#a06c20", "#d99540"),
        "coral": ("#fbeae8", "#b1453b", "#cc5e54"),
    }
    bg, fg, border = colors.get(tone, colors["sage"])
    return (
        f'<span style="background:{bg};color:{fg};border:1px solid {border};'
        f'padding:2px 10px;border-radius:999px;font-size:0.76em;font-weight:700">'
        f'{text}</span>'
    )


def _reputation_badge(safety: dict) -> str:
    score = safety.get("log_reputation_score") or 0
    if score <= 0:
        return ""
    return (
        '<span style="background:#f4f0ff;color:#5b45a8;border:1px solid #d8cdfa;'
        'padding:2px 10px;border-radius:999px;font-size:0.76em;font-weight:700">'
        f'钓友口碑 +{score:g}</span>'
    )


def _limited_pills(items: list, tone: str, limit: int = 3) -> str:
    visible = items[:limit]
    rest = len(items) - len(visible)
    html = "".join(_pill(item, tone) for item in visible)
    if rest > 0:
        html += _pill(f"+{rest}", "gold")
    return html


def _mini_stat(label: str, value: str, tone: str = "text") -> str:
    colors = {
        "text": "var(--text)",
        "sage": "var(--sage)",
        "amber": "var(--amber)",
        "coral": "var(--coral)",
        "blue": "var(--primary)",
    }
    color = colors.get(tone, colors["text"])
    return (
        '<div class="mini-stat" style="background:#f7fafc;border-radius:8px;padding:8px 10px;'
        'border:1px solid #edf3f8;min-width:84px">'
        f'<div style="color:#8fa3b1;font-size:0.68em;margin-bottom:3px">{html.escape(str(label))}</div>'
        f'<div class="mini-stat-value" style="font-family:var(--ui-zh),var(--ui);font-size:1.05em;font-weight:600;'
        f'color:{color};line-height:1.15">{html.escape(str(value))}</div>'
        '</div>'
    )


def _hero_stat_tile(value: str, label: str, sublabel: str, tone: str = "light", hint: str = "") -> str:
    value_color = "#f5c842" if tone == "gold" else "#ffffff"
    accent      = "#c69230" if tone == "gold" else "rgba(255,255,255,0.30)"
    return (
        f'<div class="hero-stat-tile" style="background:rgba(255,255,255,0.10);'
        f'border:1px solid rgba(255,255,255,0.14);border-radius:14px;'
        f'padding:14px 22px;min-width:124px;backdrop-filter:blur(8px);'
        f'border-top:2px solid {accent}">'
        f'<div class="hero-stat-value" style="font-family:\'Source Serif 4\',Georgia,serif;font-size:40px;line-height:1;'
        f'font-weight:400;color:{value_color};letter-spacing:-1px">{value}</div>'
        f'<div class="hero-stat-label" style="font-size:13px;color:rgba(255,255,255,0.92);margin-top:7px;font-weight:500">{label}</div>'
        '</div>'
    )


def _fishing_coords(spot: dict) -> tuple[float, float]:
    return spot.get("fishing_lat", spot["lat"]), spot.get("fishing_lon", spot["lon"])


def _weather_coords(spot: dict) -> tuple[float, float]:
    fish_lat, fish_lon = _fishing_coords(spot)
    return spot.get("weather_lat", fish_lat), spot.get("weather_lon", fish_lon)


def _nav_coords(spot: dict) -> tuple[float, float]:
    fish_lat, fish_lon = _fishing_coords(spot)
    return spot.get("nav_lat", fish_lat), spot.get("nav_lon", fish_lon)


def _map_coords(spot: dict) -> tuple[float, float]:
    fish_lat, fish_lon = _fishing_coords(spot)
    return spot.get("map_lat", fish_lat), spot.get("map_lon", fish_lon)


def _forecast_key(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, 4), round(lon, 4))


def _now_sydney() -> datetime:
    return datetime.now(SYD_TZ).replace(tzinfo=None)


def _is_mobile_user_agent() -> bool:
    try:
        headers = getattr(st.context, "headers", {}) or {}
        ua = str(headers.get("user-agent", "")).lower()
        return any(k in ua for k in ("iphone", "android", "mobile", "ipad"))
    except Exception:
        return False


def _deg_to_swell_dir(deg) -> str:
    try:
        d = float(deg) % 360
    except (TypeError, ValueError):
        return "—"
    dirs = ["北浪", "东北浪", "东浪", "东南浪", "南浪", "西南浪", "西浪", "西北浪"]
    return dirs[int((d + 22.5) / 45) % 8]


def _deg_to_wind_dir(deg) -> str:
    try:
        d = float(deg) % 360
    except (TypeError, ValueError):
        return "—"
    dirs = ["北风", "东北风", "东风", "东南风", "南风", "西南风", "西风", "西北风"]
    return dirs[int((d + 22.5) / 45) % 8]


def _beaufort(kmh: float) -> str:
    if kmh < 1:   return "0级·无风"
    if kmh < 6:   return "1级·软风"
    if kmh < 12:  return "2级·轻风"
    if kmh < 20:  return "3级·微风"
    if kmh < 29:  return "4级·和风"
    if kmh < 39:  return "5级·劲风"
    if kmh < 50:  return "6级·强风"
    if kmh < 62:  return "7级·疾风"
    if kmh < 75:  return "8级·大风"
    return "9+级·烈风"

def _parse_tag(tag: str) -> tuple:
    m = re.match(r'^(.+?)\s*[(\（](.+?)[)\）]$', tag.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return tag, ""


_WT_LABEL = {"ocean": "🌊 外海", "boat": "🚤 船钓", "harbour": "⚓ 内湾", "brackish": "🔀 咸淡水", "freshwater": "🏞️ 淡水"}
_WT_PILL  = {"ocean": "blue", "boat": "blue", "harbour": "blue", "brackish": "violet", "freshwater": "sage"}
_WT_EMOJI = {"ocean": "🌊", "boat": "🚤", "harbour": "⚓", "brackish": "🔀", "freshwater": "🏞️"}


def _wt_pill(spot: dict, sm: bool = True) -> str:
    wt = spot.get("water_type", "harbour")
    return _pill(_WT_LABEL.get(wt, wt), _WT_PILL.get(wt, "blue"), sm=sm)


def _freshwater_season_pill() -> str:
    month = datetime.now().month
    if 4 <= month <= 10:
        return _pill("🎯 旺季", "sage")
    return _pill("☀️ 淡季", "amber")


# 咸水鱼种旺季（月份列表）
_FISH_PEAK_MONTHS = {
    "Kingfish (黄尾师)":   [10, 11, 12, 1, 2, 3],   # 夏季回游
    "Tailor (蓝鱼)":       [3, 4, 5, 6, 7, 8, 9],    # 秋冬
    "Australian Salmon (澳三)":     [3, 4, 5, 6, 7, 8],        # 秋冬
    "Drummer (黑毛)":      [4, 5, 6, 7, 8, 9, 10],    # 凉季
    "Jewfish (皇冠鲊)":    [10, 11, 12, 1, 2],        # 夏季
    "Squid (鱿鱼)":        [3, 4, 5, 6, 7, 8, 9, 10], # 秋冬春
    "Australian Bass (澳洲鲈鱼)": [4, 5, 6, 7, 8, 9, 10],
    "Golden Perch (黄金鲈)": [4, 5, 6, 7, 8, 9],
    "Tuskfish (青衣)": [10, 11, 12, 1, 2, 3],  # 夏季礁盘活跃
    "Wrasse (彩衣)": [10, 11, 12, 1, 2, 3],
}

def _saltwater_season_pills(fish_tags: list) -> str:
    month = datetime.now().month
    in_season = [t for t in fish_tags if month in _FISH_PEAK_MONTHS.get(t, [])]
    if not in_season:
        return ""
    en_names = [_parse_tag(t)[0] for t in in_season[:2]]
    label = "·".join(en_names) + " 旺季"
    return _pill(f"🎯 {label}", "sage")


def _moon_phase(target_date: datetime) -> tuple:
    """Return (emoji, name_zh, fishing_tip) for the moon phase on target_date."""
    # Synodic period ≈ 29.53 days; reference new moon: 2000-01-06
    ref = datetime(2000, 1, 6)
    days = (target_date.replace(tzinfo=None) - ref).days % 29.53
    if days < 1.85:
        return "🌑", "新月", "潮差最大·鱼类进食最活跃"
    elif days < 7.38:
        return "🌒", "上弦前", "潮汐渐强·适合出击"
    elif days < 9.22:
        return "🌓", "上弦月", "潮汐适中"
    elif days < 14.77:
        return "🌔", "渐盈凸月", "潮汐渐强"
    elif days < 16.61:
        return "🌕", "满月", "潮差最大·夜钓黄金期"
    elif days < 22.15:
        return "🌖", "渐亏凸月", "潮汐渐弱"
    elif days < 23.99:
        return "🌗", "下弦月", "潮汐适中"
    else:
        return "🌘", "残月", "潮汐偏弱·选内湾佳"


def _legal_sizes_html(fish_tags: list) -> str:
    rows = []
    for tag in fish_tags:
        info = FISH_LEGAL_SIZE.get(tag, {})
        size = info.get("size")
        bag  = info.get("bag")
        note = info.get("note", "")
        _, cn = _parse_tag(tag)
        label = cn or tag

        if "禁捕" in note or "保护" in note:
            size_str = '<span style="color:#cc5e54;font-weight:700">⚠️ 禁捕</span>'
        elif "有害" in note:
            size_str = '<span style="color:#d99540;font-weight:700">须就地处理</span>'
        elif size:
            size_str = f'<span style="color:#2a5fb0;font-weight:700">≥ {size} cm</span>'
        else:
            size_str = '<span style="color:#8a9cb2">无限制</span>'

        bag_str = ""
        if bag is not None and bag > 0:
            bag_str = f' <span style="color:#8a9cb2;font-size:10px">袋限 {bag} 条</span>'
        elif bag == 0:
            bag_str = ""

        rows.append(
            f'<div style="display:flex;align-items:center;justify-content:space-between;'
            f'padding:4px 0;border-bottom:1px solid var(--line)">'
            f'<span style="font-size:11.5px;color:var(--text)">{label}</span>'
            f'<span style="font-size:11.5px">{size_str}{bag_str}</span>'
            f'</div>'
        )
    return "".join(rows)


def _cooking_guide_html(fish_tags: list, max_items: int = 4) -> str:
    rows = []
    for tag in fish_tags[:max_items]:
        en, cn = _parse_tag(tag)
        name = cn or en
        cook = FISH_COOKING.get(tag, "清蒸 / 香煎 / 炙烤")
        rows.append(
            f'<div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start;'
            f'padding:5px 0;border-bottom:1px solid var(--line)">'
            f'<span style="font-size:11.5px;color:var(--text)">{name}</span>'
            f'<span style="font-size:11.5px;color:#5c6f84;text-align:right">{cook}</span>'
            f'</div>'
        )
    return "".join(rows)


def _fish_rules_cook_html(fish_tags: list, max_items: int = 5) -> str:
    rows = []
    for tag in fish_tags[:max_items]:
        info = FISH_LEGAL_SIZE.get(tag, {})
        size = info.get("size")
        bag = info.get("bag")
        note = info.get("note", "")
        _, cn = _parse_tag(tag)
        name = cn or tag
        cook = FISH_COOKING.get(tag, "清蒸 / 香煎 / 炙烤")

        if "禁捕" in note or "保护" in note:
            size_str = "禁捕"
        elif "有害" in note:
            size_str = "须就地处理"
        elif "≥" in note:
            # note 中已含分品种尺寸说明（如短鳓鰼≥30cm；长鳓鰼≥58cm），直接显示
            size_str = note
        elif size:
            size_str = f"≥ {size} cm"
        else:
            size_str = "无限制"

        if bag is not None and bag > 0 and "≥" not in note:
            size_str += f" · 袋限 {bag} 条"

        rows.append(
            f'<div style="display:grid;grid-template-columns:minmax(0,0.9fr) minmax(120px,0.85fr) minmax(0,1.25fr);'
            f'gap:8px;align-items:start;padding:6px 0;border-bottom:1px solid var(--line)">'
            f'<span style="font-size:11.5px;color:var(--text)">{name}</span>'
            f'<span style="font-size:11.5px;color:#2a5fb0">{size_str}</span>'
            f'<span style="font-size:11.5px;color:#5c6f84">{cook}</span>'
            f'</div>'
        )
    return "".join(rows)


def _spot_matches_with_filters(
    spot: dict,
    methods: tuple[str, ...],
    fish: tuple[str, ...],
    region: str,
    wt_filter: str,
    family: bool,
) -> bool:
    if methods and not any(m in spot["supported_methods"] for m in methods):
        return False
    if fish and not any(f in spot["fish_tags"] for f in fish):
        return False
    if region and region != "全部 · All Sydney":
        keywords = REGION_FILTER_MAP.get(region, [region])
        if not any(kw in spot["region"] for kw in keywords):
            return False
    wt_map = {
        "🌊 外海 Ocean":       "ocean",
        "🚤 船钓 Boat":       "boat",
        "⚓ 内湾 Harbour":     "harbour",
        "🔀 咸淡水 Brackish":  "brackish",
        "🏞️ 淡水 Freshwater": "freshwater",
    }
    if wt_filter in wt_map and spot.get("water_type") != wt_map[wt_filter]:
        return False
    if family:
        stars = spot["family_friendly"].count("⭐")
        if stars < 4:
            return False
    return True


def spot_matches(spot: dict) -> bool:
    return _spot_matches_with_filters(
        spot,
        tuple(selected_methods),
        tuple(selected_fish),
        selected_region,
        water_type,
        family_only,
    )


@st.cache_data(ttl=900, show_spinner=False)
def _build_day_payload(
    day_offset: int,
    sydney_date_key: str,
    methods: tuple[str, ...],
    fish: tuple[str, ...],
    region: str,
    wt_filter: str,
    family: bool,
    max_spots: int,
) -> dict:
    target_date = datetime.strptime(sydney_date_key, "%Y-%m-%d")
    filtered_all = [
        s for s in spots
        if _spot_matches_with_filters(s, methods, fish, region, wt_filter, family)
    ]
    filtered = filtered_all if max_spots <= 0 else filtered_all[:max_spots]

    forecast_by_spot = {}
    forecast_by_coord = {}
    for spot in filtered:
        w_lat, w_lon = _weather_coords(spot)
        key = _forecast_key(w_lat, w_lon)
        if key not in forecast_by_coord:
            forecast_by_coord[key] = get_marine_forecast(w_lat, w_lon)
        forecast_by_spot[spot["name"]] = forecast_by_coord[key]

    all_spot_data = []
    for spot in filtered:
        forecast = forecast_by_spot[spot["name"]]
        spot_day_w = forecast["days"][day_offset]
        safety = assess_safety(spot, spot_day_w)
        f_lat, f_lon = _fishing_coords(spot)
        tides = get_tides_for_date(target_date, spot["tide_delay"], lat=f_lat, lon=f_lon)
        safety = {
            **safety,
            "rank_score": recommendation_score(
                spot,
                safety,
                spot_day_w,
                tides,
                methods,
                fish,
            ),
        }
        all_spot_data.append((spot, safety, tides, spot_day_w))

    _safety_order = {"sage": 0, "amber": 1, "coral": 2}
    all_spot_data.sort(
        key=lambda x: (
            _safety_order.get(x[1]["color"], 3),
            -x[1].get("rank_score", 0),
            x[0]["name"],
        )
    )
    return {
        "filtered": filtered,
        "filtered_total": len(filtered_all),
        "forecast_by_spot": forecast_by_spot,
        "all_spot_data": all_spot_data,
    }


def _val_color(value: float, warn: float, danger: float) -> str:
    if value >= danger: return "#cc5e54"
    if value >= warn:   return "#d99540"
    return "#4f9b76"


# ── 天气面板 ──────────────────────────────────────────────────────────────

def render_weather_panel(day_weather: dict, data_ok: bool, next_day: dict = None) -> None:
    if not data_ok:
        st.warning("⚠️ 天气数据加载失败，以下为估算值。")

    temp         = day_weather.get("temp") or 0
    temp_min     = day_weather.get("temp_min") or 0
    wind         = day_weather.get("wind") or 0
    swell        = day_weather.get("swell_height") or 0
    rain_prob    = day_weather.get("rain_prob") or 0
    precipitation= day_weather.get("precipitation") or 0
    swell_dir    = day_weather.get("swell_direction", "—")
    swell_period = day_weather.get("swell_period", "—")
    wind_dir     = day_weather.get("wind_direction", "—")

    swell_dir_text = _deg_to_swell_dir(swell_dir)
    wind_dir_text  = _deg_to_wind_dir(wind_dir)
    beaufort_text  = _beaufort(float(wind) if wind else 0)
    rain_str  = f"{precipitation:.1f} mm" if precipitation > 0 else "无降水"

    t_hi  = "#4f9b76"
    t_lo  = "#2a5fb0"
    r_col = "#cc5e54" if rain_prob >= 60 else ("#d99540" if rain_prob >= 25 else "#4f9b76")
    w_col = _val_color(wind,  SHELTERED_WIND_WARN,  OCEAN_WIND_DANGER)
    s_col = _val_color(swell, SHELTERED_SWELL_WARN, OCEAN_SWELL_DANGER)

    def _trend(cur, nxt, label="明天"):
        if nxt is None: return ""
        ratio = nxt / cur if cur > 0 else 1
        if ratio < 0.85:
            return f'<span style="font-size:11px;color:#4f9b76;font-weight:600">↓ {label}改善</span>'
        if ratio > 1.15:
            return f'<span style="font-size:11px;color:#cc5e54;font-weight:600">↑ {label}恶化</span>'
        return f'<span style="font-size:11px;color:#8a9cb2">→ {label}持平</span>'

    nd_wind  = (next_day.get("wind") or 0)  if next_day else None
    nd_swell = (next_day.get("swell_height") or 0) if next_day else None
    wind_trend  = _trend(wind,  nd_wind)
    swell_trend = _trend(swell, nd_swell)

    def _card(label, body_html):
        return (
            f'<div style="background:var(--surface);border:1px solid var(--line);'
            f'border-radius:14px;padding:18px 20px;box-shadow:0 2px 6px rgba(15,30,50,0.025);height:100%">'
            f'<div style="font-family:var(--mono);font-size:10.5px;color:var(--subtle);'
            f'letter-spacing:1.5px;text-transform:uppercase;margin-bottom:12px">{label}</div>'
            f'{body_html}'
            f'</div>'
        )

    row1_c1, row1_c2 = st.columns(2)
    row2_c1, row2_c2 = st.columns(2)

    row1_c1.markdown(_card("气温 · TEMPERATURE", f"""
        <div style="display:flex;gap:28px;align-items:baseline">
            <div>
                <div style="font-family:var(--serif-en);font-size:36px;font-weight:400;
                            color:{t_hi};line-height:1">{temp}°</div>
                <div style="font-size:11.5px;color:var(--muted);margin-top:4px">最高</div>
            </div>
            <div>
                <div style="font-family:var(--serif-en);font-size:36px;font-weight:400;
                            color:{t_lo};line-height:1">{temp_min}°</div>
                <div style="font-size:11.5px;color:var(--muted);margin-top:4px">最低</div>
            </div>
        </div>
    """), unsafe_allow_html=True)

    row1_c2.markdown(_card("降水 · PRECIPITATION", f"""
        <div style="display:flex;gap:28px;align-items:baseline">
            <div>
                <div style="font-family:var(--serif-en);font-size:36px;font-weight:400;
                            color:{r_col};line-height:1">{int(rain_prob)}%</div>
                <div style="font-size:11.5px;color:var(--muted);margin-top:4px">降雨概率</div>
            </div>
            <div>
                <div style="font-family:var(--serif-en);font-size:28px;font-weight:400;
                            color:var(--text);line-height:1">{rain_str}</div>
                <div style="font-size:11.5px;color:var(--muted);margin-top:4px">预计降水</div>
            </div>
        </div>
    """), unsafe_allow_html=True)

    row2_c1.markdown(_card("风 · WIND", f"""
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px">
            <div>
                <div style="font-family:var(--serif-en);font-size:36px;font-weight:400;
                            color:{w_col};line-height:1">{wind}</div>
                <div style="font-size:11.5px;color:var(--muted);margin-top:4px">风速 km/h</div>
            </div>
            <div style="text-align:right;padding-top:4px">{wind_trend}</div>
        </div>
        <div style="font-size:12.5px;color:var(--muted);line-height:1.8">
            <span style="color:var(--text);font-weight:500">{wind_dir_text}</span>
            <span style="opacity:0.35;margin:0 6px">·</span>
            <span style="color:var(--text);font-weight:500">{beaufort_text}</span>
        </div>
    """), unsafe_allow_html=True)

    row2_c2.markdown(_card("浪涌 · SWELL", f"""
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px">
            <div>
                <div style="font-family:var(--serif-en);font-size:36px;font-weight:400;
                            color:{s_col};line-height:1">{swell}</div>
                <div style="font-size:11.5px;color:var(--muted);margin-top:4px">浪高 m</div>
            </div>
            <div style="text-align:right;padding-top:4px">{swell_trend}</div>
        </div>
        <div style="font-size:12.5px;color:var(--muted);line-height:1.8">
            <span style="color:var(--text);font-weight:500">{swell_dir_text}</span>
            <span style="opacity:0.35;margin:0 6px">·</span>
            周期 <span style="color:var(--text);font-weight:500">{swell_period}s</span>
        </div>
    """), unsafe_allow_html=True)

    if rain_prob < 60:
        uv = day_weather.get("uv") or 0
        uv_val = int(round(uv))
        if uv_val <= 2:
            uv_col, uv_label, uv_tip = "#4f9b76", "低", "无需特别防护"
        elif uv_val <= 5:
            uv_col, uv_label, uv_tip = "#d99540", "中等", "SPF 30+ 推荐"
        elif uv_val <= 7:
            uv_col, uv_label, uv_tip = "#cc5e54", "高", "SPF 50+ 必备，帽子眼镜必带"
        elif uv_val <= 10:
            uv_col, uv_label, uv_tip = "#b03060", "极高", "户外务必全副防晒，减少暴露"
        else:
            uv_col, uv_label, uv_tip = "#6a0dad", "危险", "尽量避免正午户外"
    if rain_prob < 60:
        st.markdown(
        f'<div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;'
        f'padding:12px 20px;margin-top:8px;display:flex;align-items:center;gap:14px;'
        f'box-shadow:0 2px 6px rgba(15,30,50,0.025)">'
        f'<div style="font-family:var(--mono);font-size:10.5px;color:var(--subtle);'
        f'letter-spacing:1.5px;text-transform:uppercase;white-space:nowrap">☀️ UV 指数</div>'
        f'<div style="font-family:var(--serif-en);font-size:28px;font-weight:400;'
        f'color:{uv_col};line-height:1">{uv_val}</div>'
        f'<span style="background:{uv_col}22;color:{uv_col};padding:2px 10px;'
        f'border-radius:999px;font-size:12px;font-weight:600">{uv_label}</span>'
        f'<div style="font-size:12px;color:var(--muted);margin-left:auto">{uv_tip}</div>'
        f'</div>',
        unsafe_allow_html=True,
        )


# ── 最佳时段推算 ──────────────────────────────────────────────────────────

def _best_window_times(best_window: str, tides: list) -> str:
    text = best_window

    explicit = re.findall(r'\d{1,2}:\d{2}\s*[–\-~至到]\s*\d{1,2}:\d{2}', text)
    if explicit:
        return re.sub(r'\s*[~至到]\s*', '–', explicit[0]).replace(" ", "")

    sorted_tides = sorted(tides, key=lambda t: t["time"]) if tides else []

    hrs_match = re.search(r'(\d+(?:\.\d+)?)[\s]*小时', text)
    offset_h  = float(hrs_match.group(1)) if hrs_match else 1.5

    DAY_START = 6    # 06:00
    DAY_END   = 19   # 19:00

    def is_daytime(dt):
        return DAY_START <= dt.hour < DAY_END

    def fmt_window(center, before_h, after_h):
        start = center - timedelta(hours=before_h)
        end   = center + timedelta(hours=after_h)
        # clamp both ends into daylight
        if start.hour < DAY_START:
            start = start.replace(hour=DAY_START, minute=0)
        if end.hour >= DAY_END or (end.hour == DAY_END and end.minute > 0):
            end = end.replace(hour=DAY_END, minute=0)
        if start >= end:
            return None
        return f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"

    def best_daytime_tide(candidates):
        daytime = [t for t in candidates if is_daytime(t["time"])]
        return (daytime or candidates)[0]

    if ("满潮" in text or "涨潮" in text or "高潮" in text) and sorted_tides:
        highs = [t for t in sorted_tides if t["is_high"]]
        if highs:
            result = fmt_window(best_daytime_tide(highs)["time"], offset_h, offset_h)
            if result:
                return result

    if ("干潮" in text or "落潮" in text or "低潮" in text) and sorted_tides:
        lows = [t for t in sorted_tides if not t["is_high"]]
        if lows:
            result = fmt_window(best_daytime_tide(lows)["time"], offset_h, offset_h)
            if result:
                return result

    if "破晓" in text or "黎明" in text or "日出" in text:
        return "06:00–08:30"
    if "黄昏" in text or "日落" in text:
        return "17:00–19:00"
    if "夜间" in text or "夜晚" in text:
        return "06:00–08:30"   # 夜钓点改推晨钓
    if "白天" in text or "正午" in text:
        return "09:00–16:00"

    # Last-resort fallback
    return "06:30–09:00 / 16:00–19:00"


# ── 潮汐面板（Plotly） ────────────────────────────────────────────────────

def render_tide_panel(base_tides: list, chart_key: str = "tide", target_date: datetime = None) -> None:
    def _height_for_event(td: dict) -> float:
        height_m = td.get("height_m")
        if isinstance(height_m, (int, float)):
            return float(height_m)
        return 1.7 if td["is_high"] else 0.4

    def _interpolated_height(t: datetime, events: list[dict]) -> tuple[float, bool]:
        prev_td = next_td = None
        for td in events:
            if td["time"] <= t:
                prev_td = td
            elif next_td is None:
                next_td = td
                break
        if prev_td is None:
            h = _height_for_event(events[0])
            return h, True
        if next_td is None:
            h = _height_for_event(events[-1])
            return h, events[-1]["is_high"]

        ph = _height_for_event(prev_td)
        nh = _height_for_event(next_td)
        period = (next_td["time"] - prev_td["time"]).total_seconds()
        elapsed = (t - prev_td["time"]).total_seconds()
        frac = elapsed / period if period > 0 else 0
        h = ph + (nh - ph) * (1 - math.cos(math.pi * frac)) / 2
        return h, nh > ph

    now = _now_sydney()
    sorted_tides = sorted(base_tides, key=lambda x: x["time"])
    is_today     = target_date is None or target_date.date() == now.date()

    ref_day  = target_date if target_date is not None else now
    midnight = ref_day.replace(hour=0, minute=0, second=0, microsecond=0)

    prev_tides = get_tides_for_date(ref_day - timedelta(days=1))
    next_tides = get_tides_for_date(ref_day + timedelta(days=1))
    all_tides  = sorted(prev_tides + base_tides + next_tides, key=lambda x: x["time"])

    x_hours = np.linspace(0, 24, 360)
    y_m = [_interpolated_height(midnight + timedelta(hours=float(xh)), all_tides)[0] for xh in x_hours]

    now_h = now.hour + now.minute / 60
    now_height, is_rising = _interpolated_height(now, all_tides)
    next_event = next((td for td in sorted_tides if td["time"] > now), None) if is_today else None
    if next_event is None and is_today:
        future = [td for td in next_tides if td["time"] > now]
        next_event = future[0] if future else None
    next_high = next((td for td in all_tides if td["time"] > now and td["is_high"]), None) if is_today else None

    y_max = max(max(y_m), *[_height_for_event(td) for td in sorted_tides], 1.6)
    y_range = [0, max(2.0, math.ceil((y_max + 0.15) * 10) / 10)]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x_hours, y=y_m, mode="lines",
        line=dict(color="#2f70b7", width=3, shape="spline", smoothing=1.15),
        fill="tozeroy", fillcolor="rgba(47,112,183,0.16)",
        hovertemplate="%{x:.1f}:00 · %{y:.2f}m<extra></extra>",
    ))
    event_x, event_y, event_text, event_color = [], [], [], []
    for td in sorted_tides:
        if td["time"].date() != ref_day.date():
            continue
        xh = td["time"].hour + td["time"].minute / 60
        if not 0 <= xh <= 24:
            continue
        height_m = _height_for_event(td)
        event_x.append(xh)
        event_y.append(height_m)
        event_text.append(
            f'{"满潮" if td["is_high"] else "干潮"} {td["time"].strftime("%H:%M")} · {height_m:.2f}m'
        )
        event_color.append("#c69230" if td["is_high"] else "#8a9cb2")
    if event_x:
        fig.add_trace(go.Scatter(
            x=event_x, y=event_y, mode="markers",
            marker=dict(size=10, color=event_color, line=dict(width=2, color="#fff")),
            customdata=event_text,
            hovertemplate="%{customdata}<extra></extra>",
            showlegend=False,
        ))
    if is_today and 0 <= now_h <= 24:
        idx = int(now_h / 24 * (len(y_m) - 1))
        fig.add_vline(x=now_h, line=dict(color="#d94b45", width=1.4, dash="dash"))
        fig.add_trace(go.Scatter(
            x=[now_h], y=[y_m[idx]], mode="markers",
            marker=dict(size=11, color="#d94b45", line=dict(width=1.8, color="#fff")),
            hoverinfo="skip", showlegend=False,
        ))
    fig.update_layout(
        template="plotly_white", height=210,
        margin=dict(l=32, r=8, t=8, b=28), showlegend=False,
        dragmode=False,
        xaxis=dict(
            range=[0, 24],
            fixedrange=True,
            tickvals=[0, 4, 8, 12, 16, 20, 24],
            ticktext=["00:00", "04:00", "08:00", "12:00", "16:00", "20:00", "24:00"],
            tickfont=dict(family="IBM Plex Mono", size=10, color="#8a9cb2"),
            gridcolor="rgba(15,30,50,0.06)",
        ),
        yaxis=dict(
            range=y_range,
            fixedrange=True,
            title=dict(text="m", font=dict(family="IBM Plex Mono", size=10, color="#8a9cb2")),
            tickfont=dict(family="IBM Plex Mono", size=10, color="#8a9cb2"),
            gridcolor="rgba(15,30,50,0.06)",
        ),
    )
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={
            "displayModeBar": False,
            "scrollZoom": False,
            "doubleClick": False,
        },
        key=chart_key,
    )

    if is_today:
        if next_high:
            delta = next_high["time"] - now
            total_min = max(0, int(delta.total_seconds() // 60))
            next_high_text = f"{total_min // 60}h {total_min % 60}m"
        else:
            next_high_text = "—"
        next_event_text = (
            f'下个{"满潮" if next_event["is_high"] else "干潮"} {next_event["time"].strftime("%H:%M")}'
            if next_event else "今日潮点已过"
        )
        rising_col = "#2f70b7" if is_rising else "#8a9cb2"
        rising_arrow = "↑ 涨潮" if is_rising else "↓ 退潮"
        st.markdown(
            f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:4px 0 10px">'
            f'<div style="background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 12px">'
            f'<div style="font-family:var(--mono);font-size:9.5px;color:var(--subtle);letter-spacing:1px;text-transform:uppercase;margin-bottom:5px">下个满潮</div>'
            f'<div style="font-family:var(--serif-en);font-size:22px;font-weight:400;color:var(--text);line-height:1">{next_high_text}</div></div>'
            f'<div style="background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 12px;text-align:center">'
            f'<div style="font-family:var(--mono);font-size:9.5px;color:var(--subtle);letter-spacing:1px;text-transform:uppercase;margin-bottom:5px">当前潮位</div>'
            f'<div style="font-family:var(--serif-en);font-size:22px;font-weight:700;color:var(--text);line-height:1">{now_height:.2f}<span style="font-size:13px;font-weight:400"> m</span></div>'
            f'<div style="font-size:11px;font-weight:600;color:{rising_col};margin-top:3px">{rising_arrow}</div></div>'
            f'<div style="background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 12px;text-align:right">'
            f'<div style="font-family:var(--mono);font-size:9.5px;color:var(--subtle);letter-spacing:1px;text-transform:uppercase;margin-bottom:5px">下个潮点</div>'
            f'<div style="font-family:var(--mono);font-size:12px;color:var(--text);line-height:1.4">{next_event_text}</div></div>'
            f'</div>',
            unsafe_allow_html=True,
        )



# ── 精选推荐英雄卡 ────────────────────────────────────────────────────────

def render_hero_card(
    col,
    spot: dict,
    safety: dict,
    day_weather: dict,
    tides: list = None,
    forecast_days: list = None,
) -> None:
    color_map = {"sage": "#4f9b76", "amber": "#d99540", "coral": "#cc5e54"}
    bg_map    = {"sage": "#e7f3ec", "amber": "#fcf2e0", "coral": "#fbeae8"}
    bar_map   = {"sage": "linear-gradient(180deg,#6bc995,#4f9b76)",
                 "amber": "linear-gradient(180deg,#f0b85a,#d99540)",
                 "coral": "linear-gradient(180deg,#e07a72,#cc5e54)"}

    border   = color_map.get(safety["color"], "#4f9b76")
    badge_bg = bg_map.get(safety["color"], "#e7f3ec")
    bar_grad = bar_map.get(safety["color"], bar_map["sage"])

    is_fw    = spot.get("water_type") == "freshwater"
    swell    = day_weather.get("swell_height") or 0
    wind     = day_weather.get("wind") or 0
    sw_color = "#8a9cb2" if is_fw else _val_color(swell, SHELTERED_SWELL_WARN, OCEAN_SWELL_DANGER)
    wi_color = _val_color(wind, SHELTERED_WIND_WARN, OCEAN_WIND_DANGER)
    swell_val_html = "淡水" if is_fw else f"{swell}m"
    swell_label    = "水域" if is_fw else "涌浪"
    time_window = _best_window_times(spot["best_window"], tides) if tides else "—"

    fish_html   = _limited_pills(spot["fish_tags"], "blue", limit=3)
    method_html = _limited_pills(spot["supported_methods"], "violet", limit=3)
    wt_html     = _wt_pill(spot)
    season_html = _freshwater_season_pill() if is_fw else _saltwater_season_pills(spot["fish_tags"])

    # 三天预报 dots
    _dc = {"sage": "#4f9b76", "amber": "#d99540", "coral": "#cc5e54"}
    if forecast_days is not None:
        _fc_days = forecast_days
    else:
        w_lat, w_lon = _weather_coords(spot)
        _fc_days = get_marine_forecast(w_lat, w_lon)["days"]
    _hero_dots = "".join(
        f'<div style="text-align:center;line-height:1.2">'
        f'<div style="font-size:9px;color:#aaa">{lb}</div>'
        f'<div style="width:8px;height:8px;border-radius:50%;'
        f'background:{_dc[assess_safety(spot, _fc_days[di])["color"]]};margin:2px auto"></div>'
        f'</div>'
        for di, lb in enumerate(["今","明","后"])
    )
    hero_dots_html = (
        f'<div style="display:flex;gap:3px;align-items:center;'
        f'background:#f4f8fc;border-radius:7px;padding:3px 7px">{_hero_dots}</div>'
    )

    spot_name = html.escape(str(spot["name"]))
    spot_region = html.escape(str(spot["region"]))
    spot_type = html.escape(str(spot["type"]))

    card_html = f"""
    <div class="hero-pick-card" style="background:white;border-radius:14px;padding:18px 20px 16px 24px;
                box-shadow:0 2px 8px rgba(24,66,112,0.06);
                position:relative;overflow:hidden;min-height:205px;
                border:1px solid rgba(219,231,242,0.8)">
        <div style="position:absolute;left:0;top:0;bottom:0;width:5px;
                    background:{border};border-radius:14px 0 0 14px"></div>
        <div class="hero-pick-title" style="font-family:var(--serif-zh);font-weight:600;font-size:1.05em;color:#102338;
                    line-height:1.35;margin-bottom:8px">{spot_name}</div>
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:6px">
            {_status_badge(safety)}
            {_reputation_badge(safety)}
            {wt_html}{season_html}{hero_dots_html}
        </div>
        <div class="hero-pick-meta" style="color:#60758a;font-size:0.78em;margin:4px 0 10px">
            {spot_region} &nbsp;·&nbsp; {spot_type}
        </div>
        <div class="hero-stats-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-bottom:12px">
            {_mini_stat(swell_label, swell_val_html, "text" if is_fw else safety["color"])}
            {_mini_stat("风速", f"{wind}km/h", safety["color"] if wi_color != "#4f9b76" else "text")}
            {_mini_stat("最佳时段", time_window, "blue")}
        </div>
        <div style="margin-bottom:5px">{fish_html}</div>
        <div>{method_html}</div>
    </div>
    """
    col.html(card_html)


# ── 钓点详情卡片 ──────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def _load_log_by_spot() -> dict:
    """Load all fishing log entries grouped by spot_name (cached 60s)."""
    from services import log as fishing_log
    entries = fishing_log.get_entries(limit=500)
    by_spot: dict = {}
    for e in entries:
        by_spot.setdefault(e["spot_name"], []).append(e)
    return by_spot


def _apply_reputation_scores(
    all_spot_data: list,
    log_by_spot: dict,
    methods: tuple[str, ...],
    fish: tuple[str, ...],
) -> list:
    enriched = []
    for spot, safety, tides, spot_day_w in all_spot_data:
        log_score = log_reputation_score(log_by_spot.get(spot["name"], []), _now_sydney().date())
        ranked_safety = {
            **safety,
            "log_reputation_score": log_score,
            "rank_score": recommendation_score(
                spot,
                safety,
                spot_day_w,
                tides,
                methods,
                fish,
                reputation_score=log_score,
            ),
        }
        enriched.append((spot, ranked_safety, tides, spot_day_w))

    safety_order = {"sage": 0, "amber": 1, "coral": 2}
    enriched.sort(
        key=lambda x: (
            safety_order.get(x[1]["color"], 3),
            -x[1].get("rank_score", 0),
            x[0]["name"],
        )
    )
    return enriched


def render_spot_card(
    spot: dict,
    safety: dict,
    spot_tides: list,
    spot_weather: dict,
    day_offset: int,
    forecast_days: list = None,
    log_entries: list = None,
) -> None:
    color_map = {"sage": "#4f9b76", "amber": "#d99540", "coral": "#cc5e54"}
    bg_map    = {"sage": "#e7f3ec", "amber": "#fcf2e0", "coral": "#fbeae8"}
    bar_map   = {"sage": "linear-gradient(180deg,#6bc995,#4f9b76)",
                 "amber": "linear-gradient(180deg,#f0b85a,#d99540)",
                 "coral": "linear-gradient(180deg,#e07a72,#cc5e54)"}
    text_map  = {"sage": "#3a7f5d", "amber": "#a06c20", "coral": "#b1453b"}

    border    = color_map.get(safety["color"], "#4f9b76")
    badge_bg  = bg_map.get(safety["color"], "#e7f3ec")
    bar_grad  = bar_map.get(safety["color"], bar_map["sage"])
    badge_txt = text_map.get(safety["color"], "#3a7f5d")

    is_fw = spot.get("water_type") == "freshwater"
    swell = spot_weather.get("swell_height") or 0
    wind  = spot_weather.get("wind") or 0
    sw_color    = "#8a9cb2" if is_fw else _val_color(swell, SHELTERED_SWELL_WARN, OCEAN_SWELL_DANGER)
    wi_color    = _val_color(wind,  SHELTERED_WIND_WARN,  OCEAN_WIND_DANGER)
    swell_val_html  = "淡水" if is_fw else f"{swell}m"
    swell_label_str = "🏞️ 水域" if is_fw else "🌊 浪涌"
    time_window = _best_window_times(spot["best_window"], spot_tides)

    fish_chips   = _limited_pills(spot["fish_tags"], "blue", limit=3)
    method_chips = _limited_pills(spot["supported_methods"], "violet", limit=3)
    wt_badge     = _wt_pill(spot)
    season_badge = _freshwater_season_pill() if is_fw else _saltwater_season_pills(spot["fish_tags"])

    toggle_key = (
        f"det_{day_offset}_"
        + spot["name"].replace(" ", "_").replace("(", "").replace(")", "").replace("/", "_")
    )
    if toggle_key not in st.session_state:
        st.session_state[toggle_key] = False
    is_open = st.session_state[toggle_key]

    # 三天安全预报小圆点（仅在展开时计算，降低列表首屏计算量）
    three_day_dots = ""
    if is_open:
        _dot_c = {"sage": "#4f9b76", "amber": "#d99540", "coral": "#cc5e54"}
        if forecast_days is not None:
            _fc_days = forecast_days
        else:
            w_lat, w_lon = _weather_coords(spot)
            _fc_days = get_marine_forecast(w_lat, w_lon)["days"]
        _day_labels = ["今", "明", "后"]
        _dots_parts = []
        for _di, _lbl in enumerate(_day_labels):
            _ds = assess_safety(spot, _fc_days[_di])
            _c  = _dot_c[_ds["color"]]
            _dots_parts.append(
                f'<div style="text-align:center;line-height:1.2">'
                f'<div style="font-size:9px;color:#aaa">{_lbl}</div>'
                f'<div style="width:9px;height:9px;border-radius:50%;background:{_c};margin:1px auto"></div>'
                f'</div>'
            )
        three_day_dots = (
            '<div style="display:flex;gap:4px;align-items:center;'
            'background:#f4f8fc;border-radius:8px;padding:4px 7px">'
            + "".join(_dots_parts) + "</div>"
        )

    # Safety advice — only shown inline for amber/coral; sage is self-explanatory
    advice_html = ""
    if safety["color"] != "sage":
        advice_html = (
            f'<div style="background:{badge_bg};border-left:3px solid {border};'
            f'border-radius:0 8px 8px 0;padding:7px 12px;margin:10px 0 4px;'
            f'font-size:11.5px;color:{badge_txt};line-height:1.5">'
            f'{safety["advice"]}'
            f'</div>'
        )

    family_stars = spot["family_friendly"].count("⭐")
    family_display = "⭐" * family_stars if family_stars else "—"
    stat_tone = safety["color"] if safety["color"] != "sage" else "text"

    st.markdown(
        f'<div style="position:relative;background:white;border-radius:14px;'
        f'box-shadow:0 2px 8px rgba(24,66,112,0.05);'
        f'border:1px solid rgba(219,231,242,0.85);'
        f'margin-bottom:0;padding:16px 18px 20px 24px;overflow:hidden">'

        f'<div style="position:absolute;left:0;top:0;bottom:0;width:5px;'
        f'background:{border};border-radius:14px 0 0 14px"></div>'

        f'<div class="spot-main-grid" style="display:grid;grid-template-columns:minmax(0,1.7fr) minmax(260px,1fr);'
        f'gap:18px;align-items:start">'

        f'<div>'
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap">'
        f'<div style="font-family:var(--serif-zh);font-size:1.08em;font-weight:600;'
        f'color:#102338;line-height:1.35">{spot["name"]}</div>'
        f'{_status_badge(safety)}{_reputation_badge(safety)}{wt_badge}{season_badge}{three_day_dots}'
        f'</div>'
        f'<div style="color:#6f8196;font-size:0.8em;line-height:1.5;margin-bottom:10px">'
        f'{spot["region"]} · {spot["type"]}</div>'
        f'{advice_html}'
        f'<div style="margin-bottom:4px">{fish_chips}</div>'
        f'</div>'

        f'<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px">'
        f'{_mini_stat("浪涌" if not is_fw else "水域", swell_val_html, stat_tone if not is_fw else "text")}'
        f'{_mini_stat("风速", f"{wind}km/h", stat_tone if wi_color != "#4f9b76" else "text")}'
        f'{_mini_stat("黄金时段", time_window, "blue")}'
        f'{_mini_stat("家庭", family_display, "text")}'
        f'</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if st.button(("收起详情 ▴" if is_open else "展开详情 ▾"), key=toggle_key + "_btn"):
        st.session_state[toggle_key] = not is_open
        st.rerun()

    if is_open:
        show_methods = (
            [m for m in selected_methods if m in spot["supported_methods"]]
            if selected_methods else spot["supported_methods"]
        )

        # ── Tide rows ────────────────────────────────────────
        wt = spot.get("water_type", "harbour")
        if wt == "freshwater":
            tide_rows = (
                '<div style="display:flex;flex-direction:column;justify-content:center;'
                'min-height:116px;text-align:center;padding:12px 0;gap:6px">'
                '<div style="font-size:20px">🏞️</div>'
                '<div style="font-size:11px;color:var(--muted);line-height:1.6">'
                '淡水钓点<br>无潮汐影响</div>'
                '</div>'
            )
        else:
            tide_rows = ""
            for td in spot_tides:
                dot = "#c69230" if td["is_high"] else "#8a9cb2"
                lbl = "满潮" if td["is_high"] else "干潮"
                height_m = td.get("height_m")
                height_html = (
                    f'<span style="font-family:var(--mono);font-size:10.5px;color:var(--muted)"> · {height_m:.2f}m</span>'
                    if isinstance(height_m, (int, float)) else ""
                )
                tide_rows += (
                    f'<div style="display:flex;align-items:center;justify-content:space-between;'
                    f'gap:10px;padding:7px 0;border-bottom:1px solid var(--line)">'
                    f'<div style="display:flex;align-items:center;gap:8px">'
                    f'<div style="width:7px;height:7px;border-radius:50%;background:{dot};flex-shrink:0"></div>'
                    f'<span style="font-size:11.5px;color:var(--muted)">{lbl}{height_html}</span>'
                    f'</div>'
                    f'<span style="font-family:var(--mono);font-size:12px;color:{dot};font-weight:600">'
                    f'{td["time"].strftime("%H:%M")}</span>'
                    f'</div>'
                )
            if wt == "brackish":
                tide_rows += (
                    '<div style="margin-top:6px;font-size:10.5px;color:var(--amber);line-height:1.5">'
                    '⚠️ 咸淡水区域受上游来水影响，实际潮时可能偏晚，仅供参考'
                    '</div>'
                )

        # ── Legal sizes ──────────────────────────────────────
        fish_rules_cook_html = _fish_rules_cook_html(spot["fish_tags"], max_items=5)

        # ── Method rows ───────────────────────────────────────
        method_rows = ""
        tip_icons = {"🎯": "#2a5fb0", "👍": "#4f9b76", "⚠": "#d99540"}
        for method in show_methods:
            tip = spot["method_tips"].get(method, "此钓法在该钓点完全可行，建议现场根据流水微调线组铅重。")
            icon_color = next((c for k, c in tip_icons.items() if k in tip), "#8a9cb2")
            short_name = method.split("(")[0].strip()
            method_rows += (
                f'<div style="padding:8px 0 10px;border-bottom:1px dashed #e6edf4">'
                f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">'
                f'<div style="width:7px;height:7px;border-radius:50%;background:{icon_color}"></div>'
                f'<div style="font-size:12.5px;font-weight:700;color:#1f3147;">{short_name}</div>'
                f'</div>'
                f'<div style="font-size:12px;color:#5c6f84;line-height:1.58;padding-left:15px">{tip}</div>'
                f'</div>'
            )
        key_points_html = (
            f'<div style="margin-top:10px;background:#f8fbff;border:1px solid #e3edf7;'
            f'border-radius:10px;padding:10px 11px">'
            f'<div style="font-family:var(--mono);font-size:10px;letter-spacing:1.3px;color:#7f95ab;'
            f'text-transform:uppercase;margin-bottom:6px">Quick Notes · 现场要点</div>'
            f'<div style="font-size:11.5px;color:#4c6278;line-height:1.62">'
            f'👨‍👩‍👧‍👦 家庭友好：{spot["family_friendly"]}<br>'
            f'⏱️ 建议窗口：{spot["best_window"]}<br>'
            f'🐟 当前鱼情：{spot.get("active_fish", "以现场鱼讯为准")}'
            f'</div>'
            f'</div>'
        )

        nav_lat, nav_lon = _nav_coords(spot)
        maps_url = f"https://www.google.com/maps?q={nav_lat},{nav_lon}"

        _log_count = len(log_entries) if log_entries else 0
        _log_label = f"📖 渔获 {_log_count}" if _log_count else "📖 渔获日记"
        tab1, tab2, tab3, tab4 = st.tabs(["📊 海况 & 潮汐", "🎣 钓法攻略", "🗺️ 导航路线", _log_label])

        with tab1:
            st.markdown(
                f'<div class="detail-main-grid" style="display:grid;'
                f'grid-template-columns:minmax(200px,1fr) minmax(0,1.45fr);gap:12px;align-items:start">'

                f'<div style="background:var(--surface);border:1px solid #e2eaf2;border-radius:10px;padding:12px 13px">'
                f'<div style="font-family:var(--mono);font-size:10px;letter-spacing:1.4px;color:#8a9cb2;'
                f'text-transform:uppercase;margin-bottom:8px">Tide · 专属潮汐</div>'
                f'{tide_rows}'
                f'</div>'

                f'<div style="background:var(--surface);border:1px solid #e2eaf2;border-radius:10px;padding:12px 13px">'
                f'<div style="font-family:var(--mono);font-size:10px;letter-spacing:1.4px;color:#8a9cb2;'
                f'text-transform:uppercase;margin-bottom:6px">Rules & Cook · 法规与烹饪</div>'
                f'<div style="display:grid;grid-template-columns:minmax(0,0.9fr) minmax(100px,0.85fr) minmax(0,1.25fr);'
                f'gap:8px;padding:0 0 5px;border-bottom:1px solid var(--line);margin-bottom:2px">'
                f'<span style="font-size:10.5px;color:#7f95ab">鱼种</span>'
                f'<span style="font-size:10.5px;color:#7f95ab">法定尺寸</span>'
                f'<span style="font-size:10.5px;color:#7f95ab">推荐做法</span>'
                f'</div>'
                f'{fish_rules_cook_html}'
                f'</div>'

                f'</div>',
                unsafe_allow_html=True,
            )

        with tab2:
            st.markdown(
                f'<div style="background:var(--surface);border:1px solid #e2eaf2;'
                f'border-radius:10px;padding:14px 16px">'
                f'<div style="font-family:var(--mono);font-size:10px;letter-spacing:1.4px;color:#8a9cb2;'
                f'text-transform:uppercase;margin-bottom:10px">Methods · 钓法攻略</div>'
                f'{method_rows}'
                f'</div>'
                f'{key_points_html}',
                unsafe_allow_html=True,
            )

        with tab3:
            st.markdown(
                f'<div class="detail-bottom-grid" style="display:grid;'
                f'grid-template-columns:minmax(0,1.2fr) minmax(0,1fr) minmax(0,1.05fr);gap:10px">'

                f'<div style="background:var(--surface);border:1px solid #dfe8f1;'
                f'border-top:2px solid var(--primary);border-radius:10px;padding:12px 14px">'
                f'<div style="font-family:var(--mono);font-size:10px;letter-spacing:1.4px;color:#8a9cb2;'
                f'text-transform:uppercase;margin-bottom:6px">Route · 自驾路线</div>'
                f'<div style="font-size:12px;color:#213447;line-height:1.62">{spot["route"]}</div>'
                f'<a href="{maps_url}" target="_blank" '
                f'style="display:inline-block;margin-top:8px;font-size:11.5px;color:#2a5fb0;'
                f'text-decoration:none;font-weight:600">Google Maps 导航 →</a>'
                f'</div>'

                f'<div style="background:var(--surface);border:1px solid #dfe8f1;'
                f'border-top:2px solid var(--sage);border-radius:10px;padding:12px 14px">'
                f'<div style="font-family:var(--mono);font-size:10px;letter-spacing:1.4px;color:#8a9cb2;'
                f'text-transform:uppercase;margin-bottom:6px">Parking · 停车方案</div>'
                f'<div style="font-size:12px;color:#213447;line-height:1.62">{spot["parking"]}</div>'
                f'</div>'

                f'<div style="background:var(--surface);border:1px solid #dfe8f1;'
                f'border-top:2px solid var(--amber);border-radius:10px;padding:12px 14px">'
                f'{_fuel_html(spot, compact=True)}'
                f'</div>'

                f'</div>',
                unsafe_allow_html=True,
            )

        with tab4:
            if not log_entries:
                st.markdown(
                    '<div style="padding:16px 0;text-align:center;color:var(--muted);font-size:13px">'
                    '还没有人在这里记录过渔获</div>',
                    unsafe_allow_html=True,
                )
            else:
                for le in log_entries[:8]:
                    fish_pills = "".join(
                        f'<span style="background:#e7f3ec;color:#3a7f5d;border-radius:999px;'
                        f'padding:1px 8px;font-size:11px;margin:0 3px 3px 0;display:inline-block">{f}</span>'
                        for f in le["fish_caught"]
                    ) if le["fish_caught"] else '<span style="font-size:11px;color:#aaa">未记录鱼种</span>'
                    author = le["author"] or "匿名钓友"
                    notes_preview = le["notes"][:60] + "…" if len(le["notes"]) > 60 else le["notes"]
                    _entry_photos = le.get("photos") or []
                    _has_photos = bool(_entry_photos)
                    _photo_badge = (
                        f'<span style="font-size:10px;color:#5a82b4;background:#eaf1fb;'
                        f'border-radius:999px;padding:1px 7px;margin-left:4px">📸 {len(_entry_photos)}</span>'
                        if _has_photos else ""
                    )
                    st.markdown(
                        f'<div style="background:var(--surface);border:1px solid var(--line);'
                        f'border-radius:10px;padding:10px 14px;margin-bottom:{"3px" if _has_photos else "8px"}">'
                        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">'
                        f'<span style="font-family:var(--mono);font-size:11px;color:var(--subtle)">{le["fish_date"]}</span>'
                        f'<span style="font-size:11px;color:var(--muted)">· {author}</span>'
                        f'{_photo_badge}'
                        f'</div>'
                        f'<div style="margin-bottom:4px">{fish_pills}</div>'
                        + (f'<div style="font-size:12px;color:var(--muted);line-height:1.5">{notes_preview}</div>' if notes_preview else '')
                        + '</div>',
                        unsafe_allow_html=True,
                    )
                    if _has_photos:
                        with st.expander(f"📸 查看照片（{len(_entry_photos)} 张）", expanded=False):
                            _cols_n = min(len(_entry_photos), 3)
                            _grid = st.columns(_cols_n if _cols_n > 1 else 2)
                            for _pi, _pb in enumerate(_entry_photos):
                                _b64 = base64.b64encode(_pb).decode()
                                _grid[_pi % _cols_n].markdown(
                                    f'<img src="data:image/jpeg;base64,{_b64}" '
                                    f'style="width:100%;height:auto;'
                                    f'border-radius:8px;border:1px solid #edf3f8;display:block"/>',
                                    unsafe_allow_html=True,
                                )
                        st.markdown('<div style="margin-bottom:8px"></div>', unsafe_allow_html=True)
                if len(log_entries) > 8:
                    st.caption(f"仅显示最近 8 条，共 {len(log_entries)} 条记录")
            if st.button("去渔获日记发一帖 →", key=f"log_goto_{spot['name']}", use_container_width=True):
                st.session_state["_nav_to"] = "📖 渔获日记"
                st.rerun()


# ── 今日决策面板 ──────────────────────────────────────────────────────────

def render_decision_panel(all_spot_data: list, day_w: dict, base_tides: list, label: str) -> None:
    green_list  = [x for x in all_spot_data if x[1]["color"] == "sage"]
    orange_list = [x for x in all_spot_data if x[1]["color"] == "amber"]
    red_list    = [x for x in all_spot_data if x[1]["color"] == "coral"]
    green_n, orange_n, red_n = len(green_list), len(orange_list), len(red_list)
    total = len(all_spot_data) or 1

    if green_n >= total * 0.4:
        v1_band, v1_kicker = "var(--sage)", "✓ GO · OVERALL"
        v1_title, v1_body  = "适合出门", f"{green_n} 个钓点达推荐标准，海况温和"
    elif green_n > 0:
        v1_band, v1_kicker = "var(--amber)", "△ CAUTION · OVERALL"
        v1_title, v1_body  = "优先内湾钓点", f"{green_n} 个内湾钓点可安全前往"
    else:
        v1_band, v1_kicker = "var(--coral)", "⊘ NO-GO · OVERALL"
        v1_title, v1_body  = "暂不建议出行", "浪涌与风速均超安全阈值，建议改期"

    protected_ok = sum(
        1 for s, sa, _, _ in all_spot_data
        if s.get("water_type") not in {"ocean", "boat"} and sa["color"] != "coral"
    )
    exposed_danger = sum(
        1 for s, sa, _, _ in all_spot_data
        if s.get("water_type") in {"ocean", "boat"} and sa["color"] == "coral"
    )
    go_now = green_n
    caution_now = orange_n
    no_go_now = red_n

    swell_val = day_w.get("swell_height") or 0
    wind_val  = day_w.get("wind") or 0
    ocean_danger = swell_val > OCEAN_SWELL_DANGER or wind_val > OCEAN_WIND_DANGER
    if ocean_danger and protected_ok > 0:
        action_text = "优先走内湾/咸淡水，尽量避开高暴露海域。"
    elif ocean_danger:
        action_text = "今日整体风险高，建议改期或仅短时试钓。"
    elif green_n > 0:
        action_text = "按黄金时段出发，优先执行下方推荐点和钓法。"
    else:
        action_text = "暂无稳妥窗口，建议切换到明天/后天查看。"

    sorted_tides = sorted(base_tides, key=lambda t: t["time"])
    highs        = [t for t in sorted_tides if t["is_high"]]
    daytime_highs = [t for t in highs if 6 <= t["time"].hour < 19]
    best_high = (daytime_highs or highs or [None])[0]
    if best_high:
        h = best_high["time"]
        h_start = max(h - timedelta(hours=1.5), h.replace(hour=6, minute=0))
        h_end   = min(h + timedelta(hours=1.5), h.replace(hour=19, minute=0))
        v3_win = f"{h_start.strftime('%H:%M')}–{h_end.strftime('%H:%M')}"
        v3_sub = f"满潮 {h.strftime('%H:%M')} 前后 1.5h"
    else:
        v3_win, v3_sub = "—", "参考各钓点潮汐"

    fw_n  = sum(1 for s, _, _, _ in all_spot_data if s.get("water_type") == "freshwater")
    sea_n = len(all_spot_data) - fw_n
    month = datetime.now().month

    if green_n > 0 and fw_n > 0 and sea_n == 0:
        season_tip = "旺季（4–10月）Bass 活跃度高" if 4 <= month <= 10 else "淡季优先早晚窗口"
        v4_text = f"{action_text} {season_tip}。"
    else:
        v4_text = action_text

    def _verdict_card(band_color, kicker_text, big, body, big_style=""):
        return f"""
        <div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;
                    border-left:4px solid {band_color};padding:18px 18px 16px;height:100%;
                    box-shadow:0 2px 6px rgba(15,30,50,0.025)">
            <div style="font-family:var(--mono);font-size:10.5px;letter-spacing:1.5px;
                        color:var(--subtle);text-transform:uppercase;margin-bottom:8px">{kicker_text}</div>
            <div style="font-family:var(--serif-zh);font-size:20px;font-weight:600;
                        color:var(--text);line-height:1.25;margin-bottom:6px;{big_style}">{big}</div>
            <div style="font-size:12.5px;color:var(--muted);line-height:1.5">{body}</div>
        </div>"""

    def _window_card(band_color, kicker_text, big, body):
        return f"""
        <div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;
                    border-left:4px solid {band_color};padding:18px 18px 16px;height:100%;
                    box-shadow:0 2px 6px rgba(15,30,50,0.025)">
            <div style="font-family:var(--mono);font-size:10.5px;letter-spacing:1.5px;
                        color:var(--subtle);text-transform:uppercase;margin-bottom:8px">{kicker_text}</div>
            <div style="font-family:var(--serif-en);font-size:22px;font-weight:600;
                        color:var(--primary);line-height:1.2;margin-bottom:6px">{big}</div>
            <div style="font-size:12.5px;color:var(--muted);line-height:1.5">{body}</div>
        </div>"""

    def _metric_card() -> str:
        return f"""
        <div style="background:var(--surface);border:1px solid var(--line);border-radius:14px;
                    border-left:4px solid var(--primary);padding:18px 18px 16px;height:100%;
                    box-shadow:0 2px 6px rgba(15,30,50,0.025)">
            <div style="font-family:var(--mono);font-size:10.5px;letter-spacing:1.5px;
                        color:var(--subtle);text-transform:uppercase;margin-bottom:8px">RISK · SNAPSHOT</div>
            <div style="font-size:12.5px;color:var(--text);line-height:1.65">
                ✅ 可直接出发 <b>{go_now}</b> 点<br>
                ⚠️ 谨慎可去 <b>{caution_now}</b> 点<br>
                ❌ 暂不建议 <b>{no_go_now}</b> 点<br>
                🌊 外海/船钓高风险 <b>{exposed_danger}</b> 点
            </div>
        </div>"""

    def _plan_card() -> str:
        return f"""
        <div style="background:var(--surface2);border:1px solid var(--line);border-radius:14px;
                    padding:18px 18px 16px;height:100%;box-shadow:0 2px 6px rgba(15,30,50,0.025)">
            <div style="font-family:var(--mono);font-size:10.5px;letter-spacing:1.5px;
                        color:var(--subtle);text-transform:uppercase;margin-bottom:8px">PLAN · ACTION</div>
            <div style="font-family:var(--serif-zh);font-size:17px;font-weight:600;color:var(--text);
                        line-height:1.35;margin-bottom:6px">执行要点</div>
            <div style="font-size:12.5px;color:var(--muted);line-height:1.55">{v4_text}</div>
        </div>"""

    v1, v2, v3, v4 = st.columns([1.2, 1, 1, 1.3])
    v1.markdown(_verdict_card(v1_band, v1_kicker, v1_title, v1_body), unsafe_allow_html=True)
    v2.markdown(_metric_card(), unsafe_allow_html=True)
    v3.markdown(_window_card("var(--primary)", "WINDOW · BEST TIME", v3_win, v3_sub), unsafe_allow_html=True)
    v4.markdown(_plan_card(), unsafe_allow_html=True)


# ── 加油站 HTML 片段 ──────────────────────────────────────────────────────

def _fuel_html(spot: dict, compact: bool = False) -> str:
    n_lat, n_lon = _nav_coords(spot)
    stations = get_nearby_fuel(n_lat, n_lon)
    fuelcheck_url = "https://www.fuelcheck.nsw.gov.au/app"
    link = (
        f'<a href="{fuelcheck_url}" target="_blank" '
        f'style="display:inline-flex;align-items:center;gap:4px;margin-top:8px;background:#fff3e0;color:#e65100;'
        f'padding:3px 10px;border-radius:8px;font-size:0.74em;font-weight:600;'
        f'text-decoration:none">更多油价 →</a>'
    )
    if stations:
        rows = ""
        row_style = (
            'display:flex;justify-content:space-between;gap:10px;align-items:flex-start;'
            'padding:8px 10px;margin-bottom:8px;background:#f8fafc;'
            'border:1px solid #edf3f8;border-radius:10px;font-size:0.77em'
            if compact
            else
            'display:flex;justify-content:space-between;gap:10px;align-items:flex-start;'
            'padding:6px 0;border-bottom:1px solid #f3f4f6;font-size:0.77em'
        )
        for s in stations:
            price_str = f"{s['price']:.1f}¢/L" if s["price"] else "—"
            rows += (
                f'<div style="{row_style}">'
                f'<div style="min-width:0">'
                f'<div style="font-weight:600;color:#233244;line-height:1.25">{s["brand"] or s["name"]}</div>'
                f'<div style="color:#8a9cb2;margin-top:2px">{s["dist_km"]} km</div>'
                f'</div>'
                f'<div style="text-align:right;flex-shrink:0">'
                f'<span style="display:inline-block;background:#fff3e0;color:#e65100;padding:1px 6px;'
                f'border-radius:6px;font-size:0.9em;font-weight:700;margin-bottom:3px">{s["fuel_type"]}</span>'
                f'<div style="color:#1f8f53;font-weight:800;line-height:1.1">{price_str}</div>'
                f'</div>'
                f'</div>'
            )
        top_margin = "margin-top:0" if compact else "margin-top:6px"
        title_margin = "margin-bottom:4px" if compact else "margin-bottom:5px"
        return (
            f'<div style="{top_margin}">'
            f'<div style="font-family:var(--mono);font-size:10px;letter-spacing:1.5px;'
            f'color:var(--subtle);text-transform:uppercase;{title_margin}">'
            f'FUEL · 附近加油站（实时）</div>'
            f'{rows}'
            f'{link}'
            f'</div>'
        )
    gas = spot.get("nearby_gas", "")
    return (
        f'<div style="{ "margin-top:0" if compact else "margin-top:6px" };'
        f'font-size:0.77em;color:#555;line-height:1.5">'
        + (f'⛽ {gas}<br>' if gas else "")
        + link
        + '</div>'
    )


# ── 钓点详情（地图右侧内联）────────────────────────────────────────────────

def _render_map_spot_detail(spot: dict, safety: dict, tides: list, weather: dict) -> None:
    c_map = {
        "sage":  ("#4f9b76", "#e7f3ec", "#3a7f5d"),
        "amber": ("#d99540", "#fcf2e0", "#a06c20"),
        "coral": ("#cc5e54", "#fbeae8", "#b1453b"),
    }
    border, badge_bg, badge_txt = c_map.get(safety["color"], c_map["sage"])
    wt    = spot.get("water_type", "harbour")
    is_fw = wt == "freshwater"
    swell = weather.get("swell_height") or 0
    wind  = weather.get("wind") or 0
    sw_color = "#8a9cb2" if is_fw else _val_color(swell, SHELTERED_SWELL_WARN, OCEAN_SWELL_DANGER)
    wi_color = _val_color(wind, SHELTERED_WIND_WARN, OCEAN_WIND_DANGER)
    time_win = _best_window_times(spot["best_window"], tides)
    family_stars = spot["family_friendly"].count("⭐")

    # ── 头部：名称 + 安全标签 + 地区
    advice_block = (
        f'<div style="margin-top:8px;padding:6px 10px;background:{badge_bg};'
        f'border-radius:6px;font-size:11.5px;color:{badge_txt};line-height:1.5">'
        f'{safety["advice"]}</div>'
    ) if safety["color"] != "sage" else ""

    st.markdown(
        f'<div style="border-left:4px solid {border};padding:12px 14px;margin-bottom:4px;'
        f'background:var(--surface);border-radius:0 12px 12px 0;'
        f'border:1px solid var(--line);border-left:4px solid {border}">'
        f'<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">'
        f'<div style="font-family:var(--serif-zh);font-size:1.08em;font-weight:700;'
        f'color:#102338;line-height:1.35">{spot["name"]}</div>'
        f'<span style="background:{badge_bg};color:{badge_txt};border:1px solid {border};'
        f'padding:2px 10px;border-radius:999px;font-size:0.75em;font-weight:700;white-space:nowrap">'
        f'{safety["status"]}</span>'
        f'</div>'
        f'<div style="font-size:11.5px;color:var(--muted);margin-top:4px">'
        f'{spot["region"]} · {spot["type"]}</div>'
        f'{advice_block}'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── 三个 Tab
    t1, t2, t3 = st.tabs(["📊 概况", "🎣 钓法", "🗺️ 导航"])

    _TAB_H = 'height:300px;overflow-y:auto;padding-right:2px'

    with t1:
        swell_val = "淡水" if is_fw else f"{swell}m"
        swell_tone = "text" if is_fw else ("coral" if sw_color == "#cc5e54" else "amber" if sw_color == "#d99540" else "text")
        wind_tone  = "coral" if wi_color == "#cc5e54" else "amber" if wi_color == "#d99540" else "text"
        if is_fw:
            tides_md = '<div style="font-size:12px;color:var(--muted);padding:8px 0">🏞️ 淡水钓点 · 无潮汐影响</div>'
        else:
            tide_rows = "".join(
                f'<div style="display:flex;justify-content:space-between;padding:5px 0;'
                f'border-bottom:1px solid var(--line)">'
                f'<span style="font-size:12px;color:var(--muted)">{"满潮" if t["is_high"] else "干潮"}'
                f'{(" · " + format(t["height_m"], ".2f") + "m") if isinstance(t.get("height_m"), (int, float)) else ""}</span>'
                f'<span style="font-family:var(--mono);font-size:12px;font-weight:600;'
                f'color:{"#c69230" if t["is_high"] else "#8a9cb2"}">'
                f'{t["time"].strftime("%H:%M")}</span></div>'
                for t in tides
            )
            warn = '<div style="font-size:10.5px;color:var(--amber);margin-top:6px">⚠ 咸淡水·潮时仅供参考</div>' if wt == "brackish" else ""
            tides_md = f'<div style="margin-bottom:4px">{tide_rows}{warn}</div>'
        fish_chips = _limited_pills(spot["fish_tags"], "blue", limit=5)
        st.markdown(
            f'<div style="{_TAB_H}">'
            f'<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:12px">'
            f'{_mini_stat("浪涌" if not is_fw else "水域", swell_val, swell_tone)}'
            f'{_mini_stat("风速", f"{wind}km/h", wind_tone)}'
            f'{_mini_stat("黄金时段", time_win, "blue")}'
            f'{_mini_stat("家庭", "⭐"*family_stars if family_stars else "—", "text")}'
            f'</div>'
            f'<div style="font-family:var(--mono);font-size:10px;letter-spacing:1.4px;'
            f'color:var(--subtle);text-transform:uppercase;margin-bottom:6px">Tide · 潮汐</div>'
            + tides_md +
            f'<div style="font-family:var(--mono);font-size:10px;letter-spacing:1.4px;'
            f'color:var(--subtle);text-transform:uppercase;margin:12px 0 6px">Fish · 目标鱼种</div>'
            + fish_chips +
            f'</div>',
            unsafe_allow_html=True,
        )

    with t2:
        fish_rules_cook_html = _fish_rules_cook_html(spot["fish_tags"], max_items=4)
        tip_icons = {"🎯": "#2a5fb0", "👍": "#4f9b76", "⚠": "#d99540"}
        method_rows = ""
        for method in spot["supported_methods"][:4]:
            tip = spot["method_tips"].get(method, "此钓法在该钓点完全可行。")
            icon_color = next((c for k, c in tip_icons.items() if k in tip), "#8a9cb2")
            short = method.split("(")[0].strip()
            tip_short = tip[:90] + "…" if len(tip) > 90 else tip
            method_rows += (
                f'<div style="padding:7px 0 8px;border-bottom:1px dashed #e6edf4">'
                f'<div style="display:flex;align-items:center;gap:7px;margin-bottom:3px">'
                f'<div style="width:6px;height:6px;border-radius:50%;background:{icon_color};flex-shrink:0"></div>'
                f'<div style="font-size:12.5px;font-weight:700;color:#1f3147">{short}</div>'
                f'</div>'
                f'<div style="font-size:11.5px;color:#5c6f84;line-height:1.55;padding-left:13px">{tip_short}</div>'
                f'</div>'
            )
        st.markdown(
            f'<div style="{_TAB_H}">'
            f'<div style="margin-bottom:12px">{method_rows}</div>'
            f'<div style="font-family:var(--mono);font-size:10px;letter-spacing:1.4px;'
            f'color:var(--subtle);text-transform:uppercase;margin-bottom:6px">Rules & Cook · 法规</div>'
            f'<div style="display:grid;grid-template-columns:minmax(0,0.9fr) minmax(90px,0.85fr) minmax(0,1.25fr);'
            f'gap:6px;padding:0 0 4px;border-bottom:1px solid var(--line);margin-bottom:2px">'
            f'<span style="font-size:10px;color:#7f95ab">鱼种</span>'
            f'<span style="font-size:10px;color:#7f95ab">法定尺寸</span>'
            f'<span style="font-size:10px;color:#7f95ab">推荐做法</span>'
            f'</div>'
            + fish_rules_cook_html +
            f'</div>',
            unsafe_allow_html=True,
        )

    with t3:
        nav_lat, nav_lon = _nav_coords(spot)
        maps_url = f"https://www.google.com/maps?q={nav_lat},{nav_lon}"
        st.markdown(
            f'<div style="{_TAB_H};display:flex;flex-direction:column;gap:10px">'

            f'<div style="background:var(--surface);border:1px solid var(--line);'
            f'border-top:2px solid var(--primary);border-radius:10px;padding:12px 14px">'
            f'<div style="font-family:var(--mono);font-size:10px;letter-spacing:1.4px;'
            f'color:var(--subtle);text-transform:uppercase;margin-bottom:6px">Route · 自驾路线</div>'
            f'<div style="font-size:12px;color:var(--text);line-height:1.62">{spot["route"]}</div>'
            f'<a href="{maps_url}" target="_blank" '
            f'style="display:inline-block;margin-top:10px;font-size:12px;color:#2a5fb0;'
            f'text-decoration:none;font-weight:600;background:#e6efff;padding:5px 14px;'
            f'border-radius:8px">📍 Google Maps 导航</a>'
            f'</div>'

            f'<div style="background:var(--surface);border:1px solid var(--line);'
            f'border-top:2px solid var(--sage);border-radius:10px;padding:12px 14px">'
            f'<div style="font-family:var(--mono);font-size:10px;letter-spacing:1.4px;'
            f'color:var(--subtle);text-transform:uppercase;margin-bottom:6px">Parking · 停车</div>'
            f'<div style="font-size:12px;color:var(--text);line-height:1.62">{spot["parking"]}</div>'
            f'</div>'

            f'<div style="background:var(--surface);border:1px solid var(--line);'
            f'border-top:2px solid var(--amber);border-radius:10px;padding:12px 14px">'
            f'{_fuel_html(spot, compact=True)}'
            f'</div>'

            f'</div>',
            unsafe_allow_html=True,
        )


def render_spot_card_mobile(
    spot: dict,
    safety: dict,
    spot_tides: list,
    spot_weather: dict,
    day_offset: int,
    forecast_days: list = None,
) -> None:
    border_map = {"sage": "#4f9b76", "amber": "#d99540", "coral": "#cc5e54"}
    border = border_map.get(safety["color"], "#4f9b76")
    is_fw = spot.get("water_type") == "freshwater"
    swell = spot_weather.get("swell_height") or 0
    wind = spot_weather.get("wind") or 0
    time_window = _best_window_times(spot["best_window"], spot_tides)
    wt_badge = _wt_pill(spot)
    fish_chips = _limited_pills(spot["fish_tags"], "blue", limit=4)

    st.markdown(
        f'<div style="background:#fff;border:1px solid rgba(219,231,242,0.85);border-left:4px solid {border};'
        f'border-radius:12px;padding:12px 12px 10px;margin-bottom:8px">'
        f'<div style="display:flex;align-items:center;justify-content:space-between;gap:8px">'
        f'<div style="font-family:var(--serif-zh);font-size:16px;font-weight:600;line-height:1.3">{spot["name"]}</div>'
        f'{_status_badge(safety)}</div>'
        f'<div style="margin-top:6px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">{_reputation_badge(safety)}{wt_badge}</div>'
        f'<div style="margin-top:6px;overflow-x:auto;white-space:nowrap;padding-bottom:2px;-webkit-overflow-scrolling:touch">{fish_chips}</div>'
        f'<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-top:8px">'
        f'{_mini_stat("浪涌" if not is_fw else "水域", ("淡水" if is_fw else f"{swell}m"), "text")}'
        f'{_mini_stat("风速", f"{wind}km/h", "text")}'
        f'{_mini_stat("时段", time_window, "blue")}'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )



# ── 地图 + 点击详情 ───────────────────────────────────────────────────────

def render_map_section(day_offset: int, all_spot_data: list, is_mobile: bool = False,
                       top_pick_names=None) -> None:
    STATUS_COLOR = {"sage": "#4f9b76", "amber": "#d99540", "coral": "#cc5e54"}
    status_counts = {
        "sage": sum(1 for _, safety, _, _ in all_spot_data if safety["color"] == "sage"),
        "amber": sum(1 for _, safety, _, _ in all_spot_data if safety["color"] == "amber"),
        "coral": sum(1 for _, safety, _, _ in all_spot_data if safety["color"] == "coral"),
    }
    marker_limit = MAP_MARKER_LIMIT_MOBILE if is_mobile else MAP_MARKER_LIMIT
    map_spot_data = all_spot_data[:marker_limit]
    _top_picks = top_pick_names or set()

    col_map, col_detail = st.columns([3, 2])
    selected_key = f"map_selected_spot_{day_offset}"
    selected_name = st.session_state.get(selected_key, "")

    with col_map:
        m = folium.Map(
            location=[-33.86, 151.22], zoom_start=11,
            tiles="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
            attr="© OpenStreetMap · CARTO",
        )

        # Render regular spots first so top-pick markers sit on top
        render_order = (
            [(s, sa, ti, w) for s, sa, ti, w in map_spot_data if s["name"] not in _top_picks] +
            [(s, sa, ti, w) for s, sa, ti, w in map_spot_data if s["name"] in _top_picks]
        )

        for spot, safety, _, _ in render_order:
            status_col = STATUS_COLOR.get(safety["color"], "#8a9cb2")
            emoji = _WT_EMOJI.get(spot.get("water_type", "harbour"), "•")
            map_lat, map_lon = _map_coords(spot)
            is_selected = spot["name"] == selected_name
            is_top = spot["name"] in _top_picks

            if is_top:
                # Gold-filled teardrop pin: 32px pin head + pointed tail, blue ★ badge top-right
                # Wrapper gives 6px bleed on all sides for badge overflow + halo
                pad = 6
                pin = 32        # pin head diameter
                tail = 10       # tail height below circle
                wrap_w = pin + pad * 2          # 44
                wrap_h = pin + tail + pad * 2   # 54
                cx = wrap_w // 2                # 22 — horizontal center
                cy = pad + pin // 2             # 6+16=22 — center of pin head
                star_sz = 18

                icon_html = (
                    # CSS: expanding halo ring + selected bright ring
                    '<style>'
                    '@keyframes ph{0%{transform:scale(1);opacity:.55}70%{transform:scale(2);opacity:0}100%{transform:scale(2);opacity:0}}'
                    '.ph{animation:ph 2.2s ease-out infinite;transform-origin:50% 50%;pointer-events:none}'
                    '</style>'
                    f'<div style="position:relative;width:{wrap_w}px;height:{wrap_h}px;">'

                    # Pulsing halo — positioned at pin-head center
                    f'<div class="ph" style="position:absolute;'
                    f'width:{pin}px;height:{pin}px;border-radius:50%;'
                    f'background:rgba(245,166,35,0.45);'
                    f'top:{cy - pin//2}px;left:{cx - pin//2}px;"></div>'

                    # Teardrop pin — circle + triangular tail via CSS clip or border trick
                    # Using: rounded square rotated -45° for teardrop effect
                    f'<div style="position:absolute;top:{pad}px;left:{cx - pin//2}px;'
                    f'width:{pin}px;height:{pin + tail}px;">'

                    # Pin head: gold circle
                    f'<div style="position:absolute;top:0;left:0;'
                    f'width:{pin}px;height:{pin}px;border-radius:50%;'
                    f'background:radial-gradient(circle at 38% 32%,#ffe680 0%,#f5a623 52%,#c97b0a 100%);'
                    f'border:2.5px solid rgba(255,255,255,0.75);'
                    f'box-shadow:0 4px 12px rgba(180,95,0,0.45),inset 0 1px 2px rgba(255,255,255,0.5);'
                    f'display:flex;align-items:center;justify-content:center;'
                    f'font-size:15px;line-height:1;cursor:pointer;">{emoji}</div>'

                    # Tail: small triangle centered below circle
                    f'<div style="position:absolute;top:{pin - 2}px;left:{pin//2 - 6}px;'
                    f'width:0;height:0;'
                    f'border-left:6px solid transparent;'
                    f'border-right:6px solid transparent;'
                    f'border-top:{tail + 2}px solid #d4810a;"></div>'
                    # Tail highlight (lighter triangle slightly offset)
                    f'<div style="position:absolute;top:{pin - 2}px;left:{pin//2 - 4}px;'
                    f'width:0;height:0;'
                    f'border-left:4px solid transparent;'
                    f'border-right:4px solid transparent;'
                    f'border-top:{tail}px solid #f5a623;"></div>'
                    f'</div>'

                    # Blue ★ badge — sits at top-right, white border keeps it distinct
                    f'<div style="position:absolute;top:0;right:0;'
                    f'background:#1d4ed8;color:#fff;border-radius:50%;'
                    f'width:{star_sz}px;height:{star_sz}px;font-size:11px;font-weight:700;'
                    f'display:flex;align-items:center;justify-content:center;'
                    f'border:2.5px solid #fff;box-shadow:0 2px 7px rgba(0,0,0,0.32);'
                    f'line-height:1;z-index:10;">★</div>'
                    f'</div>'
                )
                # Anchor = tip of the tail
                icon = folium.DivIcon(
                    html=icon_html,
                    icon_size=(wrap_w, wrap_h),
                    icon_anchor=(cx, pad + pin + tail),
                )
            else:
                size = 21 if is_selected else 18
                font_size = 10 if is_selected else 9
                ring = (
                    f"0 0 0 2px rgba(255,224,130,0.8),0 0 0 4px {status_col}50,0 2px 5px rgba(0,0,0,0.28)"
                    if is_selected else
                    f"0 0 0 2px {status_col},0 1px 4px rgba(0,0,0,0.20)"
                )
                icon = folium.DivIcon(
                    html=(
                        f'<div style="background:#ffffff;border:2px solid {status_col};border-radius:50%;'
                        f'width:{size}px;height:{size}px;box-sizing:border-box;'
                        f'display:flex;align-items:center;justify-content:center;'
                        f'font-size:{font_size}px;line-height:1;box-shadow:{ring};cursor:pointer;">'
                        f'{emoji}</div>'
                    ),
                    icon_size=(size, size),
                    icon_anchor=(size // 2, size // 2),
                )
            folium.Marker(
                location=[map_lat, map_lon],
                icon=icon,
                tooltip=spot["name"],
                popup=spot["name"],
            ).add_to(m)

        pick_legend_row = (
            '<div style="display:flex;align-items:center;gap:5px;margin-top:3px;padding-top:3px;'
            'border-top:1px solid rgba(15,30,50,0.08)">'
            '<span style="background:#1d4ed8;color:#fff;border-radius:50%;width:13px;height:13px;'
            'font-size:9px;display:inline-flex;align-items:center;justify-content:center;'
            'font-weight:700;flex-shrink:0;border:1.5px solid #fff;'
            'box-shadow:0 1px 3px rgba(0,0,0,0.2);">★</span>'
            f'<span style="font-size:10.5px;color:#1e3a8a">精选推荐 Top {len(_top_picks)}</span>'
            '</div>'
        ) if _top_picks else ""

        legend_html = f"""
        <div style="position:absolute;top:12px;right:12px;z-index:9999;
                    background:rgba(255,255,255,0.94);border:1px solid rgba(15,30,50,0.10);
                    border-radius:10px;padding:9px 12px;
                    box-shadow:0 2px 8px rgba(0,0,0,0.12);font-size:11px;line-height:1.8">
          <div style="font-family:IBM Plex Mono,monospace;font-size:9.5px;letter-spacing:1.2px;
                      color:#8a9cb2;text-transform:uppercase;margin-bottom:2px">MAP STATUS</div>
          <div style="display:flex;gap:10px;align-items:center">
            <span><span style="background:#4f9b76;width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:4px"></span>推荐 {status_counts["sage"]}</span>
            <span><span style="background:#d99540;width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:4px"></span>谨慎 {status_counts["amber"]}</span>
            <span><span style="background:#cc5e54;width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:4px"></span>危险 {status_counts["coral"]}</span>
          </div>
          {pick_legend_row}
          <div style="color:#5b6e87;font-size:10.5px;border-top:1px solid rgba(15,30,50,0.08);
                      margin-top:4px;padding-top:3px">🌊 外海 · ⚓ 内湾 · 🔀 咸淡水 · 🏞️ 淡水</div>
        </div>"""
        m.get_root().html.add_child(folium.Element(legend_html))

        map_data = st_folium(
            m, use_container_width=True, height=430,
            returned_objects=["last_object_clicked"],
            key=f"map_{day_offset}",
        )
        if len(all_spot_data) > marker_limit:
            st.caption(f"地图已显示前 {marker_limit} 个钓点以提升速度（列表仍显示全部）。")

    with col_detail:
        clicked = (map_data or {}).get("last_object_clicked")
        spot = safety = tides = weather = None

        if clicked:
            clat, clng = clicked["lat"], clicked["lng"]
            spot, safety, tides, weather = min(
                map_spot_data,
                key=lambda x: abs(_map_coords(x[0])[0] - clat) + abs(_map_coords(x[0])[1] - clng),
            )
            st.session_state[selected_key] = spot["name"]
            selected_name = spot["name"]
        elif selected_name:
            for s, sa, ti, we in map_spot_data:
                if s["name"] == selected_name:
                    spot, safety, tides, weather = s, sa, ti, we
                    break

        if spot is None:
            n_go   = status_counts["sage"]
            n_warn = status_counts["amber"]
            n_stop = status_counts["coral"]
            st.markdown(
                f'<div style="height:430px;display:flex;flex-direction:column;align-items:center;'
                f'justify-content:center;background:linear-gradient(160deg,#f4f8ff 0%,#eef4fb 100%);'
                f'border:1px solid #dce8f5;border-radius:14px;'
                f'box-shadow:0 2px 12px rgba(15,30,50,0.06);padding:28px 24px;text-align:center">'

                f'<div style="width:88px;height:88px;border-radius:50%;'
                f'background:linear-gradient(135deg,#e8f0fe,#dbeafe);'
                f'border:1px solid #c3d9f5;display:flex;align-items:center;'
                f'justify-content:center;margin-bottom:20px;font-size:40px;line-height:1">🎣</div>'

                f'<div style="font-family:var(--serif-zh);font-size:1.1em;font-weight:700;'
                f'color:#1a334f;margin-bottom:6px">点击地图标记查看详情</div>'
                f'<div style="font-size:12px;color:var(--muted);line-height:1.6;margin-bottom:24px">'
                f'绿色 = 今日推荐 &nbsp;·&nbsp; 橙色 = 谨慎前往 &nbsp;·&nbsp; 红色 = 建议回避</div>'

                f'<div style="display:flex;gap:12px;justify-content:center">'
                f'<div style="background:#e7f3ec;border:1px solid #b2dfcc;border-radius:12px;'
                f'padding:10px 16px;min-width:70px">'
                f'<div style="font-family:var(--serif-en);font-size:28px;font-weight:400;'
                f'color:#3a7f5d;line-height:1">{n_go}</div>'
                f'<div style="font-size:11px;color:#4f9b76;margin-top:3px;font-weight:500">推荐</div></div>'

                f'<div style="background:#fcf2e0;border:1px solid #f0d090;border-radius:12px;'
                f'padding:10px 16px;min-width:70px">'
                f'<div style="font-family:var(--serif-en);font-size:28px;font-weight:400;'
                f'color:#a06c20;line-height:1">{n_warn}</div>'
                f'<div style="font-size:11px;color:#d99540;margin-top:3px;font-weight:500">谨慎</div></div>'

                f'<div style="background:#fbeae8;border:1px solid #f5c0bb;border-radius:12px;'
                f'padding:10px 16px;min-width:70px">'
                f'<div style="font-family:var(--serif-en);font-size:28px;font-weight:400;'
                f'color:#b1453b;line-height:1">{n_stop}</div>'
                f'<div style="font-size:11px;color:#cc5e54;margin-top:3px;font-weight:500">危险</div></div>'
                f'</div>'

                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            _render_map_spot_detail(spot, safety, tides, weather)


# ── 日期 Tab 渲染 ─────────────────────────────────────────────────────────

def render_day_tab(day_offset: int, overview_weather: dict) -> None:
    _t0 = time.perf_counter()
    perf = {}
    target_date = _now_sydney() + timedelta(days=day_offset)
    label = "今天" if day_offset == 0 else ("明天" if day_offset == 1 else "后天")

    day_w      = overview_weather["days"][day_offset]
    next_day_w = overview_weather["days"][day_offset + 1] if day_offset < 2 else None
    base_tides = get_tides_for_date(target_date)

    # ── 今日一句话摘要 ─────────────────────────────────────────────────────
    _w, _s, _r = (day_w.get("wind") or 0), (day_w.get("swell_height") or 0), (day_w.get("rain_prob") or 0)
    _ocean_bad    = _s > OCEAN_SWELL_DANGER or _w > OCEAN_WIND_DANGER
    _shelter_bad  = _s > SHELTERED_SWELL_WARN or _w > SHELTERED_WIND_WARN
    if _ocean_bad and _shelter_bad:
        _s_emoji, _s_col, _s_bg, _s_bd = "⛔", "#b1453b", "#fbeae8", "#cc5e54"
        _s_msg = f"{label}整体风浪偏大，建议推迟出钓或仅选淡水钓点"
    elif _ocean_bad:
        _s_emoji, _s_col, _s_bg, _s_bd = "⚠️", "#a06c20", "#fcf2e0", "#d99540"
        _s_msg = f"{label}外海/船钓危险，建议优先内湾或淡水钓点"
    elif _r >= 70:
        _s_emoji, _s_col, _s_bg, _s_bd = "🌧️", "#3a5fa8", "#e8f0fe", "#7baee8"
        _s_msg = f"{label}降雨为主，海况尚可，备好雨具仍可出钓"
    else:
        _s_emoji, _s_col, _s_bg, _s_bd = "✅", "#3a7f5d", "#e7f3ec", "#4f9b76"
        _s_msg = f"{label}适合出钓，内湾与外海条件均良好"
    st.markdown(
        f'<div style="background:{_s_bg};border:1px solid {_s_bd};border-radius:12px;'
        f'padding:10px 18px;display:flex;align-items:center;gap:12px;margin-bottom:16px">'
        f'<span style="font-size:18px">{_s_emoji}</span>'
        f'<div style="font-size:13px;color:{_s_col};font-weight:600">{_s_msg}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    section_head("OVERALL CONDITIONS · SYDNEY", "悉尼整体海况", "Open-Meteo · 缓存 1 小时")
    left_col, right_col = st.columns([1.6, 1])
    with left_col:
        render_weather_panel(day_w, overview_weather["success"], next_day=next_day_w)
    with right_col:
        st.markdown('<div class="tide-col-marker"></div>', unsafe_allow_html=True)
        st.markdown(
            '<div style="font-family:var(--mono);font-size:10.5px;color:var(--subtle);'
            'letter-spacing:1.5px;text-transform:uppercase;margin-bottom:12px">'
            'TIDES · FORT DENISON</div>',
            unsafe_allow_html=True,
        )
        st.caption(get_tide_source_label(base_tides))
        if is_estimated_tide(base_tides):
            st.warning("今日潮汐为天文估算，仅供参考；出行前请核对官方潮汐。")
        render_tide_panel(base_tides, chart_key=f"tide_{day_offset}", target_date=target_date)
    perf["overview"] = time.perf_counter() - _t0

    st.markdown('<div style="height:24px"></div>', unsafe_allow_html=True)

    is_mobile = _is_mobile_user_agent()
    _tp = time.perf_counter()
    with st.spinner("正在评估钓点海况…"):
        payload = _build_day_payload(
            day_offset,
            target_date.strftime("%Y-%m-%d"),
            tuple(selected_methods),
            tuple(selected_fish),
            selected_region,
            water_type,
            family_only,
            FAST_SPOT_LIMIT if is_mobile else DESKTOP_SPOT_LIMIT,
        )
    filtered = payload["filtered"]
    forecast_by_spot = payload["forecast_by_spot"]
    all_spot_data = list(payload["all_spot_data"])
    try:
        _log_by_spot = _load_log_by_spot()
    except Exception:
        _log_by_spot = {}
    all_spot_data = _apply_reputation_scores(
        all_spot_data,
        _log_by_spot,
        tuple(selected_methods),
        tuple(selected_fish),
    )
    perf["payload"] = time.perf_counter() - _tp

    _safety_order = {"sage": 0, "amber": 1, "coral": 2}
    if sort_by == "家庭友好优先":
        all_spot_data.sort(
            key=lambda x: (
                -x[0]["family_friendly"].count("⭐"),
                _safety_order.get(x[1]["color"], 3),
                -x[1].get("rank_score", 0),
                x[0]["name"],
            )
        )

    if not all_spot_data:
        section_head(f"{label.upper()} · GO / NO-GO", f"{label}出钓决策", "根据实时海况自动生成")
        active_filters = []
        if selected_methods: active_filters.append(f"钓法 ({len(selected_methods)} 种)")
        if selected_fish:    active_filters.append(f"鱼种 ({len(selected_fish)} 种)")
        if selected_region != "全部 · All Sydney": active_filters.append(f"区域「{selected_region}」")
        if water_type != "全部 · All": active_filters.append(f"水域「{water_type}」")
        if safe_only:   active_filters.append("隐藏危险钓点")
        if family_only: active_filters.append("仅家庭友好")
        hint = "、".join(active_filters) if active_filters else "当前条件"
        st.info(f"ℹ️ {hint} 下暂无匹配钓点。建议放宽筛选或点击「↺ 重置所有筛选」。")
        return

    section_head(f"{label.upper()} · GO / NO-GO", f"{label}出钓决策", "根据实时海况自动生成")
    _td = time.perf_counter()
    render_decision_panel(all_spot_data, day_w, base_tides, label)
    perf["decision"] = time.perf_counter() - _td

    # ── 三天最佳出钓日横幅 ──────────────────────────────────────────────────
    day_safe_counts = []
    for _di in range(3):
        _cnt = sum(
            1 for spot in filtered
            if assess_safety(spot, forecast_by_spot[spot["name"]]["days"][_di])["color"] == "sage"
        )
        day_safe_counts.append(_cnt)
    best_di   = day_safe_counts.index(max(day_safe_counts))
    day_names = ["今天", "明天", "后天"]
    if best_di != day_offset:
        diff = day_safe_counts[best_di] - day_safe_counts[day_offset]
        if diff >= 2:
            st.markdown(
                f'<div style="background:#f6f9fc;border:1px solid #dbe6f2;border-radius:10px;'
                f'padding:8px 12px;margin-bottom:8px;font-size:12px;color:#4f647a">'
                f'💡 {day_names[best_di]}更优（+{diff} 推荐点） · 可切换查看</div>',
                unsafe_allow_html=True,
            )

    st.markdown('<div style="height:24px"></div>', unsafe_allow_html=True)

    _exclude_wt = {"freshwater", "boat"}
    top_sage  = [(s, sa, ti, sw) for s, sa, ti, sw in all_spot_data if sa["color"] == "sage"  and s.get("water_type") not in _exclude_wt]
    top_amber = [(s, sa, ti, sw) for s, sa, ti, sw in all_spot_data if sa["color"] == "amber" and s.get("water_type") not in _exclude_wt]
    top_safe  = top_sage or top_amber
    is_amber_fallback = not top_sage and bool(top_amber)
    pick_accent = "海况温和，综合评分最优" if not is_amber_fallback else "无满分点，以下为谨慎可前往钓点"
    section_head(f"TOP PICK · {label.upper()}", f"{label}精选推荐", pick_accent)
    if top_safe:
        top3 = top_safe[:3]
        if len(top3) == 1:
            spot, safety, tides, dw = top3[0]
            render_hero_card(st, spot, safety, dw, tides,
                             forecast_days=forecast_by_spot[spot["name"]]["days"])
        else:
            cols = st.columns(len(top3))
            for col, (spot, safety, tides, dw) in zip(cols, top3):
                render_hero_card(col, spot, safety, dw, tides,
                                 forecast_days=forecast_by_spot[spot["name"]]["days"])
        if is_amber_fallback:
            st.markdown(
                '<div style="background:#fcf2e0;border-radius:10px;padding:10px 16px;'
                'border-left:3px solid var(--amber);font-size:12.5px;color:#7a5010;margin-top:6px">'
                '⚠️ 当日无满分推荐钓点，以上为谨慎可前往的内湾钓点，出行请注意风浪变化。'
                '</div>',
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            '<div style="background:linear-gradient(90deg,#fbf3e1 0%,#fdf8eb 60%,#fff 100%);'
            'border-radius:14px;padding:20px 24px;border:1px solid #f0e3c0;'
            'border-left:4px solid var(--gold)">'
            '<div style="font-family:var(--serif-zh);font-size:17px;font-weight:600;margin-bottom:6px">'
            f'当日海况不佳，暂无推荐钓点</div>'
            '<div style="font-size:13px;color:var(--muted);line-height:1.55">'
            '涌浪或风速超出安全阈值。调整侧边栏筛选条件、改去内湾试试手气。</div>'
            '</div>',
            unsafe_allow_html=True,
        )

    st.markdown('<div style="height:24px"></div>', unsafe_allow_html=True)
    section_head(f"MAP · {len(all_spot_data)} SPOTS", "钓点地图", "点击标记查看详情")
    _tm = time.perf_counter()
    _top_pick_names = {s["name"] for s, _, _, _ in top_safe[:3]} if top_safe else set()
    render_map_section(day_offset, all_spot_data, is_mobile=is_mobile, top_pick_names=_top_pick_names)
    perf["map"] = time.perf_counter() - _tm

    st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
    visible_list = [(s, sa, ti, sw) for s, sa, ti, sw in all_spot_data
                    if not (safe_only and not sa["safe"])]
    sort_label = {"家庭友好优先": "按家庭友好度排序"}.get(sort_by, "按当日海况评分排序")
    section_head(
        f"MATCHED SPOTS · {len(visible_list)} / {len(spots)}",
        "匹配钓点", sort_label
    )
    page_size = 12 if is_mobile else 20
    page_key = f"matched_page_{day_offset}"
    if page_key not in st.session_state:
        st.session_state[page_key] = 1
    render_n = min(len(visible_list), st.session_state[page_key] * page_size)
    visible = 0
    _tl = time.perf_counter()
    for spot, safety, spot_tides, spot_day_w in visible_list[:render_n]:
        if is_mobile:
            render_spot_card_mobile(
                spot,
                safety,
                spot_tides,
                spot_day_w,
                day_offset,
                forecast_days=forecast_by_spot[spot["name"]]["days"],
            )
        else:
            render_spot_card(
                spot,
                safety,
                spot_tides,
                spot_day_w,
                day_offset,
                forecast_days=forecast_by_spot[spot["name"]]["days"],
                log_entries=_log_by_spot.get(spot["name"], []),
            )
        visible += 1
    perf["list"] = time.perf_counter() - _tl
    if render_n < len(visible_list):
        if st.button(f"加载更多钓点（{render_n}/{len(visible_list)}）", key=f"{page_key}_more"):
            st.session_state[page_key] += 1
            st.rerun()
    if visible == 0:
        active_filters = []
        if selected_methods: active_filters.append(f"钓法 ({len(selected_methods)} 种)")
        if selected_fish:    active_filters.append(f"鱼种 ({len(selected_fish)} 种)")
        if selected_region != "全部 · All Sydney": active_filters.append(f"区域「{selected_region}」")
        if water_type != "全部 · All": active_filters.append(f"水域「{water_type}」")
        if safe_only:   active_filters.append("隐藏危险钓点")
        if family_only: active_filters.append("仅家庭友好")
        hint = "、".join(active_filters) if active_filters else "未知条件"
        st.info(f"ℹ️ 当前筛选「{hint}」无匹配钓点，点击「↺ 重置所有筛选」或放宽条件。")



# ── 渔获日记页 ───────────────────────────────────────────────────────────

def render_fishing_log_page() -> None:
    st.markdown(
        '<div style="display:flex;justify-content:space-between;align-items:flex-end;'
        'gap:16px;flex-wrap:wrap;margin-bottom:16px">'
        '<div>'
        '<h2 style="font-family:var(--serif-zh);font-weight:600;font-size:28px;'
        'color:var(--text);margin:0 0 4px">渔获日记</h2>'
        '<div style="font-size:13px;color:var(--muted)">朋友们的出钓记录、鱼种和现场照片</div>'
        '</div>'
        '<div style="font-family:var(--mono);font-size:11px;color:var(--muted);'
        'border:1px solid var(--line);border-radius:999px;padding:5px 10px">Catch log</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    all_spot_names = [s["name"] for s in spots]

    with st.expander("发布渔获", expanded=False):
        with st.form("log_form", clear_on_submit=True):
            col1, col2 = st.columns([1, 1])
            with col1:
                log_date = st.date_input(
                    "出钓日期",
                    value=_now_sydney().date(),
                    format="YYYY-MM-DD",
                )
                log_spot = st.selectbox(
                    "钓点",
                    ["（自定义）"] + all_spot_names,
                    key="log_spot_select",
                )
                if log_spot == "（自定义）":
                    log_spot = st.text_input("自定义钓点名称", placeholder="例：中国花园码头")
            with col2:
                log_author = st.text_input("你的昵称", placeholder="（可空）")
                log_fish = st.multiselect(
                    "钓到的鱼种",
                    ALL_FISH,
                    placeholder="选择鱼种",
                )
            log_notes = st.text_area(
                "渔获记录",
                placeholder="今天的情况、用饵、心得……",
                height=100,
            )
            log_photos = st.file_uploader(
                f"上传照片（最多 {LOG_MAX_PHOTOS} 张，每张 < 3 MB）",
                type=["jpg", "jpeg", "png", "webp"],
                accept_multiple_files=True,
            )
            submitted = st.form_submit_button("发布", type="primary", use_container_width=True)
            if submitted:
                spot_name = log_spot.strip() if log_spot else ""
                if not spot_name:
                    st.error("请填写钓点名称")
                elif len(log_photos or []) > LOG_MAX_PHOTOS:
                    st.error(f"最多只能上传 {LOG_MAX_PHOTOS} 张照片")
                elif any(photo.size > LOG_MAX_PHOTO_BYTES for photo in (log_photos or [])):
                    st.error("单张照片不能超过 3 MB")
                else:
                    photo_bytes = [f.read() for f in (log_photos or [])]
                    fishing_log.add_entry(
                        fish_date=log_date.isoformat(),
                        spot_name=spot_name,
                        author=log_author.strip(),
                        notes=log_notes.strip(),
                        fish_caught=log_fish,
                        photos=photo_bytes,
                    )
                    st.success("已发布！")
                    st.rerun()

    st.markdown("---")
    entries = fishing_log.get_entries()
    if not entries:
        st.markdown(
            '<div style="border:1px dashed var(--line);border-radius:14px;padding:28px 18px;'
            'text-align:center;background:#fbfdff;margin-top:8px">'
            '<div style="font-family:var(--serif-zh);font-size:20px;font-weight:600;'
            'color:var(--text);margin-bottom:6px">还没有渔获记录</div>'
            '<div style="font-size:13px;color:var(--muted)">展开上方「发布渔获」，发第一条照片和心得。</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    session_id = get_or_create_session_id()
    # 批量拉取所有条目的点赞数据，避免 N×2 次查询
    like_stats = fishing_log.get_like_stats_bulk(
        [e["id"] for e in entries], session_id
    )
    for entry in entries:
        fish_date   = entry["fish_date"]
        spot_name   = entry["spot_name"]
        author      = entry["author"] or "匿名钓友"
        notes       = entry["notes"]
        fish_caught = entry["fish_caught"]
        photos      = entry["photos"]
        entry_id    = entry["id"]
        fish_date_html = html.escape(str(fish_date))
        spot_name_html = html.escape(str(spot_name))
        author_html    = html.escape(str(author))
        notes_html     = html.escape(str(notes))

        fish_pills = "".join(
            f'<span style="background:#e7f3ec;color:#3a7f5d;border-radius:999px;'
            f'padding:2px 10px;font-size:12px;margin:0 3px 3px 0;display:inline-block">'
            f'{html.escape(str(f))}</span>'
            for f in fish_caught
        ) if fish_caught else '<span style="color:#aaa;font-size:12px">未记录鱼种</span>'

        like_count, has_liked = like_stats.get(entry_id, (0, False))

        with st.container(border=True):
            # ── 标题行：钓点/日期/作者 左，点赞按钮 右 ──────────────
            hcol, lcol = st.columns([6, 1])
            with hcol:
                st.markdown(
                    f'<div style="font-size:17px;font-weight:700;color:var(--text);line-height:1.25">📍 {spot_name_html}</div>'
                    f'<div style="font-family:var(--mono);font-size:11.5px;color:#8a9cb2;margin-top:4px">'
                    f'{fish_date_html} · {author_html}</div>',
                    unsafe_allow_html=True,
                )
            with lcol:
                _btn_label = f"❤️ {like_count}" if has_liked else f"🤍 {like_count}"
                _btn_help  = "取消点赞" if has_liked else "点赞"
                if st.button(_btn_label, key=f"log_like_{entry_id}",
                             use_container_width=True, help=_btn_help):
                    try:
                        _new_count, _is_like = fishing_log.toggle_log_like(entry_id, session_id)
                        st.toast("❤️ 已点赞！" if _is_like else "已取消点赞")
                    except Exception:
                        st.toast("⚠️ 操作失败，请重试")
                    st.rerun()

            # ── 鱼种标签 ────────────────────────────────────────────
            st.markdown(
                f'<div style="margin:8px 0 6px">{fish_pills}</div>',
                unsafe_allow_html=True,
            )

            # ── 文字记录 ────────────────────────────────────────────
            if notes:
                st.markdown(
                    f'<div style="font-size:13.5px;color:#3a4a5c;white-space:pre-wrap;'
                    f'line-height:1.65;margin-bottom:6px">{notes_html}</div>',
                    unsafe_allow_html=True,
                )

            # ── 照片 ────────────────────────────────────────────────
            if photos:
                n = len(photos)
                cols_n = min(n, 3)
                grid_cols = st.columns(cols_n if cols_n > 1 else 2)
                for idx, photo_bytes in enumerate(photos):
                    b64 = base64.b64encode(photo_bytes).decode()
                    grid_cols[idx % cols_n].markdown(
                        f'<img src="data:image/jpeg;base64,{b64}" '
                        f'style="width:100%;height:auto;'
                        f'border-radius:8px;border:1px solid #edf3f8;display:block;margin-bottom:8px"/>',
                        unsafe_allow_html=True,
                    )

            # ── 管理 ────────────────────────────────────────────────
            with st.expander("管理", expanded=False):
                if st.button(f"确认删除（{fish_date} · {spot_name}）", key=f"del_{entry_id}", type="secondary"):
                    fishing_log.delete_entry(entry_id)
                    st.rerun()


# ── 主页面 ────────────────────────────────────────────────────────────────

if selected_page == "🎣 钓点推荐":
    today = _now_sydney()
    date_str = f"{today.year} 年 {today.month} 月 {today.day} 日"
    weekdays = ["周一","周二","周三","周四","周五","周六","周日"]
    weekday  = weekdays[today.weekday()]
    today_obj = _now_sydney()
    _day_overview = get_marine_forecast(-33.8688, 151.2093)
    hero_spots = [spot for spot in spots if spot_matches(spot)][:HERO_SPOT_LIMIT]
    hero_safe = 0
    hero_ocean_danger = 0
    _overview_today_weather = _day_overview["days"][0]
    for _spot in hero_spots:
        _safety = assess_safety(_spot, _overview_today_weather)
        if _safety["color"] == "sage":
            hero_safe += 1
        if _spot.get("water_type") in {"ocean", "boat"} and _safety["color"] == "coral":
            hero_ocean_danger += 1

    st.markdown(
        f'<div class="hero" style="position:relative;border-radius:14px;overflow:hidden;'
        f'padding:28px 32px;background:linear-gradient(110deg,#1a3f7a 0%,#2a5fb0 50%,#3479c9 100%);'
        f'color:#fff;margin-bottom:16px;display:flex;align-items:center;gap:40px;flex-wrap:wrap">'
        f'<div style="min-width:260px;flex:0 1 400px">'
        f'<div style="font-family:IBM Plex Mono;font-size:11px;letter-spacing:2px;'
        f'color:rgba(255,255,255,0.7);text-transform:uppercase;margin-bottom:8px">'
        f'实时海况 · 潮汐推算 · 智能推荐</div>'
        f'<h1 class="brand-q" style="font-size:38px;font-weight:400;'
        f'color:#fff;margin:0 0 6px;display:flex;align-items:center;gap:10px">上鱼啦'
        f'<span style="font-family:var(--mono);font-size:12px;font-weight:500;letter-spacing:1.4px;'
        f'opacity:0.82;margin-left:2px">BETA</span>'
        + (
            f'<span class="brand-mascot-wrap">'
            f'<img src="{_MASCOT_DATA_URL}" class="brand-mascot" alt="mascot"/>'
            f'<span class="brand-bubble b1"></span>'
            f'<span class="brand-bubble b2"></span>'
            f'</span>'
            if _MASCOT_DATA_URL else ''
        )
        + '</h1>'
        f'<div style="font-size:14px;color:rgba(255,255,255,0.8)">'
        f'{date_str} · {weekday} · 悉尼</div>'
        f'</div>'
        f'<div class="hero-stat-row" style="display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end">'
        f'{_hero_stat_tile(str(len(hero_spots)), "匹配钓点", "Spots indexed", hint="当前筛选后纳入评估的钓点数量。")}'
        f'{_hero_stat_tile(str(hero_safe), "今日推荐", "Recommend now", "gold", hint="根据当天风浪阈値评估为“推荐”的钓点数量。")}'
        f'{_hero_stat_tile(f"{hero_ocean_danger} 个", "高风险点", "High-risk spots", hint="外海或船钓点中，今日安全评估为危险的数量。")}'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    def _day_weather_icon(i: int) -> str:
        w = _day_overview["days"][i]
        if (w.get("rain_prob") or 0) >= 60: return "🌧"
        if (w.get("wind") or 0) > OCEAN_WIND_DANGER or (w.get("swell_height") or 0) > OCEAN_SWELL_DANGER: return "⚠️"
        return "☀️"

    _day_names = ["今天", "明天", "后天"]
    selected_day_offset = st.segmented_control(
        "选择日期",
        options=[0, 1, 2],
        format_func=lambda i: f"{_day_weather_icon(i)} {_day_names[i]} {(today_obj + timedelta(days=i)).strftime('%m/%d')}",
        label_visibility="collapsed",
        default=0,
        width="stretch",
    ) or 0
    render_day_tab(selected_day_offset, _day_overview)

    st.markdown("---")
    st.markdown("""
<div style="text-align:center;color:#8a9cb2;font-size:0.82em;padding:4px 0 16px">
    🚨 <b>安全提示</b>：外海矶钓请务必穿戴救生衣和防滑钉鞋，结伴同行，浪况不对立刻撤退。<br>
    天气数据来自
    <a href="https://open-meteo.com/" target="_blank" style="color:#2a5fb0">Open-Meteo</a>，
    潮汐为天文估算，出行前请参考
    <a href="http://www.bom.gov.au/nsw/forecasts/sydney.shtml" target="_blank"
       style="color:#2a5fb0">BOM 官方预报</a>。
</div>
""", unsafe_allow_html=True)

    render_stats_panel()

else:
    render_fishing_log_page()

# 追踪访问结束
record_visit_end()
