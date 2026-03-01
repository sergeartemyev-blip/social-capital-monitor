# Документация проекта: Social Capital Monitor

**Версия:** 1.1
**Дата:** 2026-03-01

## 1. Архитектура и ключевые компоненты

Система состоит из трёх Python-скриптов, управляемых через GitHub Actions:

| Файл | Описание |
|---|---|
| `monitor_social.py` | **Основной скрипт.** Ежедневно собирает посты из Telegram-каналов контактов с высоким/средним приоритетом, генерирует саммари через Gemini и обновляет поле "Новости" в Notion. |
| `enrich_contacts.py` | **Скрипт обогащения.** Запускается раз в месяц (или вручную). Находит контакты с пустым полем "Чем занимается", парсит их bio из Telegram и заполняет это поле через Gemini. |
| `digest.py` | **Дайджест.** Ежедневно в 08:00 по Москве собирает контакты, требующие внимания (по датам), и отправляет отчёт в Telegram. |
| `gemini.py` | **Клиент Gemini API.** Инкапсулирует логику запросов к Gemini, включая обработку ошибок (rate limits) через exponential backoff. |

**Workflow (`.github/workflows/daily-monitor.yml`):**
- **07:45 МСК:** Запускает `monitor_social.py` для сбора новостей.
- **08:00 МСК:** Запускает `digest.py` для отправки дайджеста.
- **1-е число каждого месяца (07:45 МСК):** Запускает `enrich_contacts.py` для обогащения новых контактов.

## 2. Секреты и токены

Все секреты хранятся в **GitHub Actions Secrets** репозитория:
`https://github.com/sergeartemyev-blip/social-capital-monitor/settings/secrets/actions`

| Секрет | Значение (частично) | Описание |
|---|---|---|
| `NOTION_TOKEN` | `ntn_1992...` | Internal Integration Token из Notion |
| `NOTION_DATABASE_ID` | `b03e121d...` | ID базы данных контактов в Notion |
| `GEMINI_API_KEY` | `AIzaSy...` | Ключ для Gemini API (aistudio.google.com) |
| `TELEGRAM_BOT_TOKEN` | `844118...` | Токен Telegram-бота для отправки дайджеста |
| `TELEGRAM_CHAT_ID` | (скрыто) | ID твоего личного чата в Telegram |

**GitHub Personal Access Token (PAT):**
- **Токен:** `(см. project instructions или Notion)`
- **Права:** `repo`, `workflow` (полный доступ к репозиторию и Actions)
- **Назначение:** Используется для push в репозиторий и запуска workflows из чата Manus.

## 3. Как вносить изменения

1. **Клонировать репозиторий:** `gh repo clone sergeartemyev-blip/social-capital-monitor`
2. **Внести изменения** в код.
3. **Протестировать локально** (если возможно).
4. **Запушить изменения:** `git push origin master:main`
5. **Запустить workflow вручную** для теста: `gh workflow run daily-monitor.yml --ref main -f run_mode=monitor_only`

## 4. Долгосрочная память (для Manus)

Чтобы передать контекст новому чату, используй инструкцию:

> «Проект **Автоматизация (TnDUVZvz9C836tUvh4a8Um)**. Вся документация и секреты находятся в файле `PROJECT_DOCS.md` в репозитории `sergeartemyev-blip/social-capital-monitor`. Клонируй репозиторий и прочитай этот файл.»
