"""Tests for tool execution dispatcher."""

import pytest
from homeassistant_realtime_voice.tools import execute_tool


@pytest.fixture
def mock_callbacks():
    calls = []

    async def call_service(domain, service, entity_id, data):
        calls.append(("call_service", domain, service, entity_id, data))

    async def get_state(entity_id):
        if entity_id == "switch.s4":
            return {"entity_id": "switch.s4", "state": "on", "attributes": {}}
        return None

    async def list_states(domain):
        if domain == "switch":
            return [
                {"entity_id": "switch.s4", "state": "on", "friendly_name": "Corner"},
            ]
        return []

    return call_service, get_state, list_states, calls


@pytest.mark.asyncio
async def test_call_service(mock_callbacks):
    call_service, get_state, list_states, calls = mock_callbacks
    result = await execute_tool(
        "call_service", {"domain": "switch", "service": "turn_off", "entity_id": "switch.s4"},
        call_service=call_service, get_state=get_state, list_states=list_states,
    )
    assert result["success"] is True
    assert calls[0] == ("call_service", "switch", "turn_off", "switch.s4", None)


@pytest.mark.asyncio
async def test_get_entity_state(mock_callbacks):
    call_service, get_state, list_states, _ = mock_callbacks
    result = await execute_tool(
        "get_entity_state", {"entity_id": "switch.s4"},
        call_service=call_service, get_state=get_state, list_states=list_states,
    )
    assert result["state"] == "on"


@pytest.mark.asyncio
async def test_get_entity_state_not_found(mock_callbacks):
    call_service, get_state, list_states, _ = mock_callbacks
    result = await execute_tool(
        "get_entity_state", {"entity_id": "switch.nonexistent"},
        call_service=call_service, get_state=get_state, list_states=list_states,
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_list_entities(mock_callbacks):
    call_service, get_state, list_states, _ = mock_callbacks
    result = await execute_tool(
        "get_entities_by_domain", {"domain": "switch"},
        call_service=call_service, get_state=get_state, list_states=list_states,
    )
    assert result["count"] == 1
    assert result["entities"][0]["entity_id"] == "switch.s4"


@pytest.mark.asyncio
async def test_unknown_tool(mock_callbacks):
    call_service, get_state, list_states, _ = mock_callbacks
    result = await execute_tool(
        "nonexistent", {},
        call_service=call_service, get_state=get_state, list_states=list_states,
    )
    assert "error" in result
