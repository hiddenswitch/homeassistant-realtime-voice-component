# homeassistant-realtime-voice-component

HA custom component that uses OpenAI's Realtime API for voice control. Registers as both STT and TTS so stock Voice PE firmware works unchanged.

Copy `homeassistant_realtime_voice_component/` into your HA `custom_components/` directory. HA will pip install the Python package automatically via `manifest.json`.

Set `OPENAI_API_KEY` as an environment variable, or add `openai_api_key` to `configuration.yaml`:

```yaml
homeassistant_realtime_voice_component:
  voice: "marin"           # optional
  model: "gpt-4o-realtime-preview"  # optional
  system_prompt: |          # optional
    You are a smart home voice assistant...
```

##### CLI

```bash
pip install "homeassistant-realtime-voice[cli]"

# Provision a new Voice PE (WiFi via BLE + adopt in HA + create pipeline)
ha-realtime-voice provision --ssid "MyWiFi" --password "secret" \
    --ha-url http://your-ha:8123 --ha-token TOKEN

# Create/assign the assist pipeline
ha-realtime-voice setup-pipeline --ha-url http://your-ha:8123 --ha-token TOKEN
```

##### Docker

```dockerfile
FROM ghcr.io/home-assistant/home-assistant:stable
RUN pip install --no-cache-dir \
    "homeassistant-realtime-voice @ git+https://github.com/HiddenSwitch/homeassistant-realtime-voice-component.git"
RUN pip install --no-cache-dir --no-deps \
    --target /opt/rv-stubs \
    "homeassistant-realtime-voice @ git+https://github.com/HiddenSwitch/homeassistant-realtime-voice-component.git"
RUN printf '#!/bin/sh\nmkdir -p /config/custom_components\ncp -r /opt/rv-stubs/homeassistant_realtime_voice_component /config/custom_components/\n' \
    > /etc/cont-init.d/install-realtime-voice \
    && chmod +x /etc/cont-init.d/install-realtime-voice
```
