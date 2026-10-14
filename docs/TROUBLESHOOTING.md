# Диагностика max-mcp

## Сервер не подключается

Проверьте регистрацию:

```bash
claude mcp list
# Если используется OpenAI Codex:
codex mcp list
```

Запустите сервер вручную, чтобы увидеть ошибку в терминале:

```bash
cd ~/max-mcp
uv run max-mcp
```

Частые причины:

- отсутствует `~/.max-mcp/session.db`;
- каталог сессии принадлежит другому пользователю;
- права каталога шире `0700` или права файла сессии шире `0600`;
- сессия устарела;
- установленная версия `maxapi-python` перестала соответствовать протоколу MAX;
- для SMS-клиента не открывается `https://hashes.pymax.org/versions.json` или там отсутствует выбранная версия MAX.

## Ошибка загрузки fingerprint / `VersionNotFoundError`

Для профиля Android 26.35.1 PyMax 2.4.1 загружает fingerprint из HTTPS-каталога. Проверьте доступность адреса:

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' https://hashes.pymax.org/versions.json
```

Если каталог временно недоступен, запустите SMS-клиент с версией из встроенного каталога:

```bash
MAX_MCP_APP_VERSION=26.28.0 uv run max-mcp
```

Для постоянного запуска из Codex укажите `MAX_MCP_APP_VERSION=26.28.0` в `~/.codex/config.toml`, в `[mcp_servers.max-mcp.env]`; либо перерегистрируйте сервер через `codex mcp add max-mcp --env MAX_MCP_APP_VERSION=26.28.0 -- ...`. Не удаляйте сессию ради смены версии приложения.

## Повторный вход

```bash
rm -f ~/.max-mcp/session.db \
      ~/.max-mcp/session.kind \
      ~/.max-mcp/session.phone

cd ~/max-mcp
uv run max-mcp-login login-qr
```

Для входа по номеру:

```bash
uv run max-mcp-login login-sms --phone +79991234567
```

## После QR-кода появлялся `CancelledError`

В ранней версии PyMax закрывал WebSocket после успешного входа через `stop()`, а возникшая отмена задачи доходила до CLI как ошибка. Из-за этого `session.kind` мог не записаться, хотя `session.db` уже существовал.

Текущая версия считает отмену нормальным завершением только после подтверждённого входа. Отмена до события входа по-прежнему считается ошибкой.

## Ошибка `MAX client did not become ready`

Клиент не сообщил о готовности за 30 секунд. Проверьте соединение, доступность MAX и актуальность сессии. Затем повторите ручной запуск:

```bash
uv run max-mcp
```

## Не отправляется файл

Проверьте абсолютный путь и разрешённые каталоги:

```bash
export MAX_MCP_SEND_ROOTS="$HOME/Desktop:$HOME/Documents:/tmp"
```

Даже при изменённом `MAX_MCP_SEND_ROOTS` закрытые каталоги с ключами, конфигурацией и сессией MAX остаются запрещёнными.

## Большая выгрузка обрывается

`dump_channel` ограничен 1000 публикациями за вызов. Используйте возвращаемое поле `next_before_time` для продолжения выгрузки. Не пытайтесь помещать весь большой канал в один MCP-ответ.
