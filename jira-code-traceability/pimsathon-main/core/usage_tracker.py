"""Token-usage tracking for the Dashboard tab.

Every provider turn records one event (JSON line, one file per day under
``~/.cowork_local/usage/``): when, which tab/task ("source" + "label"),
provider/model, input/output/cached token counts. Real counts come from the
server's ``usage`` block when the stream includes one; otherwise a ~4 chars ≈
1 token estimate keeps the dashboard useful on gateways that never report
usage (events carry ``"estimated": true`` so the UI can say so).

The turn's source/label is set by the caller ON THE WORKER THREAD via
:func:`set_context` (thread-local — concurrent turns don't mix labels).
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import CONFIG_DIR

USAGE_DIR = CONFIG_DIR / "usage"

_local = threading.local()

# Process-global identity (NOT thread-local — who's logged in and which
# machine this is are fixed for the whole process, set once right after
# login in app.py::run(), unlike source/label which vary per worker turn).
_identity_account = ""
_identity_machine = ""
_identity_shared_dir = ""


def set_identity(account: str, machine: str, shared_dir: str = "") -> None:
    """Called once after login succeeds. ``shared_dir``, when reachable,
    makes every subsequent :func:`record` ALSO best-effort-append to the
    shared cross-machine telemetry store (see :mod:`telemetry_shared`)."""
    global _identity_account, _identity_machine, _identity_shared_dir
    _identity_account = account or ""
    _identity_machine = machine or ""
    _identity_shared_dir = shared_dir or ""


def set_context(source: str, label: str = "") -> None:
    """Tag subsequent :func:`record` calls on THIS thread (e.g. ("cowork",
    "chat title") / ("task", "task title"))."""
    _local.source = source
    _local.label = label


# ---- per-thread usage accumulator -----------------------------------------
# A step/run that wants to know its OWN token/cost (not the all-time file total)
# calls begin_accumulation(), reads accumulated() before/after a unit of work,
# and diffs the two. Because record() runs on the same worker thread that drives
# the work (providers are called synchronously inside it), the thread-local
# total is exactly that thread's usage — concurrent flows on other threads
# accumulate independently, with no locking or label collisions. Used by the
# Co4E runner to attach per-step token/cost to each node's output event.
def begin_accumulation() -> None:
    """Start (or reset) this thread's usage accumulator."""
    _local.acc = {"in": 0, "out": 0, "cache": 0, "events": []}


def accumulated() -> Dict[str, Any]:
    """Snapshot of this thread's accumulated usage since :func:`begin_accumulation`
    (all zeros / empty if never started). ``events`` is a per-turn list of
    ``{model, in, out}`` so a caller can price a delta with the per-model table."""
    acc = getattr(_local, "acc", None)
    if acc is None:
        return {"in": 0, "out": 0, "cache": 0, "events": []}
    return {"in": acc["in"], "out": acc["out"], "cache": acc["cache"],
            "events": list(acc["events"])}


def end_accumulation() -> None:
    """Stop accumulating on this thread (subsequent records aren't tallied)."""
    _local.acc = None


def estimate_tokens(text: str) -> int:
    return max(0, len(text or "") // 4)


def record(provider: str, model: str, input_tokens: int, output_tokens: int,
           cached_tokens: int = 0, estimated: bool = False) -> None:
    """Append one usage event. Never raises — usage tracking must never break
    a chat turn."""
    try:
        now = datetime.now()
        event = {
            "ts": now.isoformat(timespec="seconds"),
            "source": getattr(_local, "source", "") or "other",
            "label": getattr(_local, "label", "") or "",
            "provider": provider or "",
            "model": model or "",
            "in": int(input_tokens or 0),
            "out": int(output_tokens or 0),
            "cache": int(cached_tokens or 0),
            "estimated": bool(estimated),
            "account": _identity_account,
            "machine": _identity_machine,
        }
        USAGE_DIR.mkdir(parents=True, exist_ok=True)
        path = USAGE_DIR / f"{now.strftime('%Y-%m-%d')}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        _write_shared(event, now)
        # Feed this thread's live accumulator, if one is active (see above).
        acc = getattr(_local, "acc", None)
        if acc is not None:
            acc["in"] += event["in"]
            acc["out"] += event["out"]
            acc["cache"] += event["cache"]
            acc["events"].append({"model": event["model"], "in": event["in"], "out": event["out"]})
    except Exception:  # noqa: BLE001
        pass


def _write_shared(event: Dict[str, Any], now: datetime) -> None:
    """Best-effort mirror of ``event`` into the shared cross-machine store —
    one file PER MACHINE per day, so no two machines ever write the same
    file (avoids any read-modify-write race). Never raises."""
    if not _identity_shared_dir or not _identity_machine:
        return
    try:
        shared = Path(_identity_shared_dir).expanduser() / "telemetry" / "usage"
        shared.mkdir(parents=True, exist_ok=True)
        path = shared / f"{_identity_machine}-{now.strftime('%Y-%m-%d')}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def load_events(start: Optional[date] = None, end: Optional[date] = None,
                directory: Path = None) -> List[Dict[str, Any]]:
    """Events between ``start`` and ``end`` (inclusive; None = unbounded)."""
    directory = directory or USAGE_DIR
    if not directory.exists():
        return []
    events: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.jsonl")):
        try:
            day = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if (start and day < start) or (end and day > end):
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            continue
    return events


def summarize(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate a list of events into dashboard numbers + habit stats."""
    total_in = sum(e.get("in", 0) for e in events)
    total_out = sum(e.get("out", 0) for e in events)
    total_cache = sum(e.get("cache", 0) for e in events)
    by_label: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    by_hour: Dict[int, int] = {}
    by_day: Dict[str, int] = {}
    for e in events:
        tok = e.get("in", 0) + e.get("out", 0)
        key = e.get("label") or e.get("source") or "?"
        by_label[key] = by_label.get(key, 0) + tok
        by_source[e.get("source", "?")] = by_source.get(e.get("source", "?"), 0) + tok
        try:
            dt = datetime.fromisoformat(e.get("ts", ""))
            by_hour[dt.hour] = by_hour.get(dt.hour, 0) + tok
            by_day[dt.strftime("%Y-%m-%d")] = by_day.get(dt.strftime("%Y-%m-%d"), 0) + tok
        except ValueError:
            pass
    return {
        "turns": len(events),
        "in": total_in, "out": total_out, "cache": total_cache,
        "total": total_in + total_out,
        "avg_per_turn": (total_in + total_out) // len(events) if events else 0,
        "estimated_share": (sum(1 for e in events if e.get("estimated")) / len(events)
                            if events else 0.0),
        "top_labels": sorted(by_label.items(), key=lambda kv: -kv[1])[:5],
        "by_source": sorted(by_source.items(), key=lambda kv: -kv[1]),
        "busiest_hour": max(by_hour.items(), key=lambda kv: kv[1])[0] if by_hour else None,
        "busiest_day": max(by_day.items(), key=lambda kv: kv[1])[0] if by_day else None,
    }


# ---- cost ------------------------------------------------------------------
DEFAULT_PRICING = {
    "price_per_mtok_in_usd": 0.5,     # USD per 1M input tokens (flat fallback rate)
    "price_per_mtok_out_usd": 1.5,    # USD per 1M output tokens
    "price_per_mtok_cache_usd": 0.1,  # USD per 1M cached tokens
    "currency": "USD",                # display currency: USD | VND | JPY
    "usd_to_vnd": 25000.0,
    "usd_to_jpy": 150.0,
    # Per-model price table (USD / 1M tokens): {model: {"in","out","cache"}}.
    # Events whose model has an entry are costed with ITS rates; everything
    # else falls back to the flat price_per_mtok_* rates above. Edited in the
    # Monitoring Overview's pricing table.
    "model_prices": {},
    # Reference URL of the price list the table was filled from (set in
    # Settings; shown as a link beside the table — informational only, the
    # app never scrapes it).
    "pricing_url": "",
}

_CURRENCY_FMT = {"USD": ("$", 4), "VND": ("₫", 0), "JPY": ("¥", 1)}

# Currencies the display picker offers — exactly the ones format_cost() can
# actually convert to (symbol/precision above + a usd_to_* rate below).
SUPPORTED_CURRENCIES = tuple(_CURRENCY_FMT)


def cost_usd(summary: Dict[str, Any], pricing: Dict[str, Any]) -> Dict[str, float]:
    p = {**DEFAULT_PRICING, **(pricing or {})}
    return {
        "in": summary.get("in", 0) / 1e6 * float(p["price_per_mtok_in_usd"]),
        "out": summary.get("out", 0) / 1e6 * float(p["price_per_mtok_out_usd"]),
        "cache": summary.get("cache", 0) / 1e6 * float(p["price_per_mtok_cache_usd"]),
    }


def cost_usd_events(events: List[Dict[str, Any]], pricing: Dict[str, Any]) -> Dict[str, float]:
    """Per-bucket USD cost computed EVENT BY EVENT so the per-model price
    table applies: an event whose ``model`` has an entry in
    ``pricing["model_prices"]`` is costed with that model's own rates; any
    other event uses the flat ``price_per_mtok_*`` rates. With an empty
    table this equals ``cost_usd(summarize(events), pricing)`` exactly."""
    p = {**DEFAULT_PRICING, **(pricing or {})}
    table = p.get("model_prices") or {}
    flat = {"in": float(p["price_per_mtok_in_usd"]),
            "out": float(p["price_per_mtok_out_usd"]),
            "cache": float(p["price_per_mtok_cache_usd"])}
    out = {"in": 0.0, "out": 0.0, "cache": 0.0}
    for e in events:
        rates = table.get(e.get("model", "")) or {}
        for bucket in ("in", "out", "cache"):
            try:
                rate = float(rates.get(bucket, flat[bucket]))
            except (TypeError, ValueError):
                rate = flat[bucket]
            out[bucket] += e.get(bucket, 0) / 1e6 * rate
    return out


def bucketed_series(events: List[Dict[str, Any]], granularity: str = "day",
                    pricing: Dict[str, Any] = None, last: int = None) -> List[tuple]:
    """Group usage events into time buckets → ordered ``[(label, tokens, cost_usd)]``.

    ``granularity``: ``day`` (YYYY-MM-DD) · ``month`` (YYYY-MM) · ``year`` (YYYY).
    ``last`` keeps only the most recent N buckets (for the dashboard chart)."""
    from collections import OrderedDict
    pricing = pricing or {}

    def _key(ts: Any) -> str:
        s = str(ts or "")[:10]
        if granularity == "year":
            return s[:4]
        if granularity == "month":
            return s[:7]
        return s

    buckets: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for e in sorted(events, key=lambda ev: str(ev.get("ts", ""))):
        k = _key(e.get("ts"))
        if k:
            buckets.setdefault(k, []).append(e)
    out = []
    for k, evs in buckets.items():
        tokens = sum(int(e.get("in", 0) or 0) + int(e.get("out", 0) or 0)
                     + int(e.get("cache", 0) or 0) for e in evs)
        cost = sum(cost_usd_events(evs, pricing).values())
        out.append((k, tokens, cost))
    if last and len(out) > last:
        out = out[-last:]
    return out


def period_bounds(gran: str, offset: int, today: Optional[date] = None) -> tuple:
    """[start, end) dates of the period ``offset`` periods from the current one
    (0 = current, -1 = the previous week/month/year). Weeks run Mon→Sun."""
    from datetime import timedelta
    today = today or date.today()
    if gran == "week":
        monday = today - timedelta(days=today.weekday())     # Monday of this week
        start = monday + timedelta(weeks=offset)
        return start, start + timedelta(days=7)
    if gran == "year":
        y = today.year + offset
        return date(y, 1, 1), date(y + 1, 1, 1)
    # month (default)
    base = today.year * 12 + (today.month - 1) + offset
    y, m = divmod(base, 12)
    y2, m2 = divmod(base + 1, 12)
    return date(y, m + 1, 1), date(y2, m2 + 1, 1)


def _period_label(gran: str, start: date) -> str:
    if gran == "week":
        return start.isoformat()          # the week's Monday (YYYY-MM-DD)
    if gran == "year":
        return str(start.year)
    return start.strftime("%Y-%m")


def _sum_between(events: List[Dict[str, Any]], start: date, end: date,
                 pricing: Dict[str, Any]) -> tuple:
    lo, hi = start.isoformat(), end.isoformat()
    evs = [e for e in events if lo <= str(e.get("ts", ""))[:10] < hi]
    tokens = sum(int(e.get("in", 0) or 0) + int(e.get("out", 0) or 0)
                 + int(e.get("cache", 0) or 0) for e in evs)
    cost = sum(cost_usd_events(evs, pricing).values()) if evs else 0.0
    return tokens, cost


def period_totals(events: List[Dict[str, Any]], gran: str, pricing: Dict[str, Any],
                  offset: int = 0, today: Optional[date] = None) -> tuple:
    """(tokens, cost_usd) for the single period ``offset`` periods from now."""
    start, end = period_bounds(gran, offset, today)
    return _sum_between(events, start, end, pricing)


def period_window(events: List[Dict[str, Any]], gran: str, pricing: Dict[str, Any],
                  count: int, offset: int = 0, today: Optional[date] = None) -> List[tuple]:
    """``count`` consecutive, ZERO-FILLED periods ending at (current + offset),
    ordered oldest→newest → ``[(label, tokens, cost_usd)]``. ``offset`` (≤ 0)
    pages the window into the past for the Dashboard's prev/next navigation."""
    out = []
    for i in range(count - 1, -1, -1):
        start, end = period_bounds(gran, offset - i, today)
        tok, cost = _sum_between(events, start, end, pricing)
        out.append((_period_label(gran, start), tok, cost))
    return out


def period_breakdown(events: List[Dict[str, Any]], gran: str, pricing: Dict[str, Any],
                     offset: int = 0, today: Optional[date] = None) -> List[tuple]:
    """Break the SELECTED period (``offset`` periods from now) into its sub-parts
    → ``[(label, tokens, cost_usd)]``:
      · week  → 7 days Mon→Sun (label ``MM/DD``)
      · month → weeks W1…Wn (7-day chunks from the 1st)
      · year  → 12 months (label ``01``…``12``)."""
    from datetime import timedelta
    start, end = period_bounds(gran, offset, today)
    out = []
    if gran == "week":
        for i in range(7):
            d = start + timedelta(days=i)
            tok, cost = _sum_between(events, d, d + timedelta(days=1), pricing)
            out.append((d.strftime("%m/%d"), tok, cost))
    elif gran == "year":
        for m in range(1, 13):
            ms = date(start.year, m, 1)
            me = date(start.year + 1, 1, 1) if m == 12 else date(start.year, m + 1, 1)
            tok, cost = _sum_between(events, ms, me, pricing)
            out.append((f"{m:02d}", tok, cost))
    else:  # month → weeks W1..Wn
        ndays = (end - start).days
        wk, day = 1, 1
        while day <= ndays:
            ws = date(start.year, start.month, day)
            we = date(start.year, start.month, day + 7) if day + 7 <= ndays else end
            tok, cost = _sum_between(events, ws, we, pricing)
            out.append((f"W{wk}", tok, cost))
            wk += 1
            day += 7
    return out


def period_range_label(gran: str, offset: int, today: Optional[date] = None) -> str:
    """Human label for the selected period (shown in the Dashboard header) —
    week → MM/DD – MM/DD, month → YYYY/MM, year → YYYY."""
    from datetime import timedelta
    start, end = period_bounds(gran, offset, today)
    if gran == "week":
        last_day = end - timedelta(days=1)
        return f"{start.strftime('%m/%d')} – {last_day.strftime('%m/%d')}"
    if gran == "year":
        return str(start.year)
    return start.strftime("%Y/%m")


def set_budget(config, amount: float, currency: Optional[str] = None) -> None:
    """Set (or reset) the spending budget. ``amount`` is read in ``currency``
    (defaults to the current display currency) and converted + stored as USD.

    Remaining balance is always DERIVED fresh from the usage log — never
    incrementally decremented — so re-entering a budget starts a clean window
    instead of double-subtracting spend the old budget had already accounted
    for. The cutoff is an EVENT-COUNT baseline (how many usage events existed
    at the moment of setting), not a timestamp: events append in chronological
    order and ``budget_set_at`` only has 1-second resolution, so a timestamp
    cutoff could mis-include/exclude an event recorded in that same second —
    the count baseline is exact regardless of timing."""
    from . import model_pricing as mp
    usage = config.data.setdefault("usage", {})
    ccy = (currency or usage.get("currency") or "USD").upper()
    usage["budget_amount_usd"] = mp.convert(float(amount or 0), ccy, "USD", config)
    usage["budget_set_at"] = datetime.now().isoformat(timespec="seconds")   # display only
    usage["budget_baseline_count"] = len(load_events())                     # the real cutoff


def clear_budget(config) -> None:
    """Remove the budget entirely (Remaining/Budget box goes back to unset)."""
    usage = config.data.setdefault("usage", {})
    usage.pop("budget_amount_usd", None)
    usage.pop("budget_set_at", None)
    usage.pop("budget_baseline_count", None)


def budget_status(config) -> Optional[Dict[str, Any]]:
    """``None`` when no budget is configured. Else a dict with ``amount_usd``,
    ``spent_usd`` (cost of events recorded AFTER the budget was last set — NOT
    the all-time total, so a reset budget never inherits older spend),
    ``remaining_usd``, ``pct_used`` and ``over_85`` (⚠ the Overview/Dashboard
    balance turns red at this point)."""
    usage = (getattr(config, "data", {}) or {}).get("usage") or {}
    amount = usage.get("budget_amount_usd")
    set_at = usage.get("budget_set_at")
    if not amount or not set_at:
        return None
    all_events = load_events()
    baseline = usage.get("budget_baseline_count")
    if baseline is None:
        # backward-compat: a budget set before this field existed — fall back
        # to the timestamp cutoff (best-effort, may double-count a same-second event).
        events = [e for e in all_events if str(e.get("ts", "")) >= str(set_at)]
    else:
        events = all_events[int(baseline):]
    pricing = {**DEFAULT_PRICING, **usage}
    spent = sum(cost_usd_events(events, pricing).values())
    amount = float(amount)
    pct = (spent / amount) if amount else 0.0
    return {
        "amount_usd": amount, "spent_usd": spent, "remaining_usd": amount - spent,
        "pct_used": pct, "over_85": pct >= 0.85, "set_at": set_at,
    }


def format_cost(usd: float, pricing: Dict[str, Any], digits: Optional[int] = None) -> str:
    """Format a USD amount in the display currency. ``digits`` caps the number
    of decimal places (e.g. ``digits=2`` for the Total cost / Budget cards, so
    USD shows $1.23 not the default up-to-4 $1.2345) — never ADDS decimals to a
    currency that uses fewer (VND stays whole, JPY one place)."""
    p = {**DEFAULT_PRICING, **(pricing or {})}
    cur = p.get("currency", "USD")
    rate = {"USD": 1.0, "VND": float(p["usd_to_vnd"]), "JPY": float(p["usd_to_jpy"])}.get(cur, 1.0)
    symbol, cur_digits = _CURRENCY_FMT.get(cur, ("$", 2))
    if digits is not None:
        cur_digits = min(cur_digits, digits)
    value = usd * rate
    return f"{symbol}{value:,.{cur_digits}f}"


def format_cost_compact(usd: float, pricing: Dict[str, Any]) -> str:
    """Compact cost format for the Dashboard chart's y-axis/endpoint labels —
    always 2 decimals (not format_cost's up-to-4 for USD) and abbreviated with
    K/M above 1,000/1,000,000, same convention as ``fmt_tokens``. The chart's
    y-axis label box is narrow; the longer full-precision string used to
    overflow it, visually clipping/obscuring the leading currency symbol."""
    p = {**DEFAULT_PRICING, **(pricing or {})}
    cur = p.get("currency", "USD")
    rate = {"USD": 1.0, "VND": float(p["usd_to_vnd"]), "JPY": float(p["usd_to_jpy"])}.get(cur, 1.0)
    symbol, _digits = _CURRENCY_FMT.get(cur, ("$", 2))
    value = usd * rate
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000:
        body = f"{value / 1_000_000:,.2f}M"
    elif value >= 1_000:
        body = f"{value / 1_000:,.2f}K"
    else:
        body = f"{value:,.2f}"
    return f"{sign}{symbol}{body}"


_AI_ANALYSIS_HEADERS = {
    "vi": ("Nhận xét thói quen", "Cách viết prompt tiết kiệm hơn", "Hành động giảm token"),
    "en": ("Usage habits", "Writing more efficient prompts", "Actions to cut token usage"),
    "ja": ("利用傾向", "より効率的なプロンプトの書き方", "トークン削減のためのアクション"),
}


def build_ai_analysis_prompt(summary: Dict[str, Any], language: str = "vi") -> str:
    """The prompt sent to the model for '✨ AI analyze my usage': aggregated
    numbers only — never raw prompt contents — asking for concrete habits
    feedback and token-saving recommendations, in the CURRENTLY SELECTED
    display language (headers included — not just the model's free-text reply,
    which would otherwise leave the section titles in Vietnamese regardless of
    the app's language setting)."""
    lang_names = {"vi": "Vietnamese", "ja": "Japanese", "en": "English"}
    h1, h2, h3 = _AI_ANALYSIS_HEADERS.get(language, _AI_ANALYSIS_HEADERS["vi"])
    top = "\n".join(f"- {label}: {tok:,} tokens"
                    for label, tok in summary.get("top_labels", []))
    by_source = ", ".join(f"{k}={v:,}" for k, v in summary.get("by_source", []))
    return (
        "You are a token-efficiency coach for an AI desktop app (chat tabs + "
        "scheduled agent tasks). Analyze this usage summary and give the user "
        "practical advice, replying in "
        f"{lang_names.get(language, 'Vietnamese')}.\n\n"
        f"Period stats: {summary.get('turns', 0)} turns, "
        f"input={summary.get('in', 0):,} tokens, output={summary.get('out', 0):,}, "
        f"cache={summary.get('cache', 0):,}, "
        f"avg per prompt={summary.get('avg_per_turn', 0):,}.\n"
        f"Top consumers:\n{top or '- (none)'}\n"
        f"By area: {by_source or '(none)'}\n"
        f"Busiest day: {summary.get('busiest_day')} · busiest hour: {summary.get('busiest_hour')}\n\n"
        "Reply with EXACTLY these 3 short sections, in markdown, using THESE "
        f"section headers verbatim (already in {lang_names.get(language, 'Vietnamese')}):\n"
        f"1. **{h1}** — 2-3 bullet points about the usage pattern.\n"
        f"2. **{h2}** — 3 concrete prompt-writing tips "
        "tailored to the numbers above (e.g. long inputs → attach less / summarize "
        "first; many small turns → batch questions).\n"
        f"3. **{h3}** — 2-3 app-level actions (compact history, "
        "smaller model for simple tasks, reuse task outputs instead of re-asking).\n"
        "Keep the whole reply under 250 words."
    )


