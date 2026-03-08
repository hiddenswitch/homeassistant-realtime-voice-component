"""CLI for Voice PE provisioning and HA pipeline setup."""

from __future__ import annotations

import asyncio
import logging
import os

import typer

app = typer.Typer(help="Home Assistant Realtime Voice tools")


@app.command()
def provision(
    ssid: str = typer.Option(..., help="2.4 GHz WiFi SSID"),
    password: str = typer.Option(..., help="WiFi password"),
    ha_url: str = typer.Option(..., envvar="HA_URL", help="HA URL (e.g. http://192.168.88.210:8123)"),
    ha_token: str = typer.Option(..., envvar="HA_TOKEN", help="HA long-lived access token"),
    device_name: str | None = typer.Option(None, help="Specific BLE device name"),
    scan_timeout: float = typer.Option(15.0, help="BLE scan timeout seconds"),
    skip_provision: bool = typer.Option(False, help="Skip WiFi provisioning"),
    skip_pipeline: bool = typer.Option(False, help="Skip pipeline creation"),
    pipeline_name: str = typer.Option("OpenAI Realtime", help="Assist pipeline name"),
) -> None:
    """Provision a Voice PE device: WiFi via BLE, adopt in HA, create pipeline."""
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(_provision(
        ssid=ssid, password=password, ha_url=ha_url, ha_token=ha_token,
        device_name=device_name, scan_timeout=scan_timeout,
        skip_provision=skip_provision, skip_pipeline=skip_pipeline,
        pipeline_name=pipeline_name,
    ))


async def _provision(
    *,
    ssid: str,
    password: str,
    ha_url: str,
    ha_token: str,
    device_name: str | None,
    scan_timeout: float,
    skip_provision: bool,
    skip_pipeline: bool,
    pipeline_name: str,
) -> None:
    from .provision import (
        VOICE_PE_BLE_PREFIX,
        scan_for_voice_pe,
        provision_wifi,
        resolve_device_ip,
    )
    from .pipeline import create_pipeline, assign_pipeline_to_satellite

    logger = logging.getLogger(__name__)
    device_id: str | None = None

    if not skip_provision:
        devices = await scan_for_voice_pe(timeout=scan_timeout)
        if not devices:
            raise SystemExit("No Voice PE devices found. Is it powered on and in setup mode?")

        if device_name:
            match = [(a, n) for a, n in devices if n == device_name]
            if not match:
                raise SystemExit(f"Device {device_name} not found. Available: {[n for _, n in devices]}")
            ble_address, ble_name = match[0]
        else:
            ble_address, ble_name = devices[0]
            if len(devices) > 1:
                logger.warning("Multiple devices found, using first: %s", ble_name)

        device_id = ble_name.removeprefix(VOICE_PE_BLE_PREFIX)
        await provision_wifi(ble_address, ble_name, ssid, password)
        logger.debug("Waiting for device to connect to WiFi...")
        await asyncio.sleep(5)
    else:
        if not device_name:
            raise SystemExit("--device-name required when using --skip-provision")
        device_id = device_name.removeprefix(VOICE_PE_BLE_PREFIX)

    # Resolve IP and adopt
    device_ip = resolve_device_ip(device_id)
    if not device_ip:
        raise SystemExit("Could not resolve device IP. Check WiFi credentials.")

    import aiohttp
    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{ha_url}/api/config/config_entries/flow",
            headers=headers,
            json={"handler": "esphome", "show_advanced_options": False},
        ) as resp:
            data = await resp.json()
            flow_id = data.get("flow_id")
            if not flow_id:
                raise SystemExit(f"Failed to start ESPHome config flow: {data}")

        async with session.post(
            f"{ha_url}/api/config/config_entries/flow/{flow_id}",
            headers=headers,
            json={"host": device_ip, "port": 6053},
        ) as resp:
            data = await resp.json()
            if data.get("type") != "create_entry":
                raise SystemExit(f"Failed to adopt device: {data}")
            logger.info("Device adopted: %s", data.get("title"))

    if not skip_pipeline:
        pipeline_id = await create_pipeline(
            ha_url, ha_token, pipeline_name=pipeline_name,
        )
        if pipeline_id:
            await assign_pipeline_to_satellite(ha_url, ha_token, pipeline_name=pipeline_name)
        else:
            logger.warning("Pipeline setup failed")

    logger.info('Done. Say "Okay Nabu" to start talking.')


@app.command()
def setup_pipeline(
    ha_url: str = typer.Option(..., envvar="HA_URL", help="HA URL"),
    ha_token: str = typer.Option(..., envvar="HA_TOKEN", help="HA long-lived access token"),
    pipeline_name: str = typer.Option("OpenAI Realtime", help="Pipeline name"),
    assign: bool = typer.Option(True, help="Assign to Voice PE satellite"),
) -> None:
    """Create an assist pipeline with Realtime STT/TTS and assign to Voice PE."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(_setup_pipeline(ha_url, ha_token, pipeline_name, assign))


async def _setup_pipeline(ha_url: str, ha_token: str, name: str, assign: bool) -> None:
    from .pipeline import create_pipeline, assign_pipeline_to_satellite

    pipeline_id = await create_pipeline(ha_url, ha_token, pipeline_name=name)
    if not pipeline_id:
        raise SystemExit("Failed to create pipeline")

    if assign:
        await assign_pipeline_to_satellite(ha_url, ha_token, pipeline_name=name)
