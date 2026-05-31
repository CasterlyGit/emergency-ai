"""Offline weighted-keyword triage classifier.

classify(situation, city) -> TriageResult

Deterministic, pure-Python, no network calls, no model key required.
The same keyword logic is mirrored in docs/js/engine.js for the offline PWA.

Privacy: this module never logs or persists the situation text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emergency_ai.core.cities import CityContext

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

Urgency = str  # "critical" | "high" | "medium" | "low"


@dataclass(frozen=True)
class TriageResult:
    """Output of the keyword triage classifier."""

    urgency: Urgency
    score: float
    matched: str | None          # highest-weight term matched, or None
    signals: list[str] = field(default_factory=list)  # all matched terms


# ---------------------------------------------------------------------------
# Keyword tables
# Each entry: (pattern_string, weight)
# Patterns are matched case-insensitively against the full situation text.
# Weights within a tier are relative; the tier with the highest *sum* of
# matched weights wins.  Ties broken by tier priority (critical > high > ...).
# ---------------------------------------------------------------------------

# fmt: off
_CRITICAL_TERMS: list[tuple[str, float]] = [
    # breathing / cardiac
    (r"not\s+breathing",            10.0),
    (r"no\s+pulse",                 10.0),
    (r"cardiac\s+arrest",           10.0),
    (r"heart\s+stopped",            10.0),
    (r"no\s+heartbeat",             10.0),
    # airway
    (r"choking",                     9.5),
    (r"airway\s+blocked",            9.5),
    (r"can(?:'t|not)\s+breathe",     9.5),
    (r"unable\s+to\s+breathe",       9.5),
    # consciousness
    (r"unconscious",                 9.0),
    (r"unresponsive",                9.0),
    (r"won(?:'t|not)\s+wake\s+up",   9.0),
    (r"collapsed\s+and\s+not\s+moving", 9.0),
    # bleeding
    (r"severe\s+bleeding",           9.0),
    (r"spurting\s+blood",            9.5),
    (r"arterial\s+bleed",            9.5),
    (r"can(?:'t|not)\s+stop\s+bleed", 9.0),
    # toxicology
    (r"overdose",                    9.0),
    (r"opioid\s+overdose",           9.5),
    (r"fentanyl",                    9.5),
    (r"heroin\s+overdose",           9.5),
    # allergic
    (r"anaphylaxis",                 9.5),
    (r"anaphylactic",                9.5),
    (r"epipen",                      8.5),
    (r"throat\s+swelling",           9.0),
    (r"tongue\s+swelling",           8.5),
    (r"can(?:'t|not)\s+swallow",     8.0),
    # stroke
    (r"stroke\s+symptoms",           9.0),
    (r"face\s+drooping",             8.5),
    (r"arm\s+weakness",              7.5),
    (r"sudden\s+speech\s+problem",   8.0),
    # drowning
    (r"drowning",                    9.5),
    (r"submerged",                   8.5),
    (r"pulled\s+from\s+water",       8.5),
    # other critical
    (r"cardiac",                     6.0),   # lower weight — needs context
    (r"no\s+signs\s+of\s+life",     10.0),
    (r"blue\s+lips",                 8.5),
    (r"turning\s+blue",              8.5),
    (r"cyanosis",                    9.0),
    (r"not\s+responsive",            8.5),
    (r"electrocution",               8.5),
    (r"electric\s+shock",            8.0),
]

_HIGH_TERMS: list[tuple[str, float]] = [
    # chest
    (r"chest\s+pain",                8.0),
    (r"chest\s+tightness",           7.5),
    (r"heart\s+attack",              8.0),
    (r"pressure\s+in\s+chest",       7.5),
    (r"crushing\s+chest",            8.0),
    # trauma
    (r"broken\s+bone",               6.5),
    (r"fracture",                    6.5),
    (r"bone\s+sticking",             7.5),
    (r"compound\s+fracture",         8.0),
    (r"head\s+injury",               7.5),
    (r"head\s+trauma",               7.5),
    (r"skull\s+fracture",            8.5),
    (r"concussion",                  6.5),
    # burns
    (r"severe\s+burn",               7.5),
    (r"third[\s-]degree\s+burn",     8.5),
    (r"chemical\s+burn",             7.5),
    (r"large\s+area.*burn",          7.5),
    (r"burn.*large\s+area",          7.5),
    # seizure
    (r"seizure",                     7.5),
    (r"convulsing",                  7.5),
    (r"convulsion",                  7.5),
    (r"fitting",                     6.5),
    (r"epileptic",                   6.5),
    # other high
    (r"heavy\s+bleeding",            7.0),
    (r"deep\s+wound",                7.0),
    (r"deep\s+cut",                  7.0),
    (r"stab",                        7.0),
    (r"gunshot",                     8.0),
    (r"blunt\s+trauma",              7.0),
    (r"spinal\s+injury",             7.5),
    (r"neck\s+injury",               7.0),
    (r"difficulty\s+breathing",      6.5),
    (r"breathing\s+difficulty",      6.5),
    (r"labored\s+breathing",         7.0),
    (r"severe\s+allergic",           7.0),
    (r"hives.*swelling",             6.5),
    (r"swelling.*hives",             6.5),
    (r"high\s+fever",                5.5),
    (r"fever.*103",                  6.5),
    (r"fever.*104",                  7.0),
    (r"fever.*105",                  8.0),
    (r"heat\s+stroke",               7.5),
    (r"hypothermia",                 7.0),
    (r"frostbite",                   6.5),
    (r"poisoning",                   7.0),
    (r"ingested.*toxic",             7.5),
    (r"toxic.*ingested",             7.5),
    (r"childbirth",                  7.0),
    (r"labor.*baby",                 7.0),
    (r"baby.*coming",                7.5),
    (r"baby.*born",                  7.0),
    (r"premature\s+labor",           7.5),
]

_MEDIUM_TERMS: list[tuple[str, float]] = [
    (r"sprained",                    4.0),
    (r"twisted\s+ankle",             4.0),
    (r"dislocated",                  5.0),
    (r"minor\s+burn",                3.5),
    (r"small\s+cut",                 3.0),
    (r"laceration",                  4.5),
    (r"allergic\s+reaction",         5.0),
    (r"rash",                        2.5),
    (r"nausea",                      2.5),
    (r"vomiting",                    3.5),
    (r"dehydrated",                  3.0),
    (r"dehydration",                 3.0),
    (r"mild\s+fever",                2.5),
    (r"fever",                       3.0),
    (r"dizzy",                       3.0),
    (r"dizziness",                   3.0),
    (r"fainting",                    4.5),
    (r"fainted",                     4.5),
    (r"passed\s+out",                5.0),
    (r"headache",                    2.0),
    (r"severe\s+headache",           5.0),
    (r"migraine",                    3.0),
    (r"abdominal\s+pain",            4.0),
    (r"stomach\s+pain",              3.5),
    (r"back\s+pain",                 3.0),
    (r"sunburn",                     3.0),
    (r"insect\s+bite",               2.5),
    (r"bee\s+sting",                 3.5),
    (r"wasp\s+sting",                3.5),
    (r"nose\s+bleed",                3.0),
    (r"nosebleed",                   3.0),
    (r"bleeding\s+wound",            4.5),
    (r"anxiety\s+attack",            4.0),
    (r"panic\s+attack",              4.0),
    (r"hyperventilating",            5.0),
    (r"broken\s+finger",             4.0),
    (r"broken\s+toe",                3.5),
    (r"wrist\s+pain",                3.0),
    (r"heat\s+exhaustion",           5.5),
    (r"heat\s+cramps",               4.0),
    (r"muscle\s+cramp",              2.5),
    (r"food\s+poisoning",            4.5),
    (r"diabetic",                    5.0),
    (r"low\s+blood\s+sugar",         5.0),
    (r"hypoglycemia",                5.5),
]

_LOW_TERMS: list[tuple[str, float]] = [
    (r"mild\s+pain",                 1.5),
    (r"minor\s+injury",              1.5),
    (r"slight\s+cut",                1.0),
    (r"scrape",                      1.0),
    (r"bruise",                      1.5),
    (r"blister",                     1.0),
    (r"splinter",                    1.0),
    (r"minor\s+headache",            1.0),
    (r"sore\s+throat",               1.5),
    (r"runny\s+nose",                1.0),
    (r"sneezing",                    1.0),
    (r"cough",                       1.5),
    (r"hiccups",                     0.5),
    (r"indigestion",                 1.5),
    (r"heartburn",                   1.5),
    (r"constipation",                1.0),
    (r"minor\s+burn",                1.5),
    (r"paper\s+cut",                 0.5),
    (r"stubbed\s+toe",               0.5),
    (r"muscle\s+soreness",           1.0),
    (r"stiff\s+neck",                1.5),
    (r"tired",                       0.5),
    (r"fatigue",                     1.0),
    (r"mild\s+nausea",               1.5),
]
# fmt: on

# Compile all patterns once at import time for performance.
_TIERS: list[tuple[Urgency, list[tuple[re.Pattern[str], str, float]]]] = []

for _urgency_level, _raw_terms in (
    ("critical", _CRITICAL_TERMS),
    ("high", _HIGH_TERMS),
    ("medium", _MEDIUM_TERMS),
    ("low", _LOW_TERMS),
):
    _compiled: list[tuple[re.Pattern[str], str, float]] = []
    for _pattern, _weight in _raw_terms:
        _compiled.append(
            (re.compile(_pattern, re.IGNORECASE), _pattern, _weight)
        )
    _TIERS.append((_urgency_level, _compiled))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify(situation: str, city: CityContext) -> TriageResult:
    """Classify the urgency of an emergency situation.

    Parameters
    ----------
    situation:
        Raw free-text description of what is happening.  Never stored or logged.
    city:
        CityContext for the location.  Currently unused by the classifier itself
        (reserved for future location-aware overrides) but required by the
        interface so callers don't need to change when that feature lands.

    Returns
    -------
    TriageResult
        urgency  — "critical" | "high" | "medium" | "low"
        score    — sum of weights for the winning tier (0.0 if nothing matched)
        matched  — the single highest-weight term that fired, or None
        signals  — every term that matched, in descending weight order

    Notes
    -----
    - Deterministic: same input always produces same output.
    - No network, no model key, no heavy dependencies.
    - Privacy: situation text is processed in-memory only and never persisted.
    """
    text = situation  # never store; work on local ref only

    # For each tier, collect all matching (term_string, weight) pairs.
    tier_hits: dict[Urgency, list[tuple[str, float]]] = {
        urgency: [] for urgency, _ in _TIERS
    }

    for urgency_level, patterns in _TIERS:
        for compiled_re, term_str, weight in patterns:
            if compiled_re.search(text):
                tier_hits[urgency_level].append((term_str, weight))

    # Compute aggregate score per tier.
    tier_scores: dict[Urgency, float] = {
        u: sum(w for _, w in hits) for u, hits in tier_hits.items()
    }

    # Determine the winning urgency: highest score wins; ties broken by tier
    # priority order (critical > high > medium > low).
    tier_order: list[Urgency] = [u for u, _ in _TIERS]
    winning_urgency: Urgency = "low"
    winning_score: float = 0.0

    for urgency_level in tier_order:
        s = tier_scores[urgency_level]
        if s > winning_score:
            winning_score = s
            winning_urgency = urgency_level

    # Collect all signals from the winning tier, sorted by weight descending.
    winning_hits = sorted(
        tier_hits[winning_urgency], key=lambda x: x[1], reverse=True
    )

    signals: list[str] = [term for term, _ in winning_hits]
    matched: str | None = signals[0] if signals else None

    # Edge case: nothing matched at all — default to medium (unknown, better safe).
    if winning_score == 0.0:
        return TriageResult(
            urgency="medium",
            score=0.0,
            matched=None,
            signals=[],
        )

    return TriageResult(
        urgency=winning_urgency,
        score=round(winning_score, 4),
        matched=matched,
        signals=signals,
    )
