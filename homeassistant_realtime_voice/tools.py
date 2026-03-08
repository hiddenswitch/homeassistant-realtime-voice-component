"""HA tool execution — generic dispatcher that takes callbacks."""

from __future__ import annotations

from typing import Any, Callable, Awaitable


ServiceCaller = Callable[[str, str, str | None, dict | None], Awaitable[None]]
StateGetter = Callable[[str], Awaitable[dict[str, Any] | None]]
DomainLister = Callable[[str], Awaitable[list[dict[str, Any]]]]


async def execute_tool(
    name: str,
    args: dict[str, Any],
    *,
    call_service: ServiceCaller,
    get_state: StateGetter,
    list_states: DomainLister,
) -> dict[str, Any]:
    """Route a tool call to the appropriate callback.

    Args:
        name: Tool function name.
        args: Tool arguments from OpenAI.
        call_service: async (domain, service, entity_id, data) -> None
        get_state: async (entity_id) -> state dict or None
        list_states: async (domain) -> list of state dicts
    """
    if name == "call_service":
        domain = args.get("domain", "")
        service = args.get("service", "")
        await call_service(domain, service, args.get("entity_id"), args.get("data"))
        return {"success": True, "message": f"Called {domain}.{service}"}

    if name == "get_entity_state":
        state = await get_state(args.get("entity_id", ""))
        if state is None:
            return {"error": f"Entity not found: {args.get('entity_id')}"}
        return state

    if name == "get_entities_by_domain":
        entities = await list_states(args.get("domain", ""))
        return {
            "domain": args.get("domain"),
            "count": len(entities),
            "entities": entities,
        }

    return {"error": f"Unknown tool: {name}"}
