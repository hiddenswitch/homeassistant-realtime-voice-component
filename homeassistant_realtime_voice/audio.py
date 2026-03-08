"""PCM audio resampling utilities."""

import struct


def resample_pcm16(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample 16-bit mono PCM using linear interpolation.

    Args:
        data: Input PCM16 little-endian bytes.
        src_rate: Source sample rate in Hz.
        dst_rate: Destination sample rate in Hz.

    Returns:
        Resampled PCM16 little-endian bytes.
    """
    if src_rate == dst_rate:
        return data

    n_samples = len(data) // 2
    if n_samples == 0:
        return b""

    samples = struct.unpack(f"<{n_samples}h", data)
    ratio = src_rate / dst_rate
    n_out = int(n_samples / ratio)
    out = []
    for i in range(n_out):
        src_pos = i * ratio
        idx = int(src_pos)
        frac = src_pos - idx
        if idx + 1 < n_samples:
            val = samples[idx] * (1 - frac) + samples[idx + 1] * frac
        else:
            val = samples[idx]
        out.append(max(-32768, min(32767, int(val))))

    return struct.pack(f"<{len(out)}h", *out)


def pcm16_to_wav(data: bytes, sample_rate: int = 24000, channels: int = 1) -> bytes:
    """Wrap raw PCM16 data in a WAV header."""
    bits_per_sample = 16
    byte_rate = sample_rate * channels * (bits_per_sample // 8)
    block_align = channels * (bits_per_sample // 8)
    data_size = len(data)

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + data
