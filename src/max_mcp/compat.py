"""Обходы дефектов pymax, которые ломают сериализацию ответов.

`ReplyLink` и `ForwardLink` объявлены в `pymax.types.domain.message` до самого
`Message`, на который ссылаются. Файл включает `from __future__ import
annotations`, поэтому pydantic оставляет обе модели незавершёнными, а pymax не
вызывает для них `model_rebuild()`. Валидация входящих кадров при этом
проходит, а любой `model_dump()` над чатом или сообщением падает с
`TypeError: 'MockValSer' object is not an instance of 'SchemaSerializer'`.

Проверено на pymax 2.4.1 / pydantic 2.13.4.
"""

_applied = False


def apply_pymax_patches() -> None:
    """Достраивает модели pymax, оставшиеся незавершёнными после импорта."""
    global _applied
    if _applied:
        return

    from pymax.types.domain import message as message_module

    namespace = vars(message_module)
    for model_name in ("ReplyLink", "ForwardLink"):
        model = getattr(message_module, model_name, None)
        if model is None or getattr(model, "__pydantic_complete__", False):
            continue
        model.model_rebuild(_types_namespace=namespace, force=True)

    _applied = True
