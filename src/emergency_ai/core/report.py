"""
core/report.py — incident report renderer.

`render_incident_report(events)` produces a clean Markdown incident report
suitable for export (e.g. via the /export endpoint or the PWA "Export" button).

Privacy invariant (§7): events contain ONLY {request_id, ts, city, urgency,
ttft_ms, total_ms, source, cache_hit}. The raw situation text is never present
and is never rendered here.
"""

from __future__ import annotations

import contextlib
import math
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_URGENCY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_URGENCY_LABEL = {
    "critical": "CRITICAL",
    "high":     "HIGH    ",
    "medium":   "MEDIUM  ",
    "low":      "LOW     ",
}


def _fmt_ts(ts: Any) -> str:
    """Return a compact UTC timestamp string from an ISO string or datetime."""
    if ts is None:
        return "—"
    if hasattr(ts, "isoformat"):
        dt: datetime = ts
    else:
        try:
            dt = datetime.fromisoformat(str(ts))
        except (ValueError, TypeError):
            return str(ts)
    # Normalise to UTC display
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt_latency(ttft_ms: Any, total_ms: Any) -> str:
    """Return a human-friendly latency cell: 'ttft / total ms'."""
    def _ms(v: Any) -> str:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "—"
        try:
            return f"{float(v):.0f}"
        except (ValueError, TypeError):
            return "—"

    t = _ms(ttft_ms)
    tot = _ms(total_ms)
    if t == "—" and tot == "—":
        return "—"
    return f"{t} / {tot} ms"


def _fmt_source(source: Any) -> str:
    if not source:
        return "—"
    return str(source)


def _fmt_city(city: Any) -> str:
    if not city:
        return "—"
    return str(city)


def _fmt_urgency(urgency: Any) -> str:
    if not urgency:
        return "—"
    return str(urgency).lower()


def _fmt_cache(cache_hit: Any) -> str:
    if cache_hit is None:
        return "—"
    return "hit" if cache_hit else "miss"


# ---------------------------------------------------------------------------
# Summary counts
# ---------------------------------------------------------------------------


def _compute_summary(events: list[dict]) -> dict:
    """Return aggregate statistics over the event list."""
    total = len(events)
    by_urgency: dict[str, int] = {}
    by_source: dict[str, int] = {}
    cache_hits = 0
    cache_total = 0
    latencies: list[float] = []
    ttfts: list[float] = []

    for ev in events:
        urg = _fmt_urgency(ev.get("urgency"))
        by_urgency[urg] = by_urgency.get(urg, 0) + 1

        src = _fmt_source(ev.get("source"))
        by_source[src] = by_source.get(src, 0) + 1

        ch = ev.get("cache_hit")
        if ch is not None:
            cache_total += 1
            if ch:
                cache_hits += 1

        tot = ev.get("total_ms")
        if tot is not None:
            with contextlib.suppress(ValueError, TypeError):
                latencies.append(float(tot))

        ttft = ev.get("ttft_ms")
        if ttft is not None:
            with contextlib.suppress(ValueError, TypeError):
                ttfts.append(float(ttft))

    def _avg(lst: list[float]) -> str:
        return f"{sum(lst) / len(lst):.0f} ms" if lst else "—"

    def _p95(lst: list[float]) -> str:
        if not lst:
            return "—"
        s = sorted(lst)
        idx = max(0, math.ceil(0.95 * len(s)) - 1)
        return f"{s[idx]:.0f} ms"

    cache_pct = f"{100 * cache_hits / cache_total:.1f}%" if cache_total else "—"

    return {
        "total": total,
        "by_urgency": by_urgency,
        "by_source": by_source,
        "cache_hits": cache_hits,
        "cache_total": cache_total,
        "cache_pct": cache_pct,
        "avg_latency": _avg(latencies),
        "p95_latency": _p95(latencies),
        "avg_ttft": _avg(ttfts),
    }


# ---------------------------------------------------------------------------
# Markdown table helpers
# ---------------------------------------------------------------------------


def _md_row(cells: list[str], widths: list[int]) -> str:
    padded = [c.ljust(w) for c, w in zip(cells, widths, strict=False)]
    return "| " + " | ".join(padded) + " |"


def _md_separator(widths: list[int]) -> str:
    return "| " + " | ".join("-" * w for w in widths) + " |"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_incident_report(events: list[dict]) -> str:
    """
    Produce a Markdown incident report from a list of audit events.

    Each event must contain only privacy-safe fields:
        {request_id, ts, city, urgency, ttft_ms, total_ms, source, cache_hit}

    The raw situation text is NEVER included in events (privacy invariant §7).

    Args:
        events: List of incident dicts, newest-first (as returned by
                IncidentStore.recent()).  Empty list produces a valid empty report.

    Returns:
        A UTF-8 Markdown string suitable for display or download.
    """
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines: list[str] = []

    # ---- Header ---------------------------------------------------------
    lines.append("# Emergency-AI — Incident Report")
    lines.append("")
    lines.append(f"**Generated:** {generated_at}")
    lines.append(f"**Events:** {len(events)}")
    lines.append("")
    lines.append(
        "> **Privacy notice:** This report contains no patient or situation text. "
        "Only operational metadata is stored and exported, per the system privacy "
        "invariant (§7)."
    )
    lines.append("")

    # ---- Event table ----------------------------------------------------
    lines.append("## Incident Log")
    lines.append("")

    if not events:
        lines.append("_No incidents recorded._")
        lines.append("")
    else:
        col_headers = ["Timestamp (UTC)", "City", "Urgency", "Latency (TTFT / Total)", "Source", "Cache"]

        # Build rows first so we can size columns
        rows: list[list[str]] = []
        for ev in events:
            rows.append([
                _fmt_ts(ev.get("ts")),
                _fmt_city(ev.get("city")),
                _fmt_urgency(ev.get("urgency")),
                _fmt_latency(ev.get("ttft_ms"), ev.get("total_ms")),
                _fmt_source(ev.get("source")),
                _fmt_cache(ev.get("cache_hit")),
            ])

        # Column widths = max(header, data) for each column
        widths = [len(h) for h in col_headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(cell))

        lines.append(_md_row(col_headers, widths))
        lines.append(_md_separator(widths))
        for row in rows:
            lines.append(_md_row(row, widths))
        lines.append("")

    # ---- Summary --------------------------------------------------------
    lines.append("## Summary")
    lines.append("")

    summary = _compute_summary(events)
    lines.append("| Metric | Value |")
    lines.append("| ------ | ----- |")
    lines.append(f"| Total incidents | {summary['total']} |")
    lines.append(f"| Avg response latency | {summary['avg_latency']} |")
    lines.append(f"| p95 response latency | {summary['p95_latency']} |")
    lines.append(f"| Avg TTFT | {summary['avg_ttft']} |")
    lines.append(
        f"| Cache hit rate | {summary['cache_pct']}"
        + (f" ({summary['cache_hits']}/{summary['cache_total']})" if summary['cache_total'] else "")
        + " |"
    )
    lines.append("")

    # ---- Urgency breakdown ----------------------------------------------
    by_urg = summary["by_urgency"]
    if by_urg:
        lines.append("### Urgency Breakdown")
        lines.append("")
        lines.append("| Urgency | Count |")
        lines.append("| ------- | ----- |")
        for urg in ("critical", "high", "medium", "low"):
            count = by_urg.get(urg, 0)
            if count:
                lines.append(f"| {urg} | {count} |")
        # Any unknown urgency values
        for urg, count in sorted(by_urg.items()):
            if urg not in ("critical", "high", "medium", "low"):
                lines.append(f"| {urg} | {count} |")
        lines.append("")

    # ---- Source breakdown -----------------------------------------------
    by_src = summary["by_source"]
    if by_src:
        lines.append("### Source Breakdown")
        lines.append("")
        lines.append("| Source | Count |")
        lines.append("| ------ | ----- |")
        for src, count in sorted(by_src.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| {src} | {count} |")
        lines.append("")

    # ---- Footer ---------------------------------------------------------
    lines.append("---")
    lines.append("")
    lines.append(
        "_emergency-ai — decision support only. "
        "For life-threatening emergencies, call your local emergency number immediately._"
    )
    lines.append("")

    return "\n".join(lines)
