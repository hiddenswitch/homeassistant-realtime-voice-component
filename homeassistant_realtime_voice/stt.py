"""STT provider — streams audio to OpenAI Realtime."""

import logging
from collections.abc import AsyncIterable
from typing import Any

from homeassistant.components.stt import (
    AudioBitRates, AudioChannels, AudioCodecs, AudioFormats, AudioSampleRates,
    Provider, SpeechMetadata, SpeechResult, SpeechResultState,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import DiscoveryInfoType

from .const import CONF_MODEL, CONF_OPENAI_API_KEY, CONF_SYSTEM_PROMPT, CONF_VOICE, DOMAIN
from .prompt import build_dynamic_prompt
from .realtime import process_audio_stream
from .tools import execute_tool

_LOGGER = logging.getLogger(__name__)


async def async_get_engine(
    hass: HomeAssistant, config: dict[str, Any],
    discovery_info: DiscoveryInfoType | None = None,
) -> "RealtimeSTT":
    return RealtimeSTT(hass, hass.data[DOMAIN])


class RealtimeSTT(Provider):
    def __init__(self, hass: HomeAssistant, conf: dict[str, Any]) -> None:
        self.hass = hass
        self.name = "OpenAI Realtime STT"
        self._api_key = conf[CONF_OPENAI_API_KEY]
        self._voice = conf[CONF_VOICE]
        self._model = conf[CONF_MODEL]
        self._base_prompt = conf[CONF_SYSTEM_PROMPT]

    @property
    def supported_languages(self) -> list[str]:
        return ["en", "de", "fr", "es", "it", "pt", "nl", "ja", "ko", "zh"]

    @property
    def supported_formats(self) -> list[AudioFormats]:
        return [AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[AudioCodecs]:
        return [AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[AudioBitRates]:
        return [AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[AudioSampleRates]:
        return [AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self) -> list[AudioChannels]:
        return [AudioChannels.CHANNEL_MONO]

    async def async_process_audio_stream(
        self, metadata: SpeechMetadata, stream: AsyncIterable[bytes],
    ) -> SpeechResult:
        _LOGGER.info(
            "STT process_audio_stream called: format=%s codec=%s sample_rate=%s channels=%s",
            metadata.format, metadata.codec, metadata.sample_rate, metadata.channel,
        )
        total_bytes = 0

        async def _counted_stream():
            nonlocal total_bytes
            async for chunk in stream:
                total_bytes += len(chunk)
                yield chunk
            _LOGGER.info("STT received %d bytes of audio", total_bytes)

        states = [
            {"entity_id": s.entity_id, "state": s.state, "attributes": dict(s.attributes)}
            for s in self.hass.states.async_all()
        ]
        instructions = build_dynamic_prompt(states, self._base_prompt)
        hass = self.hass

        async def tool_handler(name: str, args: dict) -> dict:
            async def _call(domain, service, entity_id, data):
                sd = dict(data or {})
                if entity_id:
                    sd["entity_id"] = entity_id
                await hass.services.async_call(domain, service, sd)

            async def _get(entity_id):
                st = hass.states.get(entity_id)
                if st is None:
                    return None
                return {"entity_id": entity_id, "state": st.state,
                        "attributes": dict(st.attributes)}

            async def _list(domain):
                return [{"entity_id": s.entity_id, "state": s.state,
                         "friendly_name": s.attributes.get("friendly_name", s.entity_id)}
                        for s in hass.states.async_all()
                        if s.entity_id.startswith(f"{domain}.")]

            return await execute_tool(name, args,
                                      call_service=_call, get_state=_get, list_states=_list)

        try:
            transcript, audio = await process_audio_stream(
                api_key=self._api_key, audio_stream=_counted_stream(),
                input_sample_rate=metadata.sample_rate,
                model=self._model, voice=self._voice,
                instructions=instructions, tool_handler=tool_handler,
            )
        except Exception:
            _LOGGER.error("Realtime API failed", exc_info=True)
            return SpeechResult("", SpeechResultState.ERROR)

        _LOGGER.info(
            "STT result: transcript=%r audio_bytes=%d",
            transcript, len(audio) if audio else 0,
        )
        hass.data.setdefault(DOMAIN, {})["_response_audio"] = audio
        return SpeechResult(transcript or "", SpeechResultState.SUCCESS)
