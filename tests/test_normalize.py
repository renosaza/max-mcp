from enum import Enum
from types import SimpleNamespace

from pydantic import BaseModel

from max_mcp.normalize import chat_to_dict, message_to_dict


class MessageType(Enum):
    REPLY = "REPLY"


def test_chat_keeps_falsy_values() -> None:
    chat = SimpleNamespace(
        id=0,
        title="",
        type="CHAT",
        last_event_time=0,
        participants_count=0,
        description="",
        owner=0,
    )

    result = chat_to_dict(chat)

    assert result["id"] == 0
    assert result["title"] == ""
    assert result["last_event_time"] == 0
    assert result["owner_id"] == 0


def test_reply_enum_is_normalized() -> None:
    message = SimpleNamespace(
        id=1,
        chat_id=2,
        sender=SimpleNamespace(id=3),
        text="ok",
        time=4,
        type=MessageType.REPLY,
        prev_message_id=5,
        attaches=[],
    )

    result = message_to_dict(message)

    assert result["type"] == "REPLY"
    assert result["reply_to_id"] == 5
    assert result["sender"] == 3


def test_pymax_link_models_are_rebuilt() -> None:
    """pymax оставляет ReplyLink и ForwardLink незавершёнными.

    Обе объявлены до Message, на который ссылаются, и без model_rebuild()
    их сериализатор остаётся заглушкой: любой model_dump() над чатом или
    сообщением падает на MockValSer. Импорт normalize обязан это чинить.
    """
    from pymax.types.domain import message as message_module

    for model_name in ("ReplyLink", "ForwardLink"):
        model = getattr(message_module, model_name)
        assert model.__pydantic_complete__, f"{model_name} остался незавершённым"


class _Preview(BaseModel):
    preview_data: bytes
    width: int


class _Photo(BaseModel):
    type: str
    preview: _Preview


def test_binary_fields_do_not_break_dump() -> None:
    """Превью картинок приходят сырыми байтами и не декодируются как UTF-8."""
    message = SimpleNamespace(
        id=1,
        chat_id=2,
        sender=SimpleNamespace(id=3),
        text="",
        time=4,
        type="TEXT",
        attaches=[_Photo(type="PHOTO", preview=_Preview(preview_data=b"\xfe\xff\x00", width=8))],
    )

    result = message_to_dict(message)

    assert result["attaches"][0]["preview"]["preview_data"] == "<3 bytes>"
    assert result["attaches"][0]["preview"]["width"] == 8
