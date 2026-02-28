# Social Capital Monitor

Система автоматического мониторинга социального капитала. Каждое утро в 08:00 (МСК) читает базу контактов в Notion, собирает свежие публикации из Instagram, Telegram и YouTube, и отправляет персональный дайджест в Telegram.

## Логика работы

- Мониторятся только контакты, у которых поле **«Следующий контакт»** наступает в ближайшие 6 дней или уже просрочено
- Исключаются категории: «Враг/конкурент» и «Не помню»
- Дни рождения проверяются на ближайшие 14 дней

## Настройка GitHub Secrets

Перейди в Settings → Secrets and variables → Actions и добавь:

| Secret | Значение |
|--------|---------|
| `NOTION_TOKEN` | Internal Integration Token из Notion |
| `NOTION_DATABASE_ID` | `15bd188d-f5fe-4467-9408-dc09f0ba811f` |
| `TELEGRAM_BOT_TOKEN` | Токен бота @asimangus_bot |
| `TELEGRAM_CHAT_ID` | `159444719` |
| `YOUTUBE_API_KEY` | Ключ YouTube Data API v3 (опционально) |

## Ручной запуск

В GitHub: Actions → Daily Social Capital Monitor → Run workflow

## Структура файлов

```
monitor.py          # Основной скрипт
requirements.txt    # Зависимости Python
.github/workflows/
  daily-monitor.yml # Расписание запуска
```
