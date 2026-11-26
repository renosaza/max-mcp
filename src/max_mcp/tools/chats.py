from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from ..client import AppCtx
from ..normalize import chat_to_dict
from ._common import positive_int
from .users import self_id, user_to_dict


async def _directory(client: Any, max_chats: int) -> tuple[list[dict[str, Any]], bool]:
    entries: list[dict[str, Any]] = []
    seen: set[int] = set()
    marker: int | None = None
    my_id: int | None = None
    while len(entries) < max_chats:
        page = await client.fetch_chats(marker=marker) or []
        if not page:
            return entries, False
        previous_count = len(entries)
        times: list[int] = []
        for chat in page:
            data = chat_to_dict(chat)
            timestamp = data["last_event_time"]
            if isinstance(timestamp, int) and not isinstance(timestamp, bool):
                times.append(timestamp)
            chat_id = data["id"]
            if chat_id is None or chat_id in seen:
                continue
            seen.add(chat_id)
            entry = {
                "chat_id": chat_id,
                "kind": data["type"],
                "name": data["title"],
                "last_event_time": timestamp,
            }
            if data["type"] == "DIALOG":
                if my_id is None:
                    my_id = self_id(client)
                participants = (
                    chat.get("participants")
                    if isinstance(chat, dict)
                    else getattr(chat, "participants", None)
                ) or {}
                peer = next(
                    (int(uid) for uid in participants if int(uid) != my_id),
                    my_id ^ chat_id,
                )
                if peer == my_id:
                    entry["name"] = data["title"] or "Избранное"
                else:
                    entry["user_id"] = peer
            entries.append(entry)
            if len(entries) == max_chats:
                return entries, True
        # PyMax does not expose a fixed page size or an end marker. Only an
        # empty page proves exhaustion; stalled/missing cursors are incomplete.
        if len(entries) == previous_count or not times:
            return entries, True
        next_marker = min(times) - 1
        if next_marker <= 0 or (marker is not None and next_marker >= marker):
            return entries, True
        marker = next_marker
    return entries, True


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def list_chats(
        ctx: Context[ServerSession, AppCtx],
        limit: int = 50,
        marker: int | None = None,
    ) -> dict[str, Any]:
        """Получить список чатов MAX с курсорной пагинацией через marker."""
        limit = positive_int("limit", limit, maximum=100)
        client = ctx.request_context.lifespan_context.client
        page = await client.fetch_chats(marker=marker) or []
        chats = [chat_to_dict(chat) for chat in page[:limit]]

        next_marker: int | None = None
        if len(page) >= limit:
            timestamps = [
                chat["last_event_time"]
                for chat in chats
                if isinstance(chat.get("last_event_time"), int)
            ]
            if timestamps:
                next_marker = min(timestamps) - 1
        return {"chats": chats, "next_marker": next_marker}

    @mcp.tool()
    async def get_chat(
        ctx: Context[ServerSession, AppCtx],
        chat_id: int,
    ) -> dict[str, Any]:
        """Получить сведения об одном чате или канале MAX по его id."""
        client = ctx.request_context.lifespan_context.client
        chat = await client.get_chat(chat_id)
        return chat_to_dict(chat)

    @mcp.tool()
    async def find_chats(
        ctx: Context[ServerSession, AppCtx],
        query: str | None = None,
        kind: str | None = None,
        limit: int = 50,
        max_chats: int = 1000,
    ) -> dict[str, Any]:
        """Найти чат по имени профиля, подписи из контактов или названию группы.

        Без query возвращает справочник по свежести. kind: DIALOG, CHAT или
        CHANNEL. directory_truncated=true означает, что обход не завершён:
        пустой результат тогда не доказывает отсутствие нужного чата.
        """
        limit = positive_int("limit", limit, maximum=500)
        max_chats = positive_int("max_chats", max_chats, maximum=10_000)
        if kind is not None and kind not in {"DIALOG", "CHAT", "CHANNEL"}:
            raise ValueError("kind must be DIALOG, CHAT or CHANNEL")
        if query is not None and not query.strip():
            raise ValueError("query must be a non-empty string")
        client = ctx.request_context.lifespan_context.client
        entries, truncated = await _directory(client, max_chats)
        if kind is not None:
            entries = [entry for entry in entries if entry["kind"] == kind]
        user_ids = sorted({entry["user_id"] for entry in entries if "user_id" in entry})
        profiles: dict[int, dict[str, Any]] = {}
        # Keep CONTACT_INFO requests bounded; the SDK sends all IDs as one frame.
        for start in range(0, len(user_ids), 100):
            users = await client.get_users(user_ids[start : start + 100]) or []
            for user in users:
                profile = user_to_dict(user)
                profiles[profile["id"]] = profile
        for entry in entries:
            profile = profiles.get(entry.get("user_id"))
            if profile:
                entry["name"] = profile["name"] or entry["name"]
                if profile["contact_name"] and profile["contact_name"] != entry["name"]:
                    entry["contact_name"] = profile["contact_name"]

        needle = query.casefold().strip() if query is not None else None
        ranked: list[tuple[int, int, dict[str, Any]]] = []
        for entry in entries:
            names = [
                str(entry[key]).casefold()
                for key in ("name", "contact_name")
                if entry.get(key)
            ]
            score = (
                0
                if needle is None
                else min(
                    (
                        0 if name == needle else 1 if name.startswith(needle) else 2
                        for name in names
                        if needle in name
                    ),
                    default=None,
                )
            )
            if score is not None:
                ranked.append((score, -(entry["last_event_time"] or 0), entry))
        ranked.sort(key=lambda item: item[:2])
        return {
            "chats": [entry for _, _, entry in ranked[:limit]],
            "total_matched": len(ranked),
            "directory_truncated": truncated,
            "unresolved_users": sum(
                not profiles.get(user_id, {}).get("name") for user_id in user_ids
            ),
        }
