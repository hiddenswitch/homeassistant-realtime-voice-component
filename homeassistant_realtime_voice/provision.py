"""Voice PE BLE provisioning via Improv WiFi protocol."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Improv BLE protocol UUIDs
IMPROV_SERVICE_UUID = "00467768-6228-2272-4663-277478268000"
IMPROV_CURRENT_STATE = "00467768-6228-2272-4663-277478268001"
IMPROV_ERROR_STATE = "00467768-6228-2272-4663-277478268002"
IMPROV_RPC_COMMAND = "00467768-6228-2272-4663-277478268003"
IMPROV_RPC_RESULT = "00467768-6228-2272-4663-277478268004"

IMPROV_STATE_NAMES = {
    1: "Authorization Required",
    2: "Authorized",
    3: "Provisioning",
    4: "Provisioned",
}
IMPROV_ERROR_NAMES = {
    0: "None", 1: "Invalid RPC", 2: "Unknown Command",
    3: "Connection Failed", 4: "Not Authorized", 5: "Bad Hostname",
    0xFF: "Unknown",
}

VOICE_PE_BLE_PREFIX = "ha-voice-pe-"
ESPHOME_MDNS_PREFIX = "home-assistant-voice-"


def _build_wifi_command(ssid: str, password: str) -> bytes:
    ssid_bytes = ssid.encode("utf-8")
    pass_bytes = password.encode("utf-8")
    data = bytes([len(ssid_bytes)]) + ssid_bytes + bytes([len(pass_bytes)]) + pass_bytes
    payload = bytes([0x01, len(data)]) + data
    return payload + bytes([sum(payload) & 0xFF])


async def scan_for_voice_pe(timeout: float = 15.0) -> list[tuple[str, str]]:
    """Scan BLE for Voice PE devices. Returns list of (address, name)."""
    from bleak import BleakScanner

    _LOGGER.debug("Scanning BLE for Voice PE devices (%ss)...", timeout)
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    found = []
    for addr, (device, adv) in devices.items():
        name = device.name or adv.local_name or ""
        if name.startswith(VOICE_PE_BLE_PREFIX):
            found.append((addr, name))
            _LOGGER.debug("  Found: %s (%s)", name, addr)
    return found


async def provision_wifi(
    ble_address: str,
    ble_name: str,
    ssid: str,
    password: str,
    button_timeout: float = 60.0,
) -> list[str]:
    """Connect to Voice PE via BLE and send WiFi credentials.

    Returns list of redirect URLs from the device.
    """
    from bleak import BleakClient

    result_received = asyncio.Event()
    result_data: list[bytes] = []

    def on_result(_sender: Any, data: bytes) -> None:
        result_data.append(data)
        result_received.set()

    async with BleakClient(ble_address) as client:
        _LOGGER.debug("Connected to %s", ble_name)
        state = await client.read_gatt_char(IMPROV_CURRENT_STATE)
        state_val = state[0]
        _LOGGER.debug("State: %s", IMPROV_STATE_NAMES.get(state_val, f"unknown({state_val})"))

        if state_val == 4:
            _LOGGER.debug("Already provisioned")
            return []

        if state_val == 1:
            _LOGGER.info("Press the CENTER BUTTON on the Voice PE to authorize.")
            state_event = asyncio.Event()

            def on_state(_sender: Any, data: bytes) -> None:
                if data[0] >= 2:
                    state_event.set()

            await client.start_notify(IMPROV_CURRENT_STATE, on_state)
            try:
                await asyncio.wait_for(state_event.wait(), timeout=button_timeout)
            except asyncio.TimeoutError:
                raise SystemExit("Timed out waiting for button press")

        await client.start_notify(IMPROV_RPC_RESULT, on_result)
        _LOGGER.debug("Sending WiFi credentials (SSID=%s)...", ssid)
        await client.write_gatt_char(IMPROV_RPC_COMMAND, _build_wifi_command(ssid, password), response=True)

        try:
            await asyncio.wait_for(result_received.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            raise SystemExit("Timed out waiting for provisioning result")

        error = await client.read_gatt_char(IMPROV_ERROR_STATE)
        if error[0] != 0:
            raise SystemExit(f"Provisioning error: {IMPROV_ERROR_NAMES.get(error[0], 'unknown')}")

        # Parse redirect URLs
        urls: list[str] = []
        if result_data and len(result_data[0]) > 2:
            data = result_data[0]
            idx, num = 1, data[1]
            idx = 2
            for _ in range(num):
                if idx < len(data):
                    slen = data[idx]
                    idx += 1
                    urls.append(data[idx:idx + slen].decode("utf-8", errors="replace"))
                    idx += slen
        _LOGGER.debug("Provisioned. URLs: %s", urls)
        return urls


def resolve_device_ip(device_id: str, timeout: int = 10) -> str | None:
    """Resolve the Voice PE's IP via mDNS ping."""
    hostname = f"{ESPHOME_MDNS_PREFIX}{device_id}.local"
    _LOGGER.debug("Resolving %s...", hostname)
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-t", str(timeout), hostname],
            capture_output=True, text=True, timeout=timeout + 2,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "bytes from" in line:
                    return line.split("from ")[1].split(":")[0].strip("()")
    except Exception as e:
        _LOGGER.debug("mDNS resolution failed: %s", e)
    return None
