"""Dynamic system prompt builder.

Takes a generic list of entity state dicts (no HA dependency) so it can be
tested and used from the CLI without a running Home Assistant instance.
"""

from __future__ import annotations

from typing import Any

from .const import DEFAULT_SYSTEM_PROMPT, PROMPT_DOMAINS, PROMPT_SKIP_PATTERNS


def build_dynamic_prompt(
    states: list[dict[str, Any]],
    base_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> str:
    """Build system prompt with live entity states.

    Args:
        states: List of dicts with keys: entity_id, state, attributes.
        base_prompt: The base instruction text.
    """
    lines: list[str] = []
    for s in sorted(states, key=lambda x: x["entity_id"]):
        eid: str = s["entity_id"]
        domain = eid.split(".")[0]
        if domain not in PROMPT_DOMAINS:
            continue
        if any(p in eid for p in PROMPT_SKIP_PATTERNS):
            continue

        attrs = s.get("attributes") or {}
        name = attrs.get("friendly_name", eid)
        extras: list[str] = []

        if domain == "media_player" and s["state"] == "playing":
            if t := attrs.get("media_title"):
                extras.append(f"playing '{t}'")
            if a := attrs.get("media_artist"):
                extras.append(f"by {a}")
            if g := attrs.get("group_members"):
                extras.append(f"grouped={len(g)} speakers")
            if r := attrs.get("repeat"):
                extras.append(f"repeat={r}")
            if (v := attrs.get("volume_level")) is not None:
                extras.append(f"vol={int(float(v) * 100)}%")
        elif domain == "cover":
            if (pos := attrs.get("current_position")) is not None:
                extras.append(f"position={pos}%")

        extra = ", ".join(extras)
        lines.append(
            f"  {eid}: {s['state']} ({name})"
            + (f" — {extra}" if extra else "")
        )

    state_block = "\n".join(lines) if lines else "  (no devices)"
    return f"""{base_prompt}

## Current Home State
{state_block}

## Available Services
- switch/turn_on, switch/turn_off
- cover/open_cover, cover/close_cover, cover/set_cover_position
- media_player/media_play, media_player/media_pause, media_player/media_stop
- media_player/volume_set, media_player/play_media
- media_player/join, media_player/unjoin
- media_player/repeat_set, media_player/shuffle_set"""
