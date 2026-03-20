"""OpenAI Realtime API WebSocket client."""

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterable
from typing import Any, Callable, Awaitable

import aiohttp

from .audio import resample_pcm16

_LOGGER = logging.getLogger(__name__)

REALTIME_URL = "wss://api.openai.com/v1/realtime"
REALTIME_INPUT_RATE = 24000


ToolHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_entity_state",
        "description": "Get the current state of a Home Assistant entity",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "The entity ID (e.g. switch.s4, media_player.bedroom)",
                },
            },
            "required": ["entity_id"],
        },
    },
    {
        "type": "function",
        "name": "call_service",
        "description": "Call a Home Assistant service to control devices",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Service domain (e.g. switch, media_player, cover)",
                },
                "service": {
                    "type": "string",
                    "description": "Service name (e.g. turn_on, turn_off, volume_set)",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Target entity ID",
                },
                "data": {
                    "type": "object",
                    "description": "Additional service data",
                },
            },
            "required": ["domain", "service"],
        },
    },
    {
        "type": "function",
        "name": "get_entities_by_domain",
        "description": "List all entities in a specific domain",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Domain to list (e.g. switch, media_player, cover)",
                },
            },
            "required": ["domain"],
        },
    },
    {
        "type": "function",
        "name": "batch_call_services",
        "description": "Call multiple Home Assistant services simultaneously. Use this to execute several actions at once, e.g. turn on all lights, set multiple volumes, etc.",
        "parameters": {
            "type": "object",
            "properties": {
                "calls": {
                    "type": "array",
                    "description": "List of service calls to execute in parallel",
                    "items": {
                        "type": "object",
                        "properties": {
                            "domain": {
                                "type": "string",
                                "description": "Service domain (e.g. switch, light, media_player)",
                            },
                            "service": {
                                "type": "string",
                                "description": "Service name (e.g. turn_on, turn_off)",
                            },
                            "entity_id": {
                                "type": "string",
                                "description": "Target entity ID",
                            },
                            "data": {
                                "type": "object",
                                "description": "Additional service data",
                            },
                        },
                        "required": ["domain", "service"],
                    },
                },
            },
            "required": ["calls"],
        },
    },
]


async def process_audio_stream(
    *,
    api_key: str,
    audio_stream: AsyncIterable[bytes],
    input_sample_rate: int = 16000,
    model: str = "gpt-4o-realtime-preview",
    voice: str = "marin",
    instructions: str = "",
    tool_handler: ToolHandler | None = None,
) -> tuple[str, bytes]:
    """Stream audio to OpenAI Realtime and return transcript + response audio.

    Args:
        api_key: OpenAI API key.
        audio_stream: Async iterable of PCM16 LE audio chunks.
        input_sample_rate: Sample rate of incoming audio.
        model: OpenAI Realtime model name.
        voice: Voice for the response.
        instructions: System prompt.
        tool_handler: Async callback(name, args) -> result for tool calls.

    Returns:
        (transcript, response_pcm16_24khz)
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Beta": "realtime=v1",
    }

    tools = list(TOOL_DEFINITIONS) if tool_handler else []

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            f"{REALTIME_URL}?model={model}",
            headers=headers,
            heartbeat=30,
        ) as ws:
            # Configure session
            await ws.send_json({
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": instructions,
                    "voice": voice,
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 500,
                    },
                    "tools": tools,
                },
            })

            # Stream audio in background
            send_done = asyncio.Event()

            async def _send_audio():
                try:
                    async for chunk in audio_stream:
                        if not chunk:
                            continue
                        resampled = resample_pcm16(
                            chunk, input_sample_rate, REALTIME_INPUT_RATE
                        )
                        await ws.send_json({
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(resampled).decode(),
                        })
                except Exception:
                    _LOGGER.info("Audio stream ended", exc_info=True)
                finally:
                    _LOGGER.info("Audio send complete, committing buffer")
                    try:
                        await ws.send_json({"type": "input_audio_buffer.commit"})
                    except Exception:
                        pass
                    send_done.set()

            sender = asyncio.create_task(_send_audio())

            # Collect response
            transcript = ""
            audio_parts: list[bytes] = []

            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        event = json.loads(msg.data)
                        etype = event.get("type", "")

                        if etype not in ("response.audio.delta",):
                            _LOGGER.info("Realtime event: %s %s", etype,
                                         {k: v for k, v in event.items()
                                          if k not in ("type", "delta", "audio")}
                                         if etype in ("response.done", "error", "response.audio_transcript.done")
                                         else "")

                        if etype == "response.audio.delta":
                            audio_parts.append(
                                base64.b64decode(event["delta"])
                            )
                        elif etype == "response.audio_transcript.done":
                            transcript = event.get("transcript", transcript)
                        elif etype == "response.function_call_arguments.done":
                            await _handle_tool_call(
                                ws, event, tool_handler
                            )
                        elif etype == "response.done":
                            response = event.get("response", {})
                            # Check if all outputs are function calls
                            # (no audio yet) — keep listening for next response
                            outputs = response.get("output", [])
                            has_audio = any(
                                o.get("type") == "message" for o in outputs
                            )
                            if has_audio or not outputs:
                                break
                        elif etype == "error":
                            _LOGGER.error(
                                "Realtime API error: %s", event.get("error")
                            )
                            break
                        elif etype == "session.updated":
                            _LOGGER.debug("Session configured")

                    elif msg.type in (
                        aiohttp.WSMsgType.ERROR,
                        aiohttp.WSMsgType.CLOSED,
                    ):
                        break
            finally:
                sender.cancel()
                try:
                    await sender
                except asyncio.CancelledError:
                    pass

            return transcript, b"".join(audio_parts)


async def _handle_tool_call(
    ws: aiohttp.ClientWebSocketResponse,
    event: dict[str, Any],
    tool_handler: ToolHandler | None,
) -> None:
    """Execute a tool call and send the result back."""
    name = event.get("name", "")
    call_id = event.get("call_id", "")
    try:
        args = json.loads(event.get("arguments", "{}"))
    except json.JSONDecodeError:
        args = {}

    if tool_handler is None:
        result = {"error": "No tool handler configured"}
    else:
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
