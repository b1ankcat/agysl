#!/usr/bin/env python3
"""Zero-dependency custom statusLine renderer for Antigravity CLI (agy)."""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from datetime import datetime, timedelta
from typing import Any

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

# 4 个区域各自使用高对比度色彩，避开黑 / 紫 / 绿等常见终端背景色
CTX_COLOR = "\033[96m"           # 亮青色
USAGE_COLOR = "\033[93m"         # 亮琥珀黄
CACHE_COLOR = "\033[38;5;208m"   # 亮橙色
DAY_COLOR = "\033[38;5;75m"      # 亮天蓝
SEP_COLOR = "\033[2m\033[97m"    # 柔白分隔线
TOKEN_COLOR = "\033[97m"         # 亮白（可选扩展字段）

DEFAULT_BAR_WIDTH = 8

# =============================================================================
# 集中配置字典：新增模型 / 配额池 / 场景规则只需维护下面 3 个字典
# =============================================================================

# 配额池家族映射（顺序决定优先级：先匹配第三方模型关键字，再回退到 Gemini）
QUOTA_FAMILIES: dict[str, dict[str, Any]] = {
    "claude": {
        "quota_prefixes": ("claude", "anthropic", "3p"),
        "fallback_keywords": ("claude", "anthropic", "sonnet", "opus", "haiku"),
        "fallback_tokens": (),
    },
    "openai": {
        "quota_prefixes": ("gpt", "openai", "claude", "3p"),
        "fallback_keywords": ("gpt", "openai", "codex"),
        "fallback_tokens": ("o1", "o3", "o4"),
    },
    "gemini": {
        "quota_prefixes": ("gemini",),
        "fallback_keywords": ("gemini",),
        "fallback_tokens": (),
    },
}

# 单一数据源：每个模型的匹配规则、所属配额家族、特殊 bucket 变体及公开定价（USD / 1M tokens）
# 以后新增模型只需在此字典追加一条记录。
MODEL_CONFIGS: dict[str, dict[str, Any]] = {
    # --- Gemini 4 ---
    "gemini-4-neon": {
        "match": ("gemini-4-neon",),
        "tokens": ("neon",),
        "family": "gemini",
        "price": {"input": 3.00, "cache": 0.15, "output": 15.00},
    },
    "gemini-4-hydrogen": {
        "match": ("gemini-4-hydrogen",),
        "tokens": ("hydrogen",),
        "family": "gemini",
        "price": {"input": 1.50, "cache": 0.075, "output": 7.50},
    },
    "gemini-4-carbon": {
        "match": ("gemini-4-carbon",),
        "tokens": ("carbon",),
        "family": "gemini",
        "price": {"input": 2.00, "cache": 0.10, "output": 10.00},
    },
    "gemini-4-argon": {
        "match": ("gemini-4-argon",),
        "tokens": ("argon",),
        "family": "gemini",
        "quota_variants": ("argon",),
        "price": {"input": 2.00, "cache": 0.10, "output": 10.00},
    },
    # --- Gemini 3 ---
    "gemini-3.8-flash": {
        "match": ("gemini-3.8-flash",),
        "family": "gemini",
        "price": {"input": 0.75, "cache": 0.075, "output": 3.75},
    },
    "gemini-3.7-flash": {
        "match": ("gemini-3.7-flash",),
        "family": "gemini",
        "price": {"input": 0.75, "cache": 0.075, "output": 3.75},
    },
    "gemini-3.6-flash": {
        "match": ("gemini-3.6-flash",),
        "family": "gemini",
        "price": {"input": 0.75, "cache": 0.075, "output": 3.75},
    },
    "gemini-3.5-flash-lite": {
        "match": ("gemini-3.5-flash-lite",),
        "family": "gemini",
        "price": {"input": 0.30, "cache": 0.03, "output": 2.50},
    },
    "gemini-3.1-flash-lite": {
        "match": ("gemini-3.1-flash-lite",),
        "family": "gemini",
        "price": {"input": 0.25, "cache": 0.025, "output": 1.50},
    },
    "gemini-3.1-pro": {
        "match": ("gemini-3.1-pro",),
        "family": "gemini",
        "price": {"input": 2.00, "cache": 0.20, "output": 12.00},
    },
    "gemini-3-flash": {
        "match": ("gemini-3-flash", "gemini-flash"),
        "tokens": ("flash",),
        "family": "gemini",
        "price": {"input": 0.75, "cache": 0.075, "output": 3.75},
    },
    # --- Anthropic Claude (3P) ---
    "claude-opus-5": {
        "match": ("claude-opus-5", "opus-5"),
        "family": "claude",
        "price": {"input": 4.00, "cache": 0.40, "output": 20.00},
    },
    "claude-opus-4": {
        "match": ("claude-opus-4", "claude-4-opus", "opus-4", "claude-opus"),
        "tokens": ("opus",),
        "family": "claude",
        "price": {"input": 15.00, "cache": 1.50, "output": 75.00},
    },
    "claude-sonnet-5": {
        "match": ("claude-sonnet-5", "sonnet-5"),
        "family": "claude",
        "price": {"input": 2.00, "cache": 0.20, "output": 10.00},
    },
    "claude-sonnet-4": {
        "match": ("claude-sonnet-4", "claude-4-sonnet", "sonnet-4", "claude-sonnet"),
        "tokens": ("sonnet",),
        "family": "claude",
        "price": {"input": 3.00, "cache": 0.30, "output": 15.00},
    },
    "claude-haiku-5": {
        "match": ("claude-haiku-5", "haiku-5"),
        "family": "claude",
        "price": {"input": 0.10, "cache": 0.01, "output": 0.50},
    },
    "claude-haiku-4": {
        "match": ("claude-haiku-4", "claude-4-haiku", "haiku-4", "claude-haiku"),
        "tokens": ("haiku",),
        "family": "claude",
        "price": {"input": 1.00, "cache": 0.10, "output": 5.00},
    },
    # --- OpenAI / GPT (3P) ---
    "gpt-5": {
        "match": ("gpt-5",),
        "family": "openai",
        "price": {"input": 1.25, "cache": 0.125, "output": 10.00},
    },
    "gpt-oss": {
        "match": ("gpt-oss",),
        "family": "openai",
        "price": {"input": 0.15, "cache": 0.015, "output": 0.60},
    },
    "gpt-4o": {
        "match": ("gpt-4o",),
        "family": "openai",
        "price": {"input": 2.50, "cache": 1.25, "output": 10.00},
    },
    "openai-o3": {
        "match": ("o3-pro", "o3-mini"),
        "tokens": ("o3",),
        "family": "openai",
        "price": {"input": 2.00, "cache": 0.50, "output": 8.00},
    },
    "openai-o4": {
        "match": ("o4-mini",),
        "tokens": ("o4",),
        "family": "openai",
        "price": {"input": 1.10, "cache": 0.275, "output": 4.40},
    },
    "openai-o1": {
        "match": ("o1-pro", "o1-mini"),
        "tokens": ("o1",),
        "family": "openai",
        "price": {"input": 15.00, "cache": 7.50, "output": 60.00},
    },
    "openai-codex": {
        "match": ("codex",),
        "family": "openai",
        "price": {"input": 1.50, "cache": 0.15, "output": 6.00},
    },
    # --- Generic Gemini Pro fallback (placed after 3P so "pro" never shadows o3-pro) ---
    "gemini-pro": {
        "match": ("gemini-pro", "gemini-3-pro", "gemini-2.5-pro"),
        "tokens": ("pro",),
        "family": "gemini",
        "price": {"input": 2.00, "cache": 0.20, "output": 12.00},
    },
}

# 场景规则配置（Orchestrator / 配额时间窗 / transcript 目录搜索）
SCENARIO_CONFIGS: dict[str, dict[str, Any]] = {
    "orchestrator": {
        "match": ("orchestrator",),
        "settings_key": "orchestrator",
        "role_priority": ("planner", "executor"),
        "settings_env": "AGY_SETTINGS_FILE",
        "default_settings_path": "~/.gemini/antigravity-cli/settings.json",
    },
    "quota_windows": {
        "short_tokens": (
            "5h", "5_h", "5-h", "5hr", "5_hr", "5-hr",
            "5hour", "5_hour", "5-hour", "five_hour", "five-hour", "fivehour", "short",
        ),
        "weekly_tokens": ("week", "weekly", "7d", "7_d", "7-d"),
        "ignored_extra_tokens": ("wtu", "limit", "quota"),
        "short_max_seconds": 6 * 3600,
    },
    "transcript": {
        "base_dirs": ("antigravity-cli", "antigravity", "antigravity-ide"),
    },
}

# 兼容导出：默认定价与价格表均由 MODEL_CONFIGS 派生
DEFAULT_MODEL_KEY = "gemini-4-hydrogen"
DEFAULT_MODEL_PRICE: dict[str, float] = MODEL_CONFIGS[DEFAULT_MODEL_KEY]["price"]
MODEL_PRICES: tuple[tuple[str, dict[str, float]], ...] = tuple(
    (k, cfg["price"]) for k, cfg in MODEL_CONFIGS.items() if "price" in cfg
)


def _normalize_model_name(model_name: str) -> str:
    return re.sub(r"[\s_]+", "-", (model_name or "").strip().lower())


def _tokenize_model_name(model_name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (model_name or "").strip().lower()) if t}


def resolve_model_config(model_name: str) -> dict[str, Any] | None:
    """Resolve a model entry from MODEL_CONFIGS via two-pass matching (substrings first, then tokens)."""
    norm = _normalize_model_name(model_name)
    if not norm:
        return None
    for cfg in MODEL_CONFIGS.values():
        if any(pat in norm for pat in cfg.get("match", ())):
            return cfg
    tokens = _tokenize_model_name(model_name)
    if tokens:
        for cfg in MODEL_CONFIGS.values():
            if tokens & set(cfg.get("tokens", ())):
                return cfg
    return None


def resolve_model_price(model_name: str) -> dict[str, float]:
    cfg = resolve_model_config(model_name)
    if cfg and isinstance(cfg.get("price"), dict):
        return cfg["price"]
    return DEFAULT_MODEL_PRICE


def calc_cost_usd(in_tok: int, cache_tok: int, out_tok: int, model_name: str = "") -> float:
    p = resolve_model_price(model_name)
    return (in_tok * p["input"] + cache_tok * p["cache"] + out_tok * p["output"]) / 1_000_000.0


def env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def clamp_pct(val: float) -> float:
    return max(0.0, min(100.0, val))


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def fmt_daily_tokens(n: int) -> str:
    """Format daily token count in millions ('m'), switching to billions ('b') at 1e9."""
    if n >= 1_000_000_000:
        val = n / 1_000_000_000.0
        if val >= 100:
            return f"{val:.0f}b"
        if val >= 10:
            return f"{val:.1f}b"
        return f"{val:.2f}b"
    val = n / 1_000_000.0
    if val >= 100:
        return f"{val:.0f}m"
    if val >= 10:
        return f"{val:.1f}m"
    return f"{val:.2f}m"


def colorize(text: str, color: str, use_color: bool) -> str:
    if not use_color or not color:
        return text
    return f"{color}{text}{RESET}"


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def visible_width(text: str) -> int:
    plain = _ANSI_RE.sub("", text)
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in plain)


def resolve_terminal_width(payload: dict[str, Any]) -> int:
    for key in ("terminal_width", "width"):
        val = payload.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool) and int(val) > 0:
            return int(val)
    for env_key in ("AGYSL_WIDTH", "COLUMNS"):
        raw = os.environ.get(env_key)
        if raw is not None and raw.strip():
            try:
                parsed = int(raw.strip())
                if parsed >= 0:
                    return parsed
            except ValueError:
                pass
    cols = shutil.get_terminal_size(fallback=(0, 0)).columns
    return cols if cols > 0 else 0


def join_evenly(segments: list[str], term_width: int, use_color: bool) -> str:
    if not segments:
        return ""
    if len(segments) == 1:
        return segments[0]

    gaps = len(segments) - 1
    seg_widths = [visible_width(s) for s in segments]
    min_total = sum(seg_widths) + 3 * gaps

    if term_width <= 0 or term_width == min_total:
        return colorize(" │ ", SEP_COLOR, use_color).join(segments)

    if term_width < min_total:
        compact_total = sum(seg_widths) + gaps
        if term_width >= compact_total:
            extra = term_width - compact_total
            g_base, g_rem = divmod(extra, gaps)
            out = [segments[0]]
            for i in range(gaps):
                total_sp = g_base + (1 if i < g_rem else 0)
                left_sp = total_sp // 2
                right_sp = total_sp - left_sp
                sep = f"{' ' * left_sp}{colorize('│', SEP_COLOR, use_color)}{' ' * right_sp}"
                out.append(sep + segments[i + 1])
            return "".join(out)
        return colorize("│", SEP_COLOR, use_color).join(segments)

    extra = term_width - min_total
    g_base, g_rem = divmod(extra, gaps)
    out = [segments[0]]
    for i in range(gaps):
        total_sp = 2 + g_base + (1 if i < g_rem else 0)
        left_sp = total_sp // 2
        right_sp = total_sp - left_sp
        sep = f"{' ' * left_sp}{colorize('│', SEP_COLOR, use_color)}{' ' * right_sp}"
        out.append(sep + segments[i + 1])
    return "".join(out)


def render_bar(
    pct: float | None,
    width: int,
    use_color: bool,
    color: str,
    fill_char: str = "█",
    empty_char: str = "░",
) -> str:
    if pct is None:
        empty = empty_char * width
        return colorize(empty, f"{DIM}{color}", use_color)
    clamped = clamp_pct(pct)
    filled = int(round(clamped / 100.0 * width))
    filled = max(0, min(width, filled))
    bar_fill = fill_char * filled
    bar_empty = empty_char * (width - filled)
    if not use_color:
        return bar_fill + bar_empty
    return f"{color}{bar_fill}{RESET}{DIM}{color}{bar_empty}{RESET}"


def extract_model_name(payload: dict[str, Any], fallback_model: str = "") -> str:
    model_obj = payload.get("model")
    raw_name = ""
    if isinstance(model_obj, dict):
        raw_name = str(model_obj.get("display_name") or model_obj.get("id") or "").strip()
    elif isinstance(model_obj, str):
        raw_name = model_obj.strip()
    return raw_name or fallback_model or ""


def find_transcript(payload: dict[str, Any]) -> str | None:
    for key in ("transcript_path", "transcriptPath"):
        p = payload.get(key)
        if isinstance(p, str) and p and os.path.isfile(p):
            return p

    cid = (
        payload.get("conversation_id")
        or payload.get("conversationId")
        or payload.get("session_id")
    )
    if isinstance(cid, str) and cid:
        base_dirs = SCENARIO_CONFIGS.get("transcript", {}).get("base_dirs", ("antigravity-cli",))
        for base in base_dirs:
            p = os.path.expanduser(
                f"~/.gemini/{base}/brain/{cid}/.system_generated/logs/transcript.jsonl"
            )
            if os.path.isfile(p):
                return p
        return None

    # Only fall back to the most recent session when invoked without a live payload (e.g. empty stdin preview)
    if payload:
        return None

    files = glob.glob(
        os.path.expanduser(
            "~/.gemini/antigravity-cli/brain/*/.system_generated/logs/transcript.jsonl"
        )
    )
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def read_latest_planner_from_transcript(path: str | None) -> dict[str, Any]:
    """Tail-read the last 64 KiB of transcript.jsonl to extract latest PLANNER_RESPONSE."""
    result: dict[str, Any] = {"model": "", "input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0, "found": False}
    if not path or not os.path.isfile(path):
        return result
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > 65536:
                f.seek(size - 65536)
                f.readline()  # discard partial first line
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "PLANNER_RESPONSE":
                result["model"] = str(obj.get("model") or "")
                result["input_tokens"] = int(obj.get("input_tokens") or 0)
                result["cache_read_tokens"] = int(obj.get("cache_read_tokens") or 0)
                result["output_tokens"] = int(obj.get("output_tokens") or 0)
                result["found"] = True
                return result
    except OSError:
        pass
    return result


def read_totals_from_transcript(path: str | None) -> dict[str, int]:
    totals = {"in": 0, "cache": 0, "out": 0}
    if not path or not os.path.isfile(path):
        return totals
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "PLANNER_RESPONSE":
                    continue
                totals["in"] += int(obj.get("input_tokens") or 0)
                totals["cache"] += int(obj.get("cache_read_tokens") or 0)
                totals["out"] += int(obj.get("output_tokens") or 0)
    except OSError:
        pass
    return totals


def parse_iso_to_local(ts: str) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.astimezone()
    return dt.astimezone()


def _sum_file_usage_for_range(path: str, start_dt: datetime, end_dt: datetime) -> tuple[int, float]:
    total_tokens = 0
    total_cost = 0.0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "PLANNER_RESPONSE":
                    continue
                dt = parse_iso_to_local(obj.get("created_at") or "")
                if dt is None or not (start_dt <= dt < end_dt):
                    continue
                in_tok = int(obj.get("input_tokens") or 0)
                cache_tok = int(obj.get("cache_read_tokens") or 0)
                out_tok = int(obj.get("output_tokens") or 0)
                total_tokens += in_tok + cache_tok + out_tok
                total_cost += calc_cost_usd(in_tok, cache_tok, out_tok, str(obj.get("model") or ""))
    except OSError:
        pass
    return total_tokens, total_cost


def compute_daily_usage(
    extra_transcript: str | None = None,
    now: datetime | None = None,
    cache_path: str | None = None,
) -> tuple[int, float]:
    """Sum daily tokens and USD cost across all sessions for the local calendar day."""
    local_now = now.astimezone() if now is not None else datetime.now().astimezone()
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    today_key = today_start.strftime("%Y-%m-%d")
    today_start_ts = today_start.timestamp()

    pattern = os.environ.get(
        "AGYSL_BRAIN_GLOB",
        os.path.expanduser(
            "~/.gemini/antigravity-cli/brain/*/.system_generated/logs/transcript.jsonl"
        ),
    )
    paths = set(glob.glob(pattern))
    if extra_transcript and os.path.isfile(extra_transcript):
        paths.add(extra_transcript)

    if cache_path is None:
        uid = getattr(os, "getuid", lambda: 0)()
        cache_path = os.path.join(tempfile.gettempdir(), f"agysl_daily_{uid}.json")

    cache_entries: dict[str, dict[str, Any]] = {}
    if cache_path and os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as cf:
                loaded = json.load(cf)
            if isinstance(loaded, dict) and loaded.get("date") == today_key:
                raw_entries = loaded.get("files")
                if isinstance(raw_entries, dict):
                    cache_entries = raw_entries
        except (OSError, json.JSONDecodeError):
            cache_entries = {}

    new_entries: dict[str, dict[str, Any]] = {}
    daily_tokens = 0
    daily_cost = 0.0
    cache_dirty = False

    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        if st.st_mtime < today_start_ts:
            continue
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        size = st.st_size
        cached = cache_entries.get(p)
        if (
            isinstance(cached, dict)
            and cached.get("mtime_ns") == mtime_ns
            and cached.get("size") == size
            and isinstance(cached.get("tokens"), int)
            and isinstance(cached.get("cost_usd"), (int, float))
            and not isinstance(cached.get("cost_usd"), bool)
        ):
            file_tokens = cached["tokens"]
            file_cost = float(cached["cost_usd"])
        else:
            file_tokens, file_cost = _sum_file_usage_for_range(p, today_start, tomorrow_start)
            cache_dirty = True
        new_entries[p] = {
            "mtime_ns": mtime_ns,
            "size": size,
            "tokens": file_tokens,
            "cost_usd": file_cost,
        }
        daily_tokens += file_tokens
        daily_cost += file_cost

    if cache_path and (cache_dirty or len(new_entries) != len(cache_entries)):
        try:
            with open(cache_path, "w", encoding="utf-8") as cf:
                json.dump({"date": today_key, "files": new_entries}, cf)
        except OSError:
            pass

    return daily_tokens, daily_cost


def compute_daily_tokens(
    extra_transcript: str | None = None,
    now: datetime | None = None,
    cache_path: str | None = None,
) -> int:
    return compute_daily_usage(extra_transcript=extra_transcript, now=now, cache_path=cache_path)[0]


def compute_context_pct(payload: dict[str, Any], latest_transcript: dict[str, Any]) -> tuple[float | None, int, int]:
    cw = payload.get("context_window") or {}
    ctx_size = int(cw.get("context_window_size") or 0)
    used_pct = cw.get("used_percentage")

    cu = cw.get("current_usage") if isinstance(cw.get("current_usage"), dict) else {}
    cu_in = int(cu.get("input_tokens") or 0)
    cu_cache = int(cu.get("cache_read_input_tokens") or cu.get("cache_read_tokens") or 0)
    cu_create = int(cu.get("cache_creation_input_tokens") or 0)
    current_tokens = cu_in + cu_cache + cu_create

    if current_tokens == 0 and latest_transcript.get("found"):
        current_tokens = latest_transcript["input_tokens"] + latest_transcript["cache_read_tokens"]

    if isinstance(used_pct, (int, float)):
        return clamp_pct(float(used_pct)), current_tokens, ctx_size
    if ctx_size > 0 and current_tokens > 0:
        return clamp_pct(current_tokens / ctx_size * 100.0), current_tokens, ctx_size
    return None, current_tokens, ctx_size


def compute_cache_hit_pct(payload: dict[str, Any], latest_transcript: dict[str, Any]) -> float | None:
    cw = payload.get("context_window") or {}
    cu = cw.get("current_usage") if isinstance(cw.get("current_usage"), dict) else {}

    cu_in = int(cu.get("input_tokens") or 0)
    cu_cache = int(cu.get("cache_read_input_tokens") or cu.get("cache_read_tokens") or 0)
    cu_create = int(cu.get("cache_creation_input_tokens") or 0)
    cu_total = cu_in + cu_cache + cu_create

    if cu_total > 0:
        return clamp_pct(cu_cache / cu_total * 100.0)

    if latest_transcript.get("found"):
        t_in = latest_transcript["input_tokens"]
        t_cache = latest_transcript["cache_read_tokens"]
        t_total = t_in + t_cache
        if t_total > 0:
            return clamp_pct(t_cache / t_total * 100.0)

    return None


def bucket_reset_seconds(bucket: dict[str, Any] | None, now: datetime | None = None) -> float | None:
    if not isinstance(bucket, dict):
        return None
    ris = bucket.get("reset_in_seconds")
    if isinstance(ris, (int, float)) and not isinstance(ris, bool) and ris >= 0:
        return float(ris)
    rt = bucket.get("reset_time")
    if isinstance(rt, str) and rt.strip():
        dt = parse_iso_to_local(rt)
        if dt is not None:
            ref_now = now.astimezone() if now is not None else datetime.now().astimezone()
            return max(0.0, (dt - ref_now).total_seconds())
    return None


def fmt_reset_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds <= 0:
        return "0m"
    if seconds < 3600:
        return f"{max(1, round(seconds / 60.0))}m"
    return f"{max(1, round(seconds / 3600.0))}h"


def infer_quota_variant_tokens(model_name: str) -> set[str]:
    tokens = _tokenize_model_name(model_name)
    cfg = resolve_model_config(model_name)
    if cfg:
        tokens.update(cfg.get("quota_variants", ()))
    return tokens


def infer_preferred_quota_prefixes(model_name: str) -> list[str]:
    norm = _normalize_model_name(model_name)
    if not norm:
        return []
    cfg = resolve_model_config(model_name)
    if cfg:
        if cfg.get("quota_prefixes"):
            return list(cfg["quota_prefixes"])
        fam_cfg = QUOTA_FAMILIES.get(cfg.get("family", ""))
        if fam_cfg:
            return list(fam_cfg.get("quota_prefixes", ()))

    tokens = _tokenize_model_name(model_name)
    for fam_cfg in QUOTA_FAMILIES.values():
        if any(kw in norm for kw in fam_cfg.get("fallback_keywords", ())) or bool(
            tokens & set(fam_cfg.get("fallback_tokens", ()))
        ):
            return list(fam_cfg.get("quota_prefixes", ()))
    return []


def read_orchestrator_models_from_settings(settings_path: str | None = None) -> list[str]:
    orch_cfg = SCENARIO_CONFIGS["orchestrator"]
    path = (
        settings_path
        or os.environ.get(orch_cfg["settings_env"])
        or os.path.expanduser(orch_cfg["default_settings_path"])
    )
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    orch = data.get(orch_cfg["settings_key"])
    if not isinstance(orch, dict):
        return []
    models: list[str] = []
    for role in orch_cfg["role_priority"]:
        val = orch.get(role)
        if isinstance(val, str) and val.strip():
            models.append(val.strip())
    return models


def resolve_quota_model_name(payload: dict[str, Any], latest_transcript: dict[str, Any]) -> str:
    raw_candidates: list[str] = []
    model_obj = payload.get("model")
    if isinstance(model_obj, dict):
        for key in ("display_name", "id"):
            val = model_obj.get(key)
            if isinstance(val, str) and val.strip():
                raw_candidates.append(val.strip())
    elif isinstance(model_obj, str) and model_obj.strip():
        raw_candidates.append(model_obj.strip())

    for cand in raw_candidates:
        if infer_preferred_quota_prefixes(cand):
            return cand

    orch_cfg = SCENARIO_CONFIGS["orchestrator"]
    orch_markers = orch_cfg.get("match", ("orchestrator",))
    if any(any(m in _normalize_model_name(cand) for m in orch_markers) for cand in raw_candidates):
        fallback_candidates: list[str] = []
        payload_orch = payload.get(orch_cfg["settings_key"])
        if isinstance(payload_orch, dict):
            for role in orch_cfg["role_priority"]:
                val = payload_orch.get(role)
                if isinstance(val, str) and val.strip():
                    fallback_candidates.append(val.strip())
        fallback_candidates.extend(read_orchestrator_models_from_settings())
        t_model = latest_transcript.get("model", "")
        if isinstance(t_model, str) and t_model.strip():
            fallback_candidates.append(t_model.strip())
        for cand in fallback_candidates:
            if infer_preferred_quota_prefixes(cand):
                return cand

    return extract_model_name(payload, fallback_model=latest_transcript.get("model", ""))


def pick_quota_buckets(
    quota: dict[str, Any],
    model_name: str,
    now: datetime | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(quota, dict) or not quota:
        return None, None

    active_items = [
        (k, v) for k, v in quota.items()
        if isinstance(v, dict) and not v.get("disabled")
    ]
    if not active_items:
        active_items = [(k, v) for k, v in quota.items() if isinstance(v, dict)]
    if not active_items:
        return None, None

    preferred_prefixes = infer_preferred_quota_prefixes(model_name)
    model_tokens = infer_quota_variant_tokens(model_name)
    qw_cfg = SCENARIO_CONFIGS["quota_windows"]
    ignored_extra = set(qw_cfg["ignored_extra_tokens"])

    def find_bucket(window_tokens: tuple[str, ...]) -> dict[str, Any] | None:
        scored: list[tuple[int, int, int, str, dict[str, Any]]] = []
        for k, v in active_items:
            kl = k.lower()
            if not any(w in kl for w in window_tokens):
                continue

            pref_tier = len(preferred_prefixes)
            matched_pref = ""
            for idx, pref in enumerate(preferred_prefixes):
                if pref in kl:
                    pref_tier = idx
                    matched_pref = pref
                    break

            key_tokens = [t for t in re.split(r"[^a-z0-9]+", kl) if t]
            extra_tokens = [
                t for t in key_tokens
                if t != matched_pref
                and t not in preferred_prefixes
                and not any(w in t for w in window_tokens)
                and t not in ignored_extra
            ]
            if extra_tokens and any(t in model_tokens for t in extra_tokens):
                variant_tier = 0
            elif not extra_tokens:
                variant_tier = 1
            else:
                variant_tier = 2

            scored.append((pref_tier, variant_tier, len(kl), kl, v))

        if not scored:
            return None
        scored.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        return scored[0][4]

    b_5h = find_bucket(tuple(qw_cfg["short_tokens"]))
    b_wk = find_bucket(tuple(qw_cfg["weekly_tokens"]))

    # Fallback: classify remaining buckets by reset horizon when key names omit window hints
    if b_5h is None or b_wk is None:
        short_max = float(qw_cfg["short_max_seconds"])

        def pick_by_horizon(is_short: bool, excluded: dict[str, Any] | None) -> dict[str, Any] | None:
            candidates: list[tuple[int, float, dict[str, Any]]] = []
            for k, v in active_items:
                if v is excluded:
                    continue
                secs = bucket_reset_seconds(v, now=now)
                if secs is None:
                    continue
                if is_short and secs <= short_max:
                    pref_rank = 0 if any(p in k.lower() for p in preferred_prefixes) else 1
                    candidates.append((pref_rank, secs, v))
                elif not is_short and secs > short_max:
                    pref_rank = 0 if any(p in k.lower() for p in preferred_prefixes) else 1
                    candidates.append((pref_rank, -secs, v))
            if not candidates:
                return None
            candidates.sort(key=lambda item: (item[0], item[1]))
            return candidates[0][2]

        if b_5h is None:
            b_5h = pick_by_horizon(True, b_wk)
        if b_wk is None:
            b_wk = pick_by_horizon(False, b_5h)

    return b_5h, b_wk


def quota_pct(bucket: dict[str, Any] | None) -> float | None:
    if not isinstance(bucket, dict):
        return None
    rem = bucket.get("remaining_fraction")
    if not isinstance(rem, (int, float)) or isinstance(rem, bool):
        return None
    return clamp_pct(float(rem) * 100.0)


def format_quota_item(bucket: dict[str, Any] | None, pct: float | None, now: datetime | None = None) -> str:
    if pct is None:
        return "--%"
    pct_s = f"{pct:.0f}%"
    reset_s = fmt_reset_duration(bucket_reset_seconds(bucket, now=now))
    if reset_s:
        return f"{pct_s}({reset_s})"
    return pct_s


def format_statusline(payload: dict[str, Any], now: datetime | None = None) -> str:
    use_color = not (env_bool("NO_COLOR", False) or env_bool("AGYSL_NO_COLOR", False))
    bar_width = DEFAULT_BAR_WIDTH
    show_tokens = env_bool("AGYSL_SHOW_TOKENS", False)
    multiline = env_bool("AGYSL_MULTILINE", False)

    transcript_path = find_transcript(payload)
    latest_t = read_latest_planner_from_transcript(transcript_path)

    model_name = extract_model_name(payload, fallback_model=latest_t.get("model", ""))
    quota_model_name = resolve_quota_model_name(payload, latest_t)

    ctx_pct, ctx_tokens, ctx_size = compute_context_pct(payload, latest_t)
    cache_pct = compute_cache_hit_pct(payload, latest_t)

    b_5h, b_wk = pick_quota_buckets(payload.get("quota") or {}, quota_model_name, now=now)
    pct_5h = quota_pct(b_5h)
    pct_wk = quota_pct(b_wk)

    daily_tok, daily_cost = compute_daily_usage(extra_transcript=transcript_path, now=now)
    daily_s = fmt_daily_tokens(daily_tok)
    daily_cost_s = f"{daily_cost:.2f}$"

    extra_segments: list[str] = []
    if show_tokens:
        cw = payload.get("context_window") or {}
        totals = read_totals_from_transcript(transcript_path)
        tot_in = totals["in"] or int(cw.get("total_input_tokens") or 0)
        tot_cache = totals["cache"]
        tot_out = totals["out"] or int(cw.get("total_output_tokens") or 0)
        extra_segments.append(colorize(
            f"tok {fmt_tokens(tot_in)}+{fmt_tokens(tot_cache)}/{fmt_tokens(tot_out)}",
            TOKEN_COLOR,
            use_color,
        ))

    def build_segments(bw: int, show_bars: bool, ctx_detail_mode: str) -> list[str]:
        # 1. Context 区域（亮青色）
        ctx_pct_s = f"{ctx_pct:.0f}%" if ctx_pct is not None else "--%"
        if ctx_detail_mode == "full" and ctx_size > 0 and ctx_tokens > 0:
            ctx_detail = colorize(f" ({fmt_tokens(ctx_tokens)}/{fmt_tokens(ctx_size)})", f"{DIM}{CTX_COLOR}", use_color)
        elif ctx_detail_mode in ("full", "short") and ctx_tokens > 0:
            ctx_detail = colorize(f" ({fmt_tokens(ctx_tokens)})", f"{DIM}{CTX_COLOR}", use_color)
        else:
            ctx_detail = ""
        if show_bars:
            ctx_bar = render_bar(ctx_pct, bw, use_color, color=CTX_COLOR)
            ctx_seg = f"{colorize('ctx', CTX_COLOR, use_color)} {ctx_bar} {colorize(ctx_pct_s, f'{BOLD}{CTX_COLOR}', use_color)}{ctx_detail}"
        else:
            ctx_seg = f"{colorize('ctx', CTX_COLOR, use_color)} {colorize(ctx_pct_s, f'{BOLD}{CTX_COLOR}', use_color)}{ctx_detail}"

        # 2. Usage 5h / week 区域（亮琥珀黄）
        s_5h = format_quota_item(b_5h, pct_5h, now=now)
        s_wk = format_quota_item(b_wk, pct_wk, now=now)
        if show_bars:
            half_w = max(2, bw // 2) if bw < 8 else max(4, bw // 2)
            bar_5h = render_bar(pct_5h, half_w, use_color, color=USAGE_COLOR)
            bar_wk = render_bar(pct_wk, half_w, use_color, color=USAGE_COLOR)
            usage_seg = (
                f"{colorize('usage 5h/wk', USAGE_COLOR, use_color)} "
                f"{bar_5h}{colorize('/', f'{DIM}{USAGE_COLOR}', use_color)}{bar_wk} "
                f"{colorize(f'{s_5h}/{s_wk}', f'{BOLD}{USAGE_COLOR}', use_color)}"
            )
        else:
            usage_seg = f"{colorize('usage 5h/wk', USAGE_COLOR, use_color)} {colorize(f'{s_5h}/{s_wk}', f'{BOLD}{USAGE_COLOR}', use_color)}"

        # 3. Cache hit 区域（亮橙色）
        cache_s = f"{cache_pct:.0f}%" if cache_pct is not None else "--%"
        if show_bars:
            cache_bar = render_bar(cache_pct, bw, use_color, color=CACHE_COLOR)
            cache_seg = f"{colorize('cache', CACHE_COLOR, use_color)} {cache_bar} {colorize(cache_s, f'{BOLD}{CACHE_COLOR}', use_color)}"
        else:
            cache_seg = f"{colorize('cache', CACHE_COLOR, use_color)} {colorize(cache_s, f'{BOLD}{CACHE_COLOR}', use_color)}"

        # 4. 每日 token 统计区域（亮天蓝）
        day_seg = f"{colorize('day token', DAY_COLOR, use_color)} {colorize(f'{daily_s}({daily_cost_s})', f'{BOLD}{DAY_COLOR}', use_color)}"

        return [ctx_seg, usage_seg, cache_seg, day_seg, *extra_segments]

    term_width = resolve_terminal_width(payload)
    sep = colorize(" │ ", SEP_COLOR, use_color)

    if multiline:
        segments = build_segments(bar_width, True, "full")
        if len(segments) > 2:
            line1 = sep.join(segments[:2])
            line2 = sep.join(segments[2:])
            return f"{line1}\n{line2}"
        return sep.join(segments)

    if term_width <= 0:
        return sep.join(build_segments(bar_width, True, "full"))

    candidates: list[tuple[int, bool, str]] = []
    for bw in range(bar_width, 3, -1):
        candidates.append((bw, True, "full"))
    min_bw = min(bar_width, 4)
    candidates.append((min_bw, True, "short"))
    candidates.append((min_bw, True, "none"))
    candidates.append((0, False, "none"))

    chosen = build_segments(0, False, "none")
    for bw, show_bars, ctx_detail_mode in candidates:
        segs = build_segments(bw, show_bars, ctx_detail_mode)
        min_w = sum(visible_width(s) for s in segs) + 3 * (len(segs) - 1)
        if min_w <= term_width:
            chosen = segs
            break

    return join_evenly(chosen, term_width, use_color)


def main() -> int:
    raw = ""
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
        except Exception:
            raw = ""

    payload: dict[str, Any] = {}
    if raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = {}

    print(format_statusline(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
