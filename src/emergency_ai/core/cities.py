"""City context loader.

Each city lives as a single markdown file with YAML frontmatter:

    ---
    slug: new-york
    display_name: New York
    country: USA
    primary_emergency_number: "911"
    aliases: ["NYC", "New York City"]
    ---
    ## Emergency numbers
    ...
    ## Local laws relevant in emergencies
    ...

The body is sent verbatim into the system prompt's cached block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib.resources import files as pkg_files
from pathlib import Path

import yaml

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class CityContext:
    slug: str
    display_name: str
    country: str
    primary_emergency_number: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    body: str = ""

    @property
    def known(self) -> bool:
        return self.slug != "_unknown"


UNKNOWN_CITY_CONTEXT = CityContext(
    slug="_unknown",
    display_name="Unknown",
    country="",
    primary_emergency_number="112",
    aliases=(),
    body=(
        "No city-specific context is available for this location. "
        "Use generic best practice: contact local emergency services, "
        "follow operator instructions, and avoid speculation about local laws."
    ),
)


def _parse_markdown(text: str, fallback_slug: str) -> CityContext:
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"City file {fallback_slug!r} missing YAML frontmatter")
    meta = yaml.safe_load(m.group(1)) or {}
    body = m.group(2).strip()
    slug = str(meta.get("slug") or fallback_slug).strip().lower()
    return CityContext(
        slug=slug,
        display_name=str(meta.get("display_name") or slug.title()),
        country=str(meta.get("country") or ""),
        primary_emergency_number=str(meta.get("primary_emergency_number") or "112"),
        aliases=tuple(str(a) for a in (meta.get("aliases") or [])),
        body=body,
    )


def load_cities(directory: Path | None = None) -> dict[str, CityContext]:
    """Load every `*.md` city file. Returns {slug: CityContext}.

    If `directory` is None, loads bundled cities from the package data dir.
    """
    if directory is None:
        pkg_root = pkg_files("emergency_ai") / "cities"
        # importlib.resources Traversable -> iterate
        cities: dict[str, CityContext] = {}
        for entry in pkg_root.iterdir():
            name = entry.name
            if not name.endswith(".md"):
                continue
            text = entry.read_text(encoding="utf-8")
            ctx = _parse_markdown(text, fallback_slug=name[:-3])
            cities[ctx.slug] = ctx
        return cities

    cities = {}
    for path in sorted(Path(directory).glob("*.md")):
        text = path.read_text(encoding="utf-8")
        ctx = _parse_markdown(text, fallback_slug=path.stem)
        cities[ctx.slug] = ctx
    return cities


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def resolve_city(name: str, registry: dict[str, CityContext]) -> CityContext:
    """Look up by slug, display_name, or alias (case/punctuation-insensitive)."""
    norm = _normalize(name)
    if norm in registry:
        return registry[norm]
    for ctx in registry.values():
        if _normalize(ctx.display_name) == norm:
            return ctx
        for alias in ctx.aliases:
            if _normalize(alias) == norm:
                return ctx
    return UNKNOWN_CITY_CONTEXT
