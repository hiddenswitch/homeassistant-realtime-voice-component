"""Tests for the OpenAI Realtime WebSocket client.

Uses aiohttp to spin up a mock WebSocket server in-process.
Real Voice PE audio fixtures can be added to tests/fixtures/ once captured.
"""

import asyncio
import base64
import json
import struct
from pathlib import Path

import pytest
import aiohttp
from aiohttp import web

from homeassistant_realtime_voice.audio import resample_pcm16

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_pcm16(n_samples: int = 100, value: int = 0) -> bytes:
    return struct.pack(f"<{n_samples}h", *([value] * n_samples))


async def _mock_realtime_handler(request: web.Request) -> web.WebSocketResponse:
    """Mock OpenAI Realtime API WebSocket handler.

    Simulates server VAD: after receiving audio, waits briefly then sends a
    response as if speech was detected and completed.
    """
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    audio_received = False

    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            event = json.loads(msg.data)
            etype = event.get("type", "")

            if etype == "session.update":
                await ws.send_json({"type": "session.updated", "session": event["session"]})

            elif etype == "input_audio_buffer.append":
                if not audio_received:
                    audio_received = True
                    # Simulate server VAD detecting speech end after a short delay
                    asyncio.get_event_loop().call_later(
                        0.05, asyncio.ensure_future, _send_response(ws)
                    )

        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
            break

    return ws


async def _send_response(ws: web.WebSocketResponse) -> None:
    """Send a mock response sequence."""
    try:
        response_audio = _make_pcm16(50, 1000)
        await ws.send_json({
            "type": "response.audio.delta",
            "delta": base64.b64encode(response_audio).decode(),
        })
        await ws.send_json({
            "type": "response.audio_transcript.done",
            "transcript": "Hello from mock",
        })
        await ws.send_json({
            "type": "response.done",
            "response": {
                "output": [{"type": "message"}],
            },
        })
    except Exception:
        pass  # WebSocket may have closed


@pytest.mark.asyncio
async def test_process_audio_stream_basic():
    """Test basic audio processing flow with mock server."""
    app = web.Application()
    app.router.add_get("/v1/realtime", _mock_realtime_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()

    port = site._server.sockets[0].getsockname()[1]

    import homeassistant_realtime_voice.realtime as rt
    original_url = rt.REALTIME_URL
    rt.REALTIME_URL = f"http://127.0.0.1:{port}/v1/realtime"

    try:
        audio_data = _make_pcm16(480)  # 30ms at 16kHz

        async def audio_gen():
            yield audio_data

        transcript, response_audio = await rt.process_audio_stream(
            api_key="test-key",
            audio_stream=audio_gen(),
            input_sample_rate=16000,
            model="test",
            voice="marin",
            instructions="test",
        )

        assert transcript == "Hello from mock"
        assert len(response_audio) > 0
    finally:
        rt.REALTIME_URL = original_url
        await runner.cleanup()


def test_resample_round_trip():
    """16kHz -> 24kHz -> 16kHz should roughly preserve the signal."""
    original = _make_pcm16(160, 5000)  # 10ms at 16kHz
    upsampled = resample_pcm16(original, 16000, 24000)
    downsampled = resample_pcm16(upsampled, 24000, 16000)
    assert len(downsampled) // 2 == len(original) // 2
    orig_samples = struct.unpack(f"<{len(original)//2}h", original)
    down_samples = struct.unpack(f"<{len(downsampled)//2}h", downsampled)
    for a, b in zip(orig_samples, down_samples):
        assert abs(a - b) < 100, f"Sample drift too large: {a} vs {b}"
