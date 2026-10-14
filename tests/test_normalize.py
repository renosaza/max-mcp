from enum import Enum
from types import SimpleNamespace

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



def test_reply_link_from_new_pymax_is_normalized() -> None:
    message = SimpleNamespace(
        id=9,
        chat_id=2,
        sender=3,
        text="reply",
        time=14,
        type="USER",
        prev_message_id=8,
        attaches=[],
        link=SimpleNamespace(
            type="REPLY",
            message=SimpleNamespace(id=7),
        ),
    )

    result = message_to_dict(message)

    assert result["reply_to_id"] == 7


def test_forward_link_is_not_a_reply() -> None:
    message = SimpleNamespace(
        id=9,
        chat_id=2,
        sender=3,
        text="forward",
        time=14,
        type="USER",
        prev_message_id=8,
        attaches=[],
        link={"type": "FORWARD", "message": {"id": 7}},
    )

    assert message_to_dict(message)["reply_to_id"] is None
