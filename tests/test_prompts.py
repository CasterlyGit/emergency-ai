"""Prompt assembly tests — proves the cache_control block is on city context."""

from __future__ import annotations

from emergency_ai.core.cities import UNKNOWN_CITY_CONTEXT, load_cities
from emergency_ai.core.prompts import SYSTEM_INSTRUCTIONS, build_system_blocks


def test_two_blocks_returned():
    cities = load_cities()
    blocks = build_system_blocks(cities["new-york"])
    assert len(blocks) == 2


def test_first_block_is_instructions_no_cache():
    cities = load_cities()
    blocks = build_system_blocks(cities["new-york"])
    assert blocks[0]["text"] == SYSTEM_INSTRUCTIONS
    assert "cache_control" not in blocks[0]


def test_second_block_has_ephemeral_cache_control():
    cities = load_cities()
    blocks = build_system_blocks(cities["london"])
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}


def test_second_block_contains_city_context():
    cities = load_cities()
    blocks = build_system_blocks(cities["mumbai"])
    text = blocks[1]["text"]
    assert "Mumbai" in text
    assert "India" in text
    assert "112" in text
    # Body content should be there
    assert "108" in text  # Mumbai ambulance


def test_unknown_city_still_produces_valid_blocks():
    blocks = build_system_blocks(UNKNOWN_CITY_CONTEXT)
    assert len(blocks) == 2
    assert "cache_control" in blocks[1]
    assert "No city-specific" in blocks[1]["text"]
