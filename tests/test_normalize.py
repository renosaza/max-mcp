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


def test_chat_dump_does_not_decode_unneeded_bytes() -> None:
    class ChatWithBinaryNestedValue:
        def __init__(self) -> None:
            self.id = 1
            self.title = "chat"
            self.type = "CHAT"
            self.last_event_time = 2
            self.participants_count = 3
            self.description = ""
            self.owner = 4

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict:
            assert exclude_none is True
            if mode == "json":
                raise UnicodeDecodeError("utf-8", b"\x90", 0, 1, "invalid utf-8")
            assert mode == "python"
            return {
                "id": self.id,
                "title": self.title,
                "type": self.type,
                "last_event_time": self.last_event_time,
                "participants_count": self.participants_count,
                "description": self.description,
                "owner": self.owner,
                "last_message": {"binary": b"\x90"},
            }

    result = chat_to_dict(ChatWithBinaryNestedValue())

    assert result == {
        "id": 1,
        "title": "chat",
        "type": "CHAT",
        "last_event_time": 2,
        "participants_count": 3,
        "description": "",
        "owner_id": 4,
    }


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
