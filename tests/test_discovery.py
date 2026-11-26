import asyncio
from types import SimpleNamespace

import pytest
from pymax.types import Chat, User
from pymax.types.domain.name import Name

from max_mcp.tools import chats, users

MY_ID = 1000


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(function):
            self.tools[function.__name__] = function
            return function

        return decorator


class DirectoryClient:
    def __init__(self, pages=(), profiles=()):
        self.pages = list(pages)
        self.profiles = {user.id: user for user in profiles}
        self.me = SimpleNamespace(contact=SimpleNamespace(id=MY_ID))
        self.markers = []
        self.user_batches = []

    async def fetch_chats(self, *, marker):
        self.markers.append(marker)
        return self.pages.pop(0) if self.pages else []

    async def get_users(self, user_ids):
        self.user_batches.append(user_ids)
        return [
            self.profiles[user_id] for user_id in user_ids if user_id in self.profiles
        ]

    async def get_user(self, user_id):
        return self.profiles.get(user_id)

    def get_chat_id(self, first_user_id, second_user_id):
        return first_user_id ^ second_user_id


def call(client, name="find_chats", **kwargs):
    mcp = FakeMCP()
    chats.register(mcp)
    users.register(mcp)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=SimpleNamespace(client=client))
    )
    return asyncio.run(mcp.tools[name](ctx, **kwargs))


def chat(chat_id, title=None, kind="CHAT", timestamp=100, participants=None):
    return Chat(
        id=chat_id,
        type=kind,
        status="ACTIVE",
        owner=MY_ID,
        title=title,
        participants=participants or {},
        last_event_time=timestamp,
    )


def user(user_id, name, alias=None):
    names = [Name(name=name, type="ONEME")]
    if alias:
        names.insert(0, Name(name=alias, type="CUSTOM"))
    return User(id=user_id, names=names)


def test_finds_dialog_by_contact_alias_and_resolves_names_in_one_batch():
    client = DirectoryClient(
        pages=[
            [
                chat(MY_ID ^ 42, kind="DIALOG"),
                chat(7, kind="DIALOG", participants={MY_ID: 0, 43: 0}),
            ]
        ],
        profiles=[user(42, "nickname", "Иван по работе"), user(43, "Друг")],
    )

    result = call(client, query="ИВАН")

    assert result == {
        "chats": [
            {
                "chat_id": MY_ID ^ 42,
                "kind": "DIALOG",
                "name": "nickname",
                "contact_name": "Иван по работе",
                "last_event_time": 100,
                "user_id": 42,
            }
        ],
        "total_matched": 1,
        "directory_truncated": False,
        "unresolved_users": 0,
    }
    assert client.user_batches == [[42, 43]]


def test_short_pages_are_not_mistaken_for_end_of_directory():
    client = DirectoryClient(
        pages=[
            [chat(1, "Рабочий чат отдела", timestamp=200)],
            [chat(2, "Рабочий чат", timestamp=100)],
        ]
    )

    result = call(client, query="рабочий чат")

    assert [entry["chat_id"] for entry in result["chats"]] == [2, 1]
    assert result["directory_truncated"] is False
    assert client.markers == [None, 199, 99]


def test_deduplicates_within_and_across_pages_and_reports_stalled_scan():
    first = chat(1, "one", timestamp=200)
    second = chat(2, "two", timestamp=100)
    client = DirectoryClient(pages=[[first, first], [first, second], [second]])

    result = call(client)

    assert result["total_matched"] == 2
    assert result["directory_truncated"] is True
    assert len(client.markers) == 3


@pytest.mark.parametrize(
    "pages,max_chats",
    [
        ([[chat(1, "one"), chat(2, "two")]], 1),
        ([[chat(1, "one", timestamp=0)]], 1000),
        ([[chat(1, "one", timestamp=100)], [chat(2, "two", timestamp=100)]], 1000),
    ],
)
def test_incomplete_directory_never_claims_exhaustion(pages, max_chats):
    result = call(DirectoryClient(pages=pages), query="absent", max_chats=max_chats)

    assert result["chats"] == []
    assert result["directory_truncated"] is True


def test_saved_messages_has_a_searchable_name():
    client = DirectoryClient(pages=[[chat(0, kind="DIALOG", participants={MY_ID: 0})]])

    result = call(client, query="избранное")

    assert result["chats"][0]["chat_id"] == 0
    assert "user_id" not in result["chats"][0]
    assert client.user_batches == []


def test_kind_filter_avoids_unneeded_user_lookups_and_limit_reports_total():
    client = DirectoryClient(
        pages=[
            [
                chat(MY_ID ^ 42, kind="DIALOG"),
                chat(1, "Old", timestamp=100),
                chat(2, "New", timestamp=200),
            ]
        ]
    )

    result = call(client, kind="CHAT", limit=1)

    assert result["total_matched"] == 2
    assert result["chats"][0]["chat_id"] == 2
    assert result["directory_truncated"] is False
    assert result["unresolved_users"] == 0
    assert client.user_batches == []


def test_large_user_lookups_are_batched():
    client = DirectoryClient(
        pages=[[chat(MY_ID ^ user_id, kind="DIALOG") for user_id in range(2000, 2205)]]
    )

    result = call(client)

    assert result["total_matched"] == 205
    assert result["directory_truncated"] is False
    assert result["unresolved_users"] == 205
    assert [len(batch) for batch in client.user_batches] == [100, 100, 5]


def test_get_user_preserves_profile_contact_names_and_dialog_id():
    person = User(
        id=42,
        names=[
            Name(name="Рабочая подпись", type="CUSTOM"),
            Name(first_name="Иван", last_name="Петров", type="ONEME"),
        ],
    )

    result = call(DirectoryClient(profiles=[person]), "get_user", user_id=42)

    assert result["name"] == result["profile_name"] == "Иван Петров"
    assert result["contact_name"] == "Рабочая подпись"
    assert result["dialog_chat_id"] == MY_ID ^ 42


def test_get_unknown_user_reports_not_found():
    with pytest.raises(ValueError, match="not found"):
        call(DirectoryClient(), "get_user", user_id=42)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": " "},
        {"kind": "OTHER"},
        {"limit": 0},
        {"max_chats": True},
    ],
)
def test_invalid_search_does_not_call_max(kwargs):
    client = DirectoryClient()
    with pytest.raises(ValueError):
        call(client, **kwargs)
    assert client.markers == []
