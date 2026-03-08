"""Tests for dynamic prompt builder."""

from homeassistant_realtime_voice.prompt import build_dynamic_prompt


def test_empty_states():
    prompt = build_dynamic_prompt([], base_prompt="Hello")
    assert "Hello" in prompt
    assert "(no devices)" in prompt


def test_filters_domains():
    states = [
        {"entity_id": "switch.lamp", "state": "on", "attributes": {"friendly_name": "Lamp"}},
        {"entity_id": "automation.foo", "state": "on", "attributes": {}},
    ]
    prompt = build_dynamic_prompt(states)
    assert "switch.lamp" in prompt
    assert "automation.foo" not in prompt


def test_skips_crossfade():
    states = [
        {"entity_id": "switch.bedroom_crossfade", "state": "on", "attributes": {}},
        {"entity_id": "switch.s4", "state": "off", "attributes": {"friendly_name": "Corner"}},
    ]
    prompt = build_dynamic_prompt(states)
    assert "switch.bedroom_crossfade" not in prompt
    assert "switch.s4" in prompt


def test_media_player_extras():
    states = [{
        "entity_id": "media_player.bedroom",
        "state": "playing",
        "attributes": {
            "friendly_name": "Bedroom",
            "media_title": "Rain",
            "volume_level": 0.42,
            "group_members": ["a", "b"],
            "repeat": "all",
        },
    }]
    prompt = build_dynamic_prompt(states)
    assert "playing 'Rain'" in prompt
    assert "vol=42%" in prompt
    assert "grouped=2" in prompt
    assert "repeat=all" in prompt


def test_cover_position():
    states = [{
        "entity_id": "cover.blind",
        "state": "open",
        "attributes": {"friendly_name": "Blind", "current_position": 75},
    }]
    prompt = build_dynamic_prompt(states)
    assert "position=75%" in prompt
