from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from ..client import AppCtx
from ..normalize import _dump, enum_text
from ._common import positive_int


def self_id(client: Any) -> int:
    contact = getattr(getattr(client, "me", None), "contact", None)
    user_id = getattr(contact, "id", None)
    if user_id is None:
        raise RuntimeError("MAX session has no profile yet; client is not ready")
    return int(user_id)


def user_to_dict(user: Any) -> dict[str, Any]:
    data = _dump(user)
    names: dict[str | None, str] = {}
    for raw in data.get("names") or []:
        name = _dump(raw)
        text = name.get("name") or " ".join(
            str(name[key]) for key in ("first_name", "last_name") if name.get(key)
        )
        if text and str(text).strip():
            names.setdefault(enum_text(name.get("type")), str(text).strip())
    profile = names.get("ONEME")
    contact = names.get("CUSTOM")
    return {
        "id": data.get("id"),
        "name": profile or contact or next(iter(names.values()), None),
        "profile_name": profile,
        "contact_name": contact,
        "description": data.get("description"),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_user(
        ctx: Context[ServerSession, AppCtx],
        user_id: int,
    ) -> dict[str, Any]:
        """Получить имя профиля, подпись из контактов и id личного диалога MAX."""
        user_id = positive_int("user_id", user_id)
        client = ctx.request_context.lifespan_context.client
        user = await client.get_user(user_id)
        if user is None:
            raise ValueError(f"user {user_id} not found")
        profile = user_to_dict(user)
        if profile["id"] != user_id:
            raise RuntimeError("MAX returned a different user than requested")
        profile["dialog_chat_id"] = client.get_chat_id(self_id(client), user_id)
        return profile
