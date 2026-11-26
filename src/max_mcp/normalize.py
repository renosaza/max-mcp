from datetime import date, datetime, time
from enum import Enum
from typing import Any

from pymax.types.domain import message as pymax_message

# PyMax 2.4.1 leaves these forward references incomplete, breaking model_dump().
for _model in (pymax_message.ReplyLink, pymax_message.ForwardLink):
    _model.model_rebuild(_types_namespace=vars(pymax_message))


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes | bytearray):
        # Binary previews are not useful in an MCP JSON response.
        return f"<{len(value)} bytes>"
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(item) for item in value]
    return value


def _dump(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return _jsonable(obj)
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        dumped = dump(mode="python", exclude_none=True)
        return _jsonable(dumped) if isinstance(dumped, dict) else {}
    try:
        return _jsonable(vars(obj))
    except TypeError:
        return {}


def _value(d: dict[str, Any], key: str, obj: Any, attr: str | None = None) -> Any:
    value = d.get(key)
    if value is not None:
        return value
    return getattr(obj, attr or key, None)


def enum_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        value = value.value
    text = str(value)
    return text.rsplit(".", 1)[-1]


def _entity_id(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("id")
    return getattr(value, "id", value)


def chat_to_dict(chat: Any) -> dict[str, Any]:
    d = _dump(chat)
    owner = d.get("owner_id")
    if owner is None:
        owner = _value(d, "owner", chat)
    return {
        "id": _value(d, "id", chat),
        "title": _value(d, "title", chat),
        "type": enum_text(_value(d, "type", chat)),
        "last_event_time": _value(d, "last_event_time", chat),
        "participants_count": _value(d, "participants_count", chat),
        "description": _value(d, "description", chat),
        "owner_id": _entity_id(owner),
    }


def attach_to_dict(att: Any) -> dict[str, Any]:
    d = _dump(att)
    if not isinstance(d, dict):
        return {"value": d, "_kind": type(att).__name__}
    out = dict(d)
    out.setdefault("_kind", type(att).__name__)
    return out


def _reply_to_id(msg: Any, d: dict[str, Any]) -> int | None:
    link = _value(d, "link", msg)
    if link is not None:
        link_data = _dump(link)
        if enum_text(_value(link_data, "type", link)) == "REPLY":
            return _entity_id(_value(link_data, "message", link))
    msg_type = enum_text(_value(d, "type", msg))
    if msg_type == "REPLY":
        return _value(d, "prev_message_id", msg)
    return None


def message_to_dict(msg: Any) -> dict[str, Any]:
    d = _dump(msg)
    message_id = _value(d, "id", msg)
    reply_to_id = _reply_to_id(msg, d)
    out = {
        "id": message_id,
        "message_id": str(message_id) if message_id is not None else None,
        "chat_id": _value(d, "chat_id", msg),
        "sender": _entity_id(_value(d, "sender", msg)),
        "text": _value(d, "text", msg),
        "time": _value(d, "time", msg),
        "type": enum_text(_value(d, "type", msg)),
        "reply_to_id": reply_to_id,
        "reply_to_message_id": str(reply_to_id) if reply_to_id is not None else None,
    }
    attaches = _value(d, "attaches", msg) or []
    if attaches:
        out["attaches"] = [
            attach_to_dict(attachment)
            if not isinstance(attachment, dict)
            else dict(attachment)
            for attachment in attaches
        ]
    return out


def post_to_dict(msg: Any) -> dict[str, Any]:
    base = message_to_dict(msg)
    d = _dump(msg)
    reaction_info = _value(d, "reaction_info", msg)
    stats = _value(d, "stats", msg)
    if reaction_info is not None:
        base["reaction_info"] = reaction_info
    if stats is not None:
        base["stats"] = stats
    return base
