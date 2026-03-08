"""HA assist pipeline creation via WebSocket API."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


async def create_pipeline(
    ha_url: str,
    ha_token: str,
    pipeline_name: str = "OpenAI Realtime",
    stt_engine: str = "stt.openai_realtime_stt",
    tts_engine: str = "tts.openai_realtime_tts",
    conversation_engine: str = "homeassistant",
    language: str = "en",
    set_preferred: bool = True,
) -> str | None:
    """Create an assist pipeline and optionally set it as preferred.

    Returns the pipeline ID, or None on failure.
    """
    ws_url = ha_url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
    msg_id = 0

    def _next_id() -> int:
        nonlocal msg_id
        msg_id += 1
        return msg_id

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url) as ws:
            # Auth
            await ws.receive_json()  # auth_required
            await ws.send_json({"type": "auth", "access_token": ha_token})
            auth = await ws.receive_json()
            if auth.get("type") != "auth_ok":
                _LOGGER.error("WebSocket auth failed: %s", auth)
                return None

            # Check if pipeline already exists
            await ws.send_json({"id": _next_id(), "type": "assist_pipeline/pipeline/list"})
            resp = await ws.receive_json()
            for p in resp.get("result", {}).get("pipelines", []):
                if p["name"] == pipeline_name:
                    _LOGGER.info("Pipeline '%s' already exists: %s", pipeline_name, p["id"])
                    if set_preferred:
                        await _set_preferred(ws, _next_id, p["id"])
                    return p["id"]

            # Create pipeline
            await ws.send_json({
                "id": _next_id(),
                "type": "assist_pipeline/pipeline/create",
                "conversation_engine": conversation_engine,
                "conversation_language": language,
                "language": language,
                "name": pipeline_name,
                "stt_engine": stt_engine,
                "stt_language": language,
                "tts_engine": tts_engine,
                "tts_language": language,
            })
            resp = await ws.receive_json()
            if not resp.get("success"):
                _LOGGER.error("Failed to create pipeline: %s", resp)
                return None

            pipeline_id = resp["result"]["id"]
            _LOGGER.info("Pipeline created: %s", pipeline_id)

            if set_preferred:
                await _set_preferred(ws, _next_id, pipeline_id)

            return pipeline_id


async def assign_pipeline_to_satellite(
    ha_url: str,
    ha_token: str,
    pipeline_name: str = "OpenAI Realtime",
    satellite_prefix: str = "select.home_assistant_voice",
) -> bool:
    """Find the Voice PE's pipeline selector entity and assign the pipeline."""
    headers = {"Authorization": f"Bearer {ha_token}"}
    async with aiohttp.ClientSession(headers=headers) as session:
        # Get all states to find the pipeline selector and pipeline ID
        async with session.get(f"{ha_url}/api/states") as resp:
            if resp.status != 200:
                _LOGGER.error("Failed to get states: %s", resp.status)
                return False
            all_states = await resp.json()

        # Find the Voice PE pipeline selector entity
        selector = None
        for s in all_states:
            eid = s["entity_id"]
            if eid.startswith(satellite_prefix) and eid.endswith("_pipeline"):
                selector = s
                break

        if not selector:
            _LOGGER.error("No Voice PE pipeline selector found (prefix: %s)", satellite_prefix)
            return False

        # Check if pipeline_name is in the options
        options = selector.get("attributes", {}).get("options", [])
        if pipeline_name not in options:
            _LOGGER.error(
                "Pipeline '%s' not in options for %s: %s",
                pipeline_name, selector["entity_id"], options,
            )
            return False

        # Set the pipeline
        async with session.post(
            f"{ha_url}/api/services/select/select_option",
            json={"entity_id": selector["entity_id"], "option": pipeline_name},
        ) as resp:
            if resp.status == 200:
                _LOGGER.info("Assigned '%s' to %s", pipeline_name, selector["entity_id"])
                return True
            _LOGGER.error("Failed to assign pipeline: %s", await resp.text())
            return False


async def _set_preferred(ws, next_id, pipeline_id: str) -> None:
    await ws.send_json({
        "id": next_id(),
        "type": "assist_pipeline/pipeline/set_preferred",
        "pipeline_id": pipeline_id,
    })
    await ws.receive_json()
    _LOGGER.info("Set as preferred pipeline")
