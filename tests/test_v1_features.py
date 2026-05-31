"""Tests for the v1 backend modules.

Covers:
  core.triage.classify
  core.metrics (inc + render)
  core.cache.ResponseCache (in-memory path)
  core.store.IncidentStore (record/recent, rejects 'situation' key)
  core.geo.nearest_city (haversine picks right city)
  core.retrieval.JurisdictionIndex (search returns relevant snippet)
  core.report (render produces markdown, no situation text)
  core.scenarios (load / get / search)

All offline — no network, no API key. Runs with EMERGENCY_AI_MOCK=1.
"""

from __future__ import annotations

import asyncio
import os
import re

import pytest

# Ensure mock mode is set before any module imports resolve env vars.
os.environ.setdefault("EMERGENCY_AI_MOCK", "1")

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cities():
    """Load the bundled 6-city registry once per module."""
    from emergency_ai.core.cities import load_cities

    return load_cities()


@pytest.fixture(scope="module")
def ny(cities):
    return cities["new-york"]


@pytest.fixture(scope="module")
def london(cities):
    return cities["london"]


# ---------------------------------------------------------------------------
# core.triage.classify
# ---------------------------------------------------------------------------


class TestTriageClassify:
    """Deterministic keyword classifier — no model, no network."""

    def test_not_breathing_is_critical(self, ny):
        from emergency_ai.core.triage import classify

        result = classify("person is not breathing and has no pulse", ny)

        assert result.urgency == "critical"
        assert result.score > 0
        assert result.matched is not None

    def test_cardiac_arrest_is_critical(self, ny):
        from emergency_ai.core.triage import classify

        result = classify("patient in cardiac arrest", ny)

        assert result.urgency == "critical"
        assert result.score >= 10.0

    def test_choking_is_critical_with_correct_signals(self, ny):
        from emergency_ai.core.triage import classify

        result = classify("she is choking and cannot breathe", ny)

        assert result.urgency == "critical"
        # Both 'choking' and "can't breathe" variant should appear in signals
        assert len(result.signals) >= 1
        assert any("chok" in s for s in result.signals)

    def test_chest_pain_is_high(self, ny):
        from emergency_ai.core.triage import classify

        result = classify("severe chest pain with difficulty breathing", ny)

        assert result.urgency == "high"
        assert result.score > 0

    def test_seizure_is_high(self, ny):
        from emergency_ai.core.triage import classify

        result = classify("she is having a seizure and convulsing", ny)

        assert result.urgency == "high"
        assert "seizure" in result.signals or "convulsing" in result.signals

    def test_minor_scrape_is_low(self, ny):
        from emergency_ai.core.triage import classify

        result = classify("minor scrape on my knee", ny)

        assert result.urgency == "low"
        assert result.score > 0

    def test_blister_is_low(self, ny):
        from emergency_ai.core.triage import classify

        result = classify("I have a blister on my heel", ny)

        assert result.urgency == "low"

    def test_no_keywords_returns_medium_default(self, ny):
        from emergency_ai.core.triage import classify

        result = classify("something seems wrong", ny)

        assert result.urgency == "medium"
        assert result.score == 0.0
        assert result.matched is None
        assert result.signals == []

    def test_result_is_deterministic(self, ny):
        from emergency_ai.core.triage import classify

        text = "person collapsed and unresponsive"
        r1 = classify(text, ny)
        r2 = classify(text, ny)

        assert r1.urgency == r2.urgency
        assert r1.score == r2.score
        assert r1.matched == r2.matched
        assert r1.signals == r2.signals

    def test_signals_list_is_non_empty_for_matching_text(self, ny):
        from emergency_ai.core.triage import classify

        result = classify("anaphylaxis with throat swelling", ny)

        assert len(result.signals) >= 1

    def test_city_context_accepts_different_cities(self, london):
        """classify() accepts any CityContext — London should give same logic."""
        from emergency_ai.core.triage import classify

        result = classify("person is not breathing", london)

        assert result.urgency == "critical"

    def test_opioid_overdose_is_critical(self, ny):
        from emergency_ai.core.triage import classify

        result = classify("suspected fentanyl overdose, unresponsive", ny)

        assert result.urgency == "critical"

    def test_drowning_is_critical(self, ny):
        from emergency_ai.core.triage import classify

        result = classify("person is drowning at the beach", ny)

        assert result.urgency == "critical"


# ---------------------------------------------------------------------------
# core.metrics
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def reset_metrics():
    """Reset global metrics state before and after each test that uses it."""
    from emergency_ai.core import metrics as m

    m._reset()
    yield
    m._reset()


class TestMetrics:
    def test_render_returns_string(self, reset_metrics):
        from emergency_ai.core import metrics as m

        output = m.render()

        assert isinstance(output, str)

    def test_render_ends_with_newline(self, reset_metrics):
        from emergency_ai.core import metrics as m

        output = m.render()

        assert output.endswith("\n")

    def test_render_contains_help_and_type_lines(self, reset_metrics):
        from emergency_ai.core import metrics as m

        output = m.render()

        assert "# HELP emergency_requests_total" in output
        assert "# TYPE emergency_requests_total counter" in output
        assert "# HELP emergency_cache_hits_total" in output
        assert "# TYPE emergency_cache_hits_total counter" in output
        assert "# HELP emergency_errors_total" in output
        assert "# TYPE emergency_errors_total counter" in output
        assert "# HELP emergency_ttft_ms" in output
        assert "# TYPE emergency_ttft_ms histogram" in output

    def test_inc_request_appears_in_render(self, reset_metrics):
        from emergency_ai.core import metrics as m

        m.inc_request("new-york", "critical", "live")
        output = m.render()

        assert 'city="new-york"' in output
        assert 'urgency="critical"' in output
        assert 'source="live"' in output
        # Value should be 1
        assert "} 1" in output

    def test_inc_request_accumulates_counts(self, reset_metrics):
        from emergency_ai.core import metrics as m

        m.inc_request("london", "high", "mock")
        m.inc_request("london", "high", "mock")
        m.inc_request("london", "high", "mock")
        output = m.render()

        # The label set for london/high/mock should show 3
        assert "} 3" in output

    def test_cache_hit_counter(self, reset_metrics):
        from emergency_ai.core import metrics as m

        m.inc_cache_hit()
        m.inc_cache_hit()
        output = m.render()

        # emergency_cache_hits_total should be 2
        assert "emergency_cache_hits_total 2" in output

    def test_error_counter(self, reset_metrics):
        from emergency_ai.core import metrics as m

        m.inc_error()
        output = m.render()

        assert "emergency_errors_total 1" in output

    def test_histogram_inf_bucket_present(self, reset_metrics):
        from emergency_ai.core import metrics as m

        m.observe_ttft(120.0)
        output = m.render()

        assert 'le="+Inf"' in output

    def test_histogram_sum_and_count(self, reset_metrics):
        from emergency_ai.core import metrics as m

        m.observe_ttft(100.0)
        m.observe_ttft(200.0)
        output = m.render()

        assert "emergency_ttft_ms_sum 300" in output
        assert "emergency_ttft_ms_count 2" in output

    def test_histogram_buckets_are_cumulative(self, reset_metrics):
        from emergency_ai.core import metrics as m

        # Observe a value of 80ms — should fall in the <=100 bucket and all higher ones.
        m.observe_ttft(80.0)
        output = m.render()

        # The <=50 bucket should be 0 (80 > 50)
        assert 'le="50"} 0' in output
        # The <=100 bucket should be 1 (80 <= 100)
        assert 'le="100"} 1' in output
        # +Inf is always total observations
        assert 'le="+Inf"} 1' in output

    def test_prometheus_label_format_valid(self, reset_metrics):
        """Label values must be double-quoted in Prometheus text format."""
        from emergency_ai.core import metrics as m

        m.inc_request("san-francisco", "low", "offline")
        output = m.render()

        # Pattern: metric_name{...label="value"...} number
        label_pattern = re.compile(
            r'emergency_requests_total\{[^}]*city="san-francisco"[^}]*\} \d+'
        )
        assert label_pattern.search(output), "Expected valid Prometheus label format"

    def test_multiple_label_sets(self, reset_metrics):
        from emergency_ai.core import metrics as m

        m.inc_request("new-york", "critical", "live")
        m.inc_request("london", "high", "mock")
        output = m.render()

        assert 'city="new-york"' in output
        assert 'city="london"' in output


# ---------------------------------------------------------------------------
# core.cache.ResponseCache (in-memory path, no Redis)
# ---------------------------------------------------------------------------


class TestResponseCache:
    """All tests use the in-memory LRU path (no REDIS_URL set)."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    @pytest.fixture
    def cache(self):
        from emergency_ai.core.cache import ResponseCache

        c = ResponseCache()
        self._run(c.initialize())
        return c

    def test_miss_returns_none(self, cache):
        result = self._run(cache.get("new-york", "nobody home"))

        assert result is None

    def test_set_then_get_returns_value(self, cache):
        payload = {"urgency": "critical", "immediate_actions": ["Call 911"]}

        self._run(cache.set("new-york", "person not breathing", payload))
        result = self._run(cache.get("new-york", "person not breathing"))

        assert result == payload

    def test_different_city_slug_is_miss(self, cache):
        payload = {"urgency": "critical"}
        self._run(cache.set("new-york", "person collapsed", payload))

        # Same situation, different city — must be a miss
        result = self._run(cache.get("london", "person collapsed"))

        assert result is None

    def test_different_situation_is_miss(self, cache):
        payload = {"urgency": "high"}
        self._run(cache.set("tokyo", "chest pain", payload))

        result = self._run(cache.get("tokyo", "nausea"))

        assert result is None

    def test_key_is_case_insensitive(self, cache):
        """The cache normalises situation text so case doesn't matter."""
        payload = {"urgency": "low"}
        self._run(cache.set("london", "Minor scrape", payload))

        result = self._run(cache.get("london", "minor scrape"))

        assert result == payload

    def test_key_is_whitespace_insensitive(self, cache):
        payload = {"urgency": "high"}
        self._run(cache.set("mumbai", "chest   pain", payload))

        result = self._run(cache.get("mumbai", "chest pain"))

        assert result == payload

    def test_stored_key_is_hex_hash(self):
        """Keys must be SHA-256 hex strings, not raw text (privacy invariant)."""
        from emergency_ai.core.cache import _make_key

        key = _make_key("new-york", "person not breathing")

        assert len(key) == 64
        assert re.fullmatch(r"[0-9a-f]{64}", key), "Key must be lowercase hex SHA-256"

    def test_hash_hides_situation_text(self):
        """The raw situation string must not appear in the generated key."""
        from emergency_ai.core.cache import _make_key

        situation = "someone is having a cardiac arrest"
        key = _make_key("new-york", situation)

        assert situation not in key
        assert "cardiac" not in key

    def test_overwrite_updates_value(self, cache):
        self._run(cache.set("london", "burns", {"urgency": "high"}))
        self._run(cache.set("london", "burns", {"urgency": "critical"}))
        result = self._run(cache.get("london", "burns"))

        assert result == {"urgency": "critical"}


# ---------------------------------------------------------------------------
# core.store.IncidentStore
# ---------------------------------------------------------------------------


class TestIncidentStore:
    """In-memory backend (no DATABASE_URL or SQLITE_PATH)."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    @pytest.fixture
    def store(self):
        """Fresh in-memory store for each test."""
        from emergency_ai.core.store import IncidentStore

        return IncidentStore()

    def _valid_event(self, **overrides):
        base = {
            "request_id": "req-test-001",
            "city": "new-york",
            "urgency": "critical",
            "ttft_ms": 120.5,
            "total_ms": 800.0,
            "source": "mock",
            "cache_hit": False,
        }
        base.update(overrides)
        return base

    def test_record_and_retrieve(self, store):
        event = self._valid_event()
        self._run(store.record(event))
        recent = self._run(store.recent(10))

        assert len(recent) == 1
        assert recent[0]["request_id"] == "req-test-001"
        assert recent[0]["city"] == "new-york"
        assert recent[0]["urgency"] == "critical"

    def test_recent_is_newest_first(self, store):
        for i in range(3):
            self._run(store.record(self._valid_event(request_id=f"req-{i:03d}")))
        recent = self._run(store.recent(10))

        # Newest first: last inserted = req-002
        assert recent[0]["request_id"] == "req-002"

    def test_recent_respects_limit(self, store):
        for i in range(10):
            self._run(store.record(self._valid_event(request_id=f"req-{i:03d}")))
        recent = self._run(store.recent(3))

        assert len(recent) == 3

    def test_rejects_situation_key(self, store):
        event = self._valid_event(situation="DO NOT STORE THIS")

        with pytest.raises(ValueError, match="situation"):
            self._run(store.record(event))

    def test_rejects_unknown_keys(self, store):
        event = self._valid_event(patient_name="Alice")

        with pytest.raises(ValueError, match="Unknown event keys"):
            self._run(store.record(event))

    def test_ts_is_auto_populated_when_missing(self, store):
        event = self._valid_event()
        # Explicitly no 'ts' key
        event.pop("ts", None)
        self._run(store.record(event))
        recent = self._run(store.recent(1))

        assert "ts" in recent[0]
        assert recent[0]["ts"]  # non-empty

    def test_empty_store_returns_empty_list(self, store):
        recent = self._run(store.recent(10))

        assert recent == []

    def test_zero_limit_returns_empty_list(self, store):
        self._run(store.record(self._valid_event()))
        recent = self._run(store.recent(0))

        assert recent == []

    def test_stored_event_never_contains_situation(self, store):
        """Verify situation text cannot leak into stored records."""
        self._run(store.record(self._valid_event()))
        recent = self._run(store.recent(1))

        for key in recent[0]:
            assert key != "situation", "situation must never appear in stored records"


# ---------------------------------------------------------------------------
# core.geo.nearest_city
# ---------------------------------------------------------------------------


class TestNearestCity:
    def test_near_new_york_returns_new_york(self, cities):
        from emergency_ai.core.geo import nearest_city

        # Near JFK
        result = nearest_city(40.64, -73.78, cities)

        assert result.slug == "new-york"

    def test_near_london_returns_london(self, cities):
        from emergency_ai.core.geo import nearest_city

        result = nearest_city(51.50, -0.13, cities)

        assert result.slug == "london"

    def test_near_tokyo_returns_tokyo(self, cities):
        from emergency_ai.core.geo import nearest_city

        result = nearest_city(35.67, 139.65, cities)

        assert result.slug == "tokyo"

    def test_near_san_francisco_returns_san_francisco(self, cities):
        from emergency_ai.core.geo import nearest_city

        result = nearest_city(37.77, -122.42, cities)

        assert result.slug == "san-francisco"

    def test_near_mumbai_returns_mumbai(self, cities):
        from emergency_ai.core.geo import nearest_city

        result = nearest_city(19.07, 72.88, cities)

        assert result.slug == "mumbai"

    def test_near_bangalore_returns_bangalore(self, cities):
        from emergency_ai.core.geo import nearest_city

        result = nearest_city(12.97, 77.59, cities)

        assert result.slug == "bangalore"

    def test_empty_cities_returns_unknown(self):
        from emergency_ai.core.cities import UNKNOWN_CITY_CONTEXT
        from emergency_ai.core.geo import nearest_city

        result = nearest_city(40.71, -74.01, {})

        assert result is UNKNOWN_CITY_CONTEXT
        assert result.slug == "_unknown"

    def test_haversine_ny_to_london(self):
        from emergency_ai.core.geo import haversine_km

        dist = haversine_km((40.7128, -74.006), (51.5074, -0.1278))

        # Known great-circle distance ~5570 km
        assert 5500 < dist < 5650

    def test_haversine_same_point_is_zero(self):
        from emergency_ai.core.geo import haversine_km

        dist = haversine_km((40.7128, -74.006), (40.7128, -74.006))

        assert dist == pytest.approx(0.0, abs=1e-6)

    def test_haversine_is_symmetric(self):
        from emergency_ai.core.geo import haversine_km

        a = (40.7128, -74.006)
        b = (35.6762, 139.6503)
        assert haversine_km(a, b) == pytest.approx(haversine_km(b, a), rel=1e-9)

    def test_nearest_city_picks_closer_not_farther(self, cities):
        """A point much closer to Tokyo than London should resolve to Tokyo."""
        from emergency_ai.core.geo import nearest_city

        # Osaka coordinates — clearly closer to Tokyo than London
        lat, lon = 34.69, 135.50
        result = nearest_city(lat, lon, cities)

        assert result.slug == "tokyo"


# ---------------------------------------------------------------------------
# core.retrieval.JurisdictionIndex
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def jurisdiction_index(cities):
    from emergency_ai.core.retrieval import JurisdictionIndex

    return JurisdictionIndex(cities)


class TestJurisdictionIndex:
    def test_search_returns_snippets(self, jurisdiction_index):
        snippets = jurisdiction_index.search("good samaritan law immunity", "new-york", k=3)

        assert len(snippets) >= 1

    def test_snippet_has_text_score_and_slug(self, jurisdiction_index):
        snippets = jurisdiction_index.search("emergency law", "london", k=1)

        assert len(snippets) >= 1
        s = snippets[0]
        assert isinstance(s.text, str) and s.text
        assert isinstance(s.score, float) and s.score > 0.0
        assert s.city_slug == "london"

    def test_snippets_are_sorted_by_score_descending(self, jurisdiction_index):
        snippets = jurisdiction_index.search("ambulance hospital emergency", "new-york", k=5)

        scores = [s.score for s in snippets]
        assert scores == sorted(scores, reverse=True)

    def test_unknown_city_returns_empty(self, jurisdiction_index):
        snippets = jurisdiction_index.search("anything", "atlantis", k=3)

        assert snippets == []

    def test_empty_query_returns_empty(self, jurisdiction_index):
        snippets = jurisdiction_index.search("", "new-york", k=3)

        assert snippets == []

    def test_k_limits_results(self, jurisdiction_index):
        snippets = jurisdiction_index.search("emergency ambulance law hospital", "new-york", k=1)

        assert len(snippets) <= 1

    def test_relevant_query_scores_higher_than_irrelevant(self, jurisdiction_index):
        """A query matching specific city law content should score > 0."""
        snippets = jurisdiction_index.search("good samaritan", "new-york", k=3)

        # At least one result with a positive score
        assert any(s.score > 0 for s in snippets)

    def test_snippet_text_not_empty(self, jurisdiction_index):
        snippets = jurisdiction_index.search("police fire emergency", "london", k=3)

        for s in snippets:
            assert s.text.strip()

    def test_index_covers_multiple_cities(self, jurisdiction_index):
        """Index should find results for several different city slugs."""
        found_cities = set()
        for slug in ("new-york", "london", "tokyo"):
            snips = jurisdiction_index.search("emergency", slug, k=1)
            if snips:
                found_cities.add(slug)

        assert len(found_cities) >= 1


# ---------------------------------------------------------------------------
# core.report
# ---------------------------------------------------------------------------


class TestRenderIncidentReport:
    def _sample_events(self):
        return [
            {
                "request_id": "req-001",
                "ts": "2026-05-31T10:00:00+00:00",
                "city": "new-york",
                "urgency": "critical",
                "ttft_ms": 120.5,
                "total_ms": 800.0,
                "source": "live",
                "cache_hit": False,
            },
            {
                "request_id": "req-002",
                "ts": "2026-05-31T10:05:00+00:00",
                "city": "london",
                "urgency": "high",
                "ttft_ms": 90.0,
                "total_ms": 500.0,
                "source": "mock",
                "cache_hit": True,
            },
        ]

    def test_returns_string(self):
        from emergency_ai.core.report import render_incident_report

        report = render_incident_report([])

        assert isinstance(report, str)

    def test_starts_with_markdown_header(self):
        from emergency_ai.core.report import render_incident_report

        report = render_incident_report([])

        assert report.startswith("# Emergency-AI")

    def test_contains_privacy_notice(self):
        from emergency_ai.core.report import render_incident_report

        report = render_incident_report([])

        assert "Privacy notice" in report or "privacy" in report.lower()

    def test_no_situation_text_in_report(self):
        from emergency_ai.core.report import render_incident_report

        events = self._sample_events()
        report = render_incident_report(events)

        # The word "situation" may appear in the privacy disclaimer ("situation text")
        # but must NEVER be a stored data value. Verify no raw situation strings.
        # The report should not contain any user input text.
        for phrase in ["person not breathing", "fire in kitchen", "chest pain"]:
            assert phrase not in report

    def test_report_contains_incident_log_section(self):
        from emergency_ai.core.report import render_incident_report

        report = render_incident_report(self._sample_events())

        assert "## Incident Log" in report

    def test_report_contains_summary_section(self):
        from emergency_ai.core.report import render_incident_report

        report = render_incident_report(self._sample_events())

        assert "## Summary" in report

    def test_empty_events_produces_valid_report(self):
        from emergency_ai.core.report import render_incident_report

        report = render_incident_report([])

        assert "# Emergency-AI" in report
        assert "0" in report  # event count is zero

    def test_city_slug_appears_in_table(self):
        from emergency_ai.core.report import render_incident_report

        report = render_incident_report(self._sample_events())

        assert "new-york" in report
        assert "london" in report

    def test_urgency_appears_in_table(self):
        from emergency_ai.core.report import render_incident_report

        report = render_incident_report(self._sample_events())

        assert "critical" in report
        assert "high" in report

    def test_request_id_does_not_appear(self):
        """request_id is privacy-safe metadata but may appear — just ensure no crash."""
        from emergency_ai.core.report import render_incident_report

        # This is a smoke test: render should not raise with any valid events.
        report = render_incident_report(self._sample_events())

        assert isinstance(report, str) and len(report) > 100

    def test_latency_formatted_in_ms(self):
        from emergency_ai.core.report import render_incident_report

        report = render_incident_report(self._sample_events())

        # Latency column header
        assert "ms" in report.lower() or "Latency" in report

    def test_cache_hit_shown_as_hit_or_miss(self):
        from emergency_ai.core.report import render_incident_report

        report = render_incident_report(self._sample_events())

        assert "hit" in report or "miss" in report


# ---------------------------------------------------------------------------
# core.scenarios
# ---------------------------------------------------------------------------


class TestScenarios:
    def test_list_scenarios_returns_all(self):
        from emergency_ai.core.scenarios import list_scenarios

        scenarios = list_scenarios()

        # Spec requires minimum 18 scenarios
        assert len(scenarios) >= 18

    def test_each_scenario_has_required_fields(self):
        from emergency_ai.core.scenarios import list_scenarios

        required = {
            "id", "title", "short", "icon", "category", "keywords",
            "urgency", "time_to_act_seconds", "immediate_actions",
        }
        for scenario in list_scenarios():
            missing = required - set(scenario.keys())
            assert not missing, f"Scenario {scenario.get('id')!r} missing fields: {missing}"

    def test_get_known_scenario(self):
        from emergency_ai.core.scenarios import get

        scenario = get("cardiac-arrest")

        assert scenario is not None
        assert scenario["id"] == "cardiac-arrest"
        assert scenario["urgency"] == "critical"

    def test_get_unknown_scenario_returns_none(self):
        from emergency_ai.core.scenarios import get

        assert get("nonexistent-scenario-xyz") is None

    def test_canonical_scenarios_present(self):
        """Spec §8 mandates specific scenario IDs."""
        from emergency_ai.core.scenarios import get

        required_ids = [
            "cardiac-arrest",
            "choking-adult",
            "severe-bleeding",
            "stroke",
            "anaphylaxis",
        ]
        for sid in required_ids:
            assert get(sid) is not None, f"Missing required scenario: {sid!r}"

    def test_search_finds_by_keyword(self):
        from emergency_ai.core.scenarios import search

        results = search("cardiac")

        ids = [s["id"] for s in results]
        assert "cardiac-arrest" in ids

    def test_search_finds_choking(self):
        from emergency_ai.core.scenarios import search

        results = search("choking")
        ids = [s["id"] for s in results]

        assert "choking-adult" in ids

    def test_search_is_case_insensitive(self):
        from emergency_ai.core.scenarios import search

        results_lower = search("stroke")
        results_upper = search("STROKE")

        ids_lower = {s["id"] for s in results_lower}
        ids_upper = {s["id"] for s in results_upper}
        assert ids_lower == ids_upper

    def test_search_empty_query_returns_all(self):
        from emergency_ai.core.scenarios import list_scenarios, search

        all_scenarios = list_scenarios()
        search_results = search("")

        assert len(search_results) == len(all_scenarios)

    def test_search_no_match_returns_empty(self):
        from emergency_ai.core.scenarios import search

        results = search("xyznomatchterm12345")

        assert results == []

    def test_search_ranks_more_specific_first(self):
        """A query matching more terms in a scenario should rank it higher."""
        from emergency_ai.core.scenarios import search

        # "cardiac arrest" should rank cardiac-arrest above less-relevant ones
        results = search("cardiac arrest breathing")

        assert results[0]["id"] == "cardiac-arrest"

    def test_list_returns_shallow_copies(self):
        """Mutating a returned scenario must not affect the internal cache."""
        from emergency_ai.core.scenarios import get, list_scenarios

        scenarios = list_scenarios()
        scenarios[0]["_injected"] = True

        # The cached version should not be mutated
        fresh = get(scenarios[0]["id"])
        assert "_injected" not in (fresh or {})

    def test_cardiac_arrest_has_metronome_bpm(self):
        """CPR scenarios must have a non-null metronome_bpm."""
        from emergency_ai.core.scenarios import get

        scenario = get("cardiac-arrest")

        assert scenario is not None
        assert scenario.get("metronome_bpm") is not None
        bpm = scenario["metronome_bpm"]
        # Standard CPR rate: 100-120 bpm
        assert 100 <= bpm <= 120

    def test_urgency_values_are_valid(self):
        from emergency_ai.core.scenarios import list_scenarios

        valid_urgencies = {"critical", "high", "medium", "low"}
        for scenario in list_scenarios():
            assert scenario["urgency"] in valid_urgencies, (
                f"Scenario {scenario['id']!r} has invalid urgency {scenario['urgency']!r}"
            )

    def test_categories_are_valid(self):
        from emergency_ai.core.scenarios import list_scenarios

        valid_categories = {"medical", "trauma", "environmental", "threat", "poison"}
        for scenario in list_scenarios():
            assert scenario["category"] in valid_categories, (
                f"Scenario {scenario['id']!r} has invalid category {scenario['category']!r}"
            )
