import json
from datetime import UTC, date, datetime, time
from enum import Enum
from types import SimpleNamespace

import pytest
from pymax.types.domain import Chat, Message

from max_mcp.normalize import (
    attach_to_dict,
    chat_to_dict,
    message_to_dict,
    post_to_dict,
)


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


@pytest.mark.parametrize("link_type,reply_to", [("REPLY", 7), ("FORWARD", None)])
def test_real_pymax_links_can_be_serialized(link_type, reply_to):
    message = Message.model_validate(
        {
            "id": 9,
            "time": 14,
            "type": "USER",
            "link": {
                "type": link_type,
                "chatId": 2,
                "message": {"id": 7, "time": 10, "type": "USER", "text": "source"},
            },
        }
    )

    result = message_to_dict(message)

    assert result["reply_to_id"] == reply_to
    assert json.loads(json.dumps(result))["id"] == 9


def test_large_message_ids_have_exact_string_aliases():
    message_id = 116641336752745889
    reply_id = message_id - 2
    message = Message.model_validate(
        {
            "id": message_id,
            "time": 14,
            "type": "USER",
            "link": {
                "type": "REPLY",
                "chatId": 2,
                "message": {"id": reply_id, "time": 10, "type": "USER"},
            },
        }
    )

    result = json.loads(json.dumps(message_to_dict(message)))

    assert result["id"] == message_id
    assert result["reply_to_id"] == reply_id
    assert result["message_id"] == str(message_id)
    assert result["reply_to_message_id"] == str(reply_id)


@pytest.mark.parametrize(
    "attachment",
    [
        {
            "_type": "PHOTO",
            "baseUrl": "https://example.test/photo",
            "photoId": 4,
            "photoToken": "example",
            "width": 8,
            "height": 6,
        },
        {
            "_type": "VIDEO",
            "videoId": 4,
            "token": "example",
            "videoType": 0,
            "thumbnail": "https://example.test/thumbnail",
            "width": 8,
            "height": 6,
        },
    ],
)
def test_binary_previews_in_real_messages_and_chats(attachment):
    message = Message.model_validate(
        {
            "id": 1,
            "chatId": 2,
            "time": 3,
            "type": "USER",
            "attaches": [{**attachment, "previewData": b"\xfe\xff\x00"}],
        }
    )
    chat = Chat(id=2, type="CHAT", status="ACTIVE", owner=0, last_message=message)

    result = message_to_dict(message)
    raw_attachment = attach_to_dict(message.attaches[0])

    assert result["attaches"][0]["preview_data"] == "<3 bytes>"
    assert result["attaches"][0]["width"] == 8
    assert raw_attachment["type"] == attachment["_type"]
    assert raw_attachment["preview_data"] == "<3 bytes>"
    assert json.loads(json.dumps(result))["chat_id"] == 2
    assert chat_to_dict(chat)["owner_id"] == 0


def test_nested_binary_enums_and_dates_are_json_safe():
    message = Message(
        id=1,
        time=2,
        type="CHANNEL",
        stats={
            "preview": {"data": bytearray(b"\xff\x00")},
            "type": MessageType.REPLY,
            "created": datetime(2026, 11, 26, 1, 2, 3, tzinfo=UTC),
            "dates": (date(2026, 11, 26), time(1, 2, 3)),
        },
    )

    result = json.loads(json.dumps(post_to_dict(message)))

    assert result["stats"] == {
        "preview": {"data": "<2 bytes>"},
        "type": "REPLY",
        "created": "2026-11-26T01:02:03+00:00",
        "dates": ["2026-11-26", "01:02:03"],
    }
