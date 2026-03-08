"""Tests for PCM resampling and WAV conversion."""

import struct
from homeassistant_realtime_voice.audio import resample_pcm16, pcm16_to_wav


def _make_pcm16(samples: list[int]) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def test_resample_same_rate():
    data = _make_pcm16([0, 100, -100, 32767])
    assert resample_pcm16(data, 16000, 16000) == data


def test_resample_upsample():
    # 2 samples at 16kHz → 3 samples at 24kHz (ratio 2:3)
    data = _make_pcm16([0, 3000])
    result = resample_pcm16(data, 16000, 24000)
    samples = struct.unpack(f"<{len(result)//2}h", result)
    assert len(samples) == 3
    assert samples[0] == 0
    assert samples[-1] == 3000


def test_resample_downsample():
    data = _make_pcm16([0, 1000, 2000])
    result = resample_pcm16(data, 24000, 16000)
    samples = struct.unpack(f"<{len(result)//2}h", result)
    assert len(samples) == 2


def test_resample_empty():
    assert resample_pcm16(b"", 16000, 24000) == b""


def test_resample_clipping():
    # Values near int16 limits should be clamped
    data = _make_pcm16([32767, -32768])
    result = resample_pcm16(data, 16000, 24000)
    samples = struct.unpack(f"<{len(result)//2}h", result)
    for s in samples:
        assert -32768 <= s <= 32767


def test_pcm16_to_wav():
    pcm = _make_pcm16([0, 1000, -1000])
    wav = pcm16_to_wav(pcm, sample_rate=24000)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[12:16] == b"fmt "
    assert wav[36:40] == b"data"
    # Data size field
    data_size = struct.unpack_from("<I", wav, 40)[0]
    assert data_size == len(pcm)
    # Actual PCM data at offset 44
    assert wav[44:] == pcm
