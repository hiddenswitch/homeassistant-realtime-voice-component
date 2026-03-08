"""TTS provider — returns pre-generated Realtime audio."""

from typing import Any

from homeassistant.components.tts import Provider, TtsAudioType
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import DiscoveryInfoType

from .audio import pcm16_to_wav
from .const import DOMAIN


async def async_get_engine(
    hass: HomeAssistant, config: dict[str, Any],
    discovery_info: DiscoveryInfoType | None = None,
) -> "RealtimeTTS":
    return RealtimeTTS(hass)


class RealtimeTTS(Provider):
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.name = "OpenAI Realtime TTS"

    @property
    def supported_languages(self) -> list[str]:
        return ["en", "de", "fr", "es", "it", "pt", "nl", "ja", "ko", "zh"]

    @property
    def default_language(self) -> str:
        return "en"

    def get_tts_audio(
        self, message: str, language: str, options: dict[str, Any],
    ) -> TtsAudioType:
        data = self.hass.data.get(DOMAIN) or {}
        audio: bytes | None = data.pop("_response_audio", None)
        if not audio:
            return (None, None)
        return ("wav", pcm16_to_wav(audio, sample_rate=24000))
