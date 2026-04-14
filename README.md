# AI-Girl (Telegram Userbot)

Telegram userbot с AI-автоответчиком на базе LLM. Поддерживает глубокий контекст (история + reply-цепочка), режимы `.auto` / `.mode`, человеческие задержки и typing, цепочка LLM через LiteLLM.

## Возможности

- Автоматические ответы на входящие сообщения
- Проверка непрочитанных сообщений каждую минуту и автоответ
- Поддержка голосовых и кружков (с контекстом для LLM)
- Анализ фото
- Режимы настроения (`.mode`)
- Ghost-режим с `!ask`
- История диалога для контекста

## Безопасность

- **Не коммитьте** файл `.env` и `*.session`
- Ключи API храните только в `.env`
- Если ключи попадали в репозиторий — **ротируйте** их

## Установка

1. Python 3.10+
2. В корне проекта:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

3. Скопируйте `.env.example` в `.env` и заполните переменные
4. Запустите `python main.py`

## Переменные окружения (.env)

- `API_ID`, `API_HASH` — from my.telegram.org
- `SESSION_NAME` — имя файла сессии (без .session)
- `OWNER_ID` — ваш Telegram ID
- Ключи LLM: `GEMINI_API_KEY`, `GROQ_API_KEY`, `CEREBRAS_API_KEY`, `SILICONFLOW_API_KEY` (минимум один)

## Команды владельца

- `.auto` — справка; `.auto ai` / `.auto static` / `.auto off`
- `.mode sleep|work|gaming|default`
- `.ghost on|off`
- `.human on|off`
- `.vip add|rem|list` (ответом на сообщение)
- `.clearhistory`
- `!ask …`

## Структура

- `handlers/` — обработчики событий
- `core/` — ядро (LLM, клиент, хранилища)
- `utils/` — утилиты и промпты
- `persona.py` — персона и системный промпт
- `config.py` — конфигурация