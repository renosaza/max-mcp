from mcp.server.fastmcp import FastMCP

from .client import lifespan
from .tools import channels, chats, download, messages, send, users

INSTRUCTIONS = (
    "Инструменты пользовательской сессии MAX. Сначала находите чат по имени "
    "через find_chats или по id через list_chats/get_chat. get_user показывает "
    "имя пользователя и id личного диалога. Историю читайте через read_messages или "
    "list_channel_posts. search_messages выполняет локальный проход и может "
    "работать медленно. dump_channel выгружает канал частями до 1000 публикаций. "
    "Вложения видны в поле attaches прочитанных сообщений и через "
    "list_attachments; download_attachment сохраняет их на диск. "
    "Для операций с сообщением используйте строковый message_id, чтобы не "
    "потерять точность большого числового id. "
    "Перед send_message/send_file обязательно проверьте chat_id и содержимое."
)

mcp = FastMCP("max-mcp", instructions=INSTRUCTIONS, lifespan=lifespan)
chats.register(mcp)
messages.register(mcp)
channels.register(mcp)
send.register(mcp)
download.register(mcp)
users.register(mcp)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
