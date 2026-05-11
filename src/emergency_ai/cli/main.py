"""CLI demo: `emergency "<situation>" --city "<name>"`.

Streams the response live to the terminal and prints a latency banner.
"""

from __future__ import annotations

import asyncio
import os
import time

import click
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..core.cities import load_cities
from ..core.client import AnthropicProvider, EmergencyClient, MockProvider
from ..core.schema import EmergencyRequest

console = Console()

URGENCY_STYLE = {
    "critical": "bold white on red",
    "high": "bold red",
    "medium": "bold yellow",
    "low": "bold green",
}


def _build_panel(state: dict, ttft_ms: int | None, elapsed_ms: int, cache_hint: str) -> Panel:
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(style="dim", width=20)
    table.add_column()

    urgency = state.get("urgency")
    if urgency:
        style = URGENCY_STYLE.get(urgency, "bold")
        table.add_row("URGENCY", Text(urgency.upper(), style=style))
    if "time_to_act_seconds" in state:
        table.add_row("Act within", f"{state['time_to_act_seconds']}s")
    if state.get("immediate_actions"):
        actions = "\n".join(f"  {i+1}. {a}" for i, a in enumerate(state["immediate_actions"]))
        table.add_row("Actions", actions)
    if state.get("who_to_call"):
        calls = "\n".join(f"  {k}: [bold]{v}[/bold]" for k, v in state["who_to_call"].items())
        table.add_row("Call", calls)
    if state.get("avoid"):
        avoids = "\n".join(f"  • {a}" for a in state["avoid"])
        table.add_row("Avoid", avoids)
    if state.get("jurisdictional_notes"):
        table.add_row("Local notes", state["jurisdictional_notes"])
    if "confidence" in state:
        conf_pct = int(state["confidence"] * 100)
        table.add_row("Confidence", f"{conf_pct}%")

    latency_bits = []
    if ttft_ms is not None:
        latency_bits.append(f"TTFT: [bold green]{ttft_ms} ms[/bold green]")
    latency_bits.append(f"elapsed: {elapsed_ms} ms")
    latency_bits.append(f"cache: {cache_hint}")
    subtitle = " · ".join(latency_bits)

    return Panel(table, title="emergency-ai", subtitle=subtitle, border_style="red")


async def _run(situation: str, city: str, use_mock: bool) -> int:
    cities = load_cities()
    if use_mock:
        provider = MockProvider()
        cache_hint = "mock"
    else:
        try:
            provider = AnthropicProvider()
            cache_hint = "live"
        except RuntimeError as e:
            console.print(f"[red]error:[/red] {e}", style="bold")
            console.print("Set ANTHROPIC_API_KEY or pass --mock for a canned demo.")
            return 2

    client = EmergencyClient(provider, cities)
    req = EmergencyRequest(situation=situation, city=city)

    state: dict = {}
    ttft_ms: int | None = None
    t0 = time.monotonic()

    with Live(_build_panel(state, None, 0, cache_hint), refresh_per_second=20, console=console) as live:
        async for ev in client.stream(req):
            elapsed = int((time.monotonic() - t0) * 1000)
            if ev.field.startswith("__"):
                if ev.field == "__final__":
                    fr = ev.value
                    state = fr.model_dump()
                elif ev.field == "__error__":
                    console.print(f"\n[yellow]warn:[/yellow] {ev.value}")
                live.update(_build_panel(state, ttft_ms, elapsed, cache_hint))
                continue
            if ttft_ms is None:
                ttft_ms = elapsed
            state[ev.field] = ev.value
            live.update(_build_panel(state, ttft_ms, elapsed, cache_hint))
    return 0


@click.command()
@click.argument("situation", required=True)
@click.option("--city", default="New York", help="City for jurisdictional context.")
@click.option(
    "--mock", "use_mock", is_flag=True, default=None, help="Use canned response (no API call)."
)
def cli(situation: str, city: str, use_mock: bool | None) -> None:
    """Get fast emergency action steps for SITUATION in --city."""
    if use_mock is None:
        use_mock = os.environ.get("EMERGENCY_AI_MOCK") == "1"
    code = asyncio.run(_run(situation, city, bool(use_mock)))
    raise SystemExit(code)


if __name__ == "__main__":
    cli()
