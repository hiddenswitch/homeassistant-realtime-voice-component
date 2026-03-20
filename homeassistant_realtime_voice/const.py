"""Shared constants and defaults."""

DOMAIN = "homeassistant_realtime_voice_component"

CONF_OPENAI_API_KEY = "openai_api_key"
CONF_VOICE = "voice"
CONF_SYSTEM_PROMPT = "system_prompt"
CONF_MODEL = "model"

DEFAULT_VOICE = "marin"
DEFAULT_MODEL = "gpt-4o-realtime-preview"

DEFAULT_SYSTEM_PROMPT = """\
You are a smart home voice assistant. You control lights, speakers, blinds, \
and other devices.

Rules:
- Be concise. One or two sentences max.
- Execute actions immediately when asked — don't ask for confirmation.
- Use batch_call_services when controlling multiple devices at once.
- Use call_service for single actions. Use get_entity_state to check status."""

# Domains to include in dynamic system prompt
PROMPT_DOMAINS = (
    "switch", "media_player", "cover", "climate", "light", "scene",
    "weather", "person",
)
PROMPT_SKIP_PATTERNS = ("crossfade", "loudness")
