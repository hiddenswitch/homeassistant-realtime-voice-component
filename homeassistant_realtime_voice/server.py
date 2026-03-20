"""Minimal full-duplex WebSocket bridge: Voice PE <-> OpenAI Realtime API.

Accepts binary PCM16 24kHz audio from the Voice PE's voice_assistant_websocket
component, forwards it to OpenAI Realtime, and streams response audio back.
Tool calls are executed against the HA REST API.
"""

import asyncio
import base64
import json
import logging
import os
import signal
from typing import Any

import aiohttp
from aiohttp import web

from .const import DEFAULT_MODEL, DEFAULT_SYSTEM_PROMPT, DEFAULT_VOICE
from .prompt import build_dynamic_prompt
from .tools import execute_tool

_LOGGER = logging.getLogger(__name__)

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime"


async def _fetch_ha_states(ha_url: str, ha_token: str) -> list[dict[str, Any]]:
    """Fetch all entity states from HA REST API."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{ha_url}/api/states",
            headers={"Authorization": f"Bearer {ha_token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            raw = await resp.json()
    return [
        {"entity_id": s["entity_id"], "state": s["state"],
         "attributes": s.get("attributes", {})}
        for s in raw
    ]


def _make_tool_handler(ha_url: str, ha_token: str):
    """Create a tool handler that calls HA REST API."""
    async def handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
        async def call_service(domain, service, entity_id, data):
            url = f"{ha_url}/api/services/{domain}/{service}"
            payload = dict(data or {})
            if entity_id:
                payload["entity_id"] = entity_id
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload,
                    headers={"Authorization": f"Bearer {ha_token}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    resp.raise_for_status()

        async def get_state(entity_id):
            url = f"{ha_url}/api/states/{entity_id}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers={"Authorization": f"Bearer {ha_token}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 404:
                        return None
                    resp.raise_for_status()
                    s = await resp.json()
                    return {"entity_id": s["entity_id"], "state": s["state"],
                            "attributes": s.get("attributes", {})}

        async def list_states(domain):
            states = await _fetch_ha_states(ha_url, ha_token)
            return [
                {"entity_id": s["entity_id"], "state": s["state"],
                 "friendly_name": s["attributes"].get("friendly_name", s["entity_id"])}
                for s in states if s["entity_id"].startswith(f"{domain}.")
            ]

        return await execute_tool(
            name, args,
            call_service=call_service, get_state=get_state, list_states=list_states,
        )
    return handler


async def _bridge(device_ws: web.WebSocketResponse, request: web.Request) -> None:
    """Bridge a single Voice PE WebSocket connection to OpenAI Realtime."""
    api_key = request.app["openai_api_key"]
    ha_url = request.app["ha_url"]
    ha_token = request.app["ha_token"]
    model = request.app.get("model", DEFAULT_MODEL)
    voice = request.app.get("voice", DEFAULT_VOICE)
    base_prompt = request.app.get("system_prompt", DEFAULT_SYSTEM_PROMPT)

    tool_handler = _make_tool_handler(ha_url, ha_token)

    # Build dynamic prompt with current HA state
    try:
        states = await _fetch_ha_states(ha_url, ha_token)
        instructions = build_dynamic_prompt(states, base_prompt)
    except Exception:
        _LOGGER.warning("Failed to fetch HA states for prompt", exc_info=True)
        instructions = base_prompt

    from .realtime import TOOL_DEFINITIONS

    headers = {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Beta": "realtime=v1",
    }

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            f"{OPENAI_REALTIME_URL}?model={model}",
            headers=headers,
            heartbeat=30,
        ) as openai_ws:
            _LOGGER.info("Connected to OpenAI Realtime API")

            # Configure session
            await openai_ws.send_json({
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": instructions,
                    "voice": voice,
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.8,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 700,
                    },
                    "tools": list(TOOL_DEFINITIONS),
                },
            })

            # Forward device audio -> OpenAI
            audio_bytes_received = 0
            async def device_to_openai():
                nonlocal audio_bytes_received
                try:
                    async for msg in device_ws:
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            audio_bytes_received += len(msg.data)
                            if audio_bytes_received <= len(msg.data):
                                _LOGGER.info("First audio chunk from device: %d bytes", len(msg.data))
                            elif audio_bytes_received % 50000 < len(msg.data):
                                _LOGGER.info("Audio received so far: %d bytes", audio_bytes_received)
                            await openai_ws.send_json({
                                "type": "input_audio_buffer.append",
                                "audio": base64.b64encode(msg.data).decode(),
                            })
                        elif msg.type == aiohttp.WSMsgType.TEXT:
                            # Handle control messages from device
                            try:
                                ctrl = json.loads(msg.data)
                                if ctrl.get("type") == "interrupt":
                                    _LOGGER.info("Interrupt from device")
                                    await openai_ws.send_json(
                                        {"type": "response.cancel"}
                                    )
                            except json.JSONDecodeError:
                                pass
                        elif msg.type in (
                            aiohttp.WSMsgType.ERROR,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.CLOSING,
                        ):
                            break
                except Exception:
                    _LOGGER.debug("device_to_openai ended", exc_info=True)
                finally:
                    _LOGGER.info("Device audio stream ended")

            # Forward OpenAI audio -> device
            audio_bytes_sent = 0
            response_active = False
            async def openai_to_device():
                nonlocal audio_bytes_sent, response_active
                try:
                    async for msg in openai_ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            event = json.loads(msg.data)
                            etype = event.get("type", "")

                            if etype == "response.audio.delta":
                                audio = base64.b64decode(event["delta"])
                                audio_bytes_sent += len(audio)
                                response_active = True
                                if audio_bytes_sent == len(audio):
                                    _LOGGER.info("First audio delta to device: %d bytes", len(audio))
                                await device_ws.send_bytes(audio)
                            elif etype == "input_audio_buffer.speech_started":
                                if response_active:
                                    # Ignore speech_started during playback — it's echo
                                    _LOGGER.debug("Ignoring speech_started during active response (likely echo)")
                                else:
                                    _LOGGER.info("OpenAI detected speech start (no active response)")
                            elif etype == "input_audio_buffer.speech_stopped":
                                _LOGGER.info("OpenAI detected speech stop")
                            elif etype == "response.audio.done":
                                response_active = False
                                _LOGGER.info("OpenAI audio response complete, sent %d bytes to device", audio_bytes_sent)
                            elif etype == "response.function_call_arguments.done":
                                await _handle_tool_call(
                                    openai_ws, event, tool_handler
                                )
                            elif etype == "error":
                                _LOGGER.error("OpenAI error: %s", event.get("error"))
                            elif etype == "session.updated":
                                _LOGGER.info("OpenAI session configured")
                        elif msg.type in (
                            aiohttp.WSMsgType.ERROR,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.CLOSING,
                        ):
                            break
                except Exception:
                    _LOGGER.debug("openai_to_device ended", exc_info=True)
                finally:
                    _LOGGER.info("OpenAI stream ended")

            # Run both directions concurrently
            tasks = [
                asyncio.create_task(device_to_openai()),
                asyncio.create_task(openai_to_device()),
            ]
            # Wait for either to finish (device disconnect or OpenAI close)
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass


async def _handle_tool_call(
    ws: aiohttp.ClientWebSocketResponse,
    event: dict[str, Any],
    tool_handler,
) -> None:
    """Execute a tool call and send the result back to OpenAI."""
    name = event.get("name", "")
    call_id = event.get("call_id", "")
    try:
        args = json.loads(event.get("arguments", "{}"))
    except json.JSONDecodeError:
        args = {}

    _LOGGER.info("Tool call: %s(%s)", name, args)
    try:
        result = await tool_handler(name, args)
    except Exception as exc:
        _LOGGER.error("Tool %s failed: %s", name, exc)
        result = {"error": str(exc)}

    await ws.send_json({
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(result),
        },
    })
    await ws.send_json({"type": "response.create"})


async def _ws_handler(request: web.Request) -> web.WebSocketResponse:
    """Handle incoming WebSocket connection from Voice PE."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    peer = request.remote
    _LOGGER.info("Voice PE connected from %s", peer)

    try:
        await _bridge(ws, request)
    except Exception:
        _LOGGER.error("Bridge error", exc_info=True)
    finally:
        _LOGGER.info("Voice PE disconnected from %s", peer)
        if not ws.closed:
            await ws.close()

    return ws


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def create_app(
    *,
    openai_api_key: str,
    ha_url: str,
    ha_token: str,
    model: str = DEFAULT_MODEL,
    voice: str = DEFAULT_VOICE,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> web.Application:
    """Create the aiohttp application."""
    app = web.Application()
    app["openai_api_key"] = openai_api_key
    app["ha_url"] = ha_url
    app["ha_token"] = ha_token
    app["model"] = model
    app["voice"] = voice
    app["system_prompt"] = system_prompt
    app.router.add_get("/", _ws_handler)
    app.router.add_get("/ws", _ws_handler)
    app.router.add_get("/health", _health)
    return app


def main():
    """Entry point for the server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    openai_key = os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        raise SystemExit("OPENAI_API_KEY environment variable required")

    ha_url = os.environ.get("HA_URL", "http://localhost:8123")
    ha_token = os.environ.get("HA_TOKEN", "")
    port = int(os.environ.get("PORT", "8080"))
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    voice = os.environ.get("OPENAI_VOICE", DEFAULT_VOICE)
    system_prompt = os.environ.get("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)

    app = create_app(
        openai_api_key=openai_key,
        ha_url=ha_url,
        ha_token=ha_token,
        model=model,
        voice=voice,
        system_prompt=system_prompt,
    )

    _LOGGER.info("Starting server on port %d", port)
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
