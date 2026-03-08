"""OpenAI Realtime Voice — thin HA integration stub."""

import logging
import os

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, discovery
from homeassistant.helpers.typing import ConfigType

from homeassistant_realtime_voice.const import (
    CONF_MODEL, CONF_OPENAI_API_KEY, CONF_SYSTEM_PROMPT, CONF_VOICE,
    DEFAULT_MODEL, DEFAULT_SYSTEM_PROMPT, DEFAULT_VOICE, DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Optional(CONF_OPENAI_API_KEY): cv.string,
        vol.Optional(CONF_VOICE, default=DEFAULT_VOICE): cv.string,
        vol.Optional(CONF_MODEL, default=DEFAULT_MODEL): cv.string,
        vol.Optional(CONF_SYSTEM_PROMPT, default=DEFAULT_SYSTEM_PROMPT): cv.string,
    })
}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    if DOMAIN not in config:
        return True
    conf = config[DOMAIN]
    api_key = conf.get(CONF_OPENAI_API_KEY) or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        _LOGGER.error("No OpenAI API key (set in YAML or OPENAI_API_KEY env var)")
        return False
    hass.data[DOMAIN] = {
        CONF_OPENAI_API_KEY: api_key,
        CONF_VOICE: conf[CONF_VOICE],
        CONF_MODEL: conf[CONF_MODEL],
        CONF_SYSTEM_PROMPT: conf[CONF_SYSTEM_PROMPT],
    }
    hass.async_create_task(
        discovery.async_load_platform(hass, "stt", DOMAIN, {}, config))
    hass.async_create_task(
        discovery.async_load_platform(hass, "tts", DOMAIN, {}, config))
    return True
