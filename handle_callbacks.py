#!/usr/bin/env python3
"""
Social Capital Monitor — Скрипт 3: Обработчик нажатий кнопок
Запускается каждые 5 минут через GitHub Actions.
Читает накопившиеся callback_query от Telegram,
обновляет Notion и отвечает toast-уведомлением.
Лёгкий скрипт: ~5 сек работы, не тратит лишних минут Actions.
"""

import os
import json
import time
import requests
from datetime import datetime, timedelta, date
from notion_client import Client

# ── Конфигурация ──────────────────────────────────────────────────────────────
NOTION_TOKEN       = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]

TG_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
notion = Client(auth=NOTION_TOKEN)


# ── Telegram helpers ──────────────────────────────────────────────────────────
def tg_get_updates(offset=None):
    params = {"timeout": 3, "limit": 100}
    if offset:
        params["offset"] = offset
    resp = requests.get(f"{TG_API}/getUpdates", params=params, timeout=10)
    return resp.json().get("result", [])


def tg_answer_callback(callback_query_id, text="", show_alert=False):
    """Показывает toast-уведомление при нажатии кнопки."""
    requests.post(f"{TG_API}/answerCallbackQuery", json={
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": show_alert,
    }, timeout=10)


def tg_edit_message(chat_id, message_id, text, parse_mode="HTML"):
    """Убирает кнопки и добавляет подтверждение в текст."""
    # Сначала убираем кнопки
    requests.post(f"{TG_API}/editMessageReplyMarkup", json={
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": json.dumps({"inline_keyboard": []})
    }, timeout=10)
    # Затем обновляем текст
    requests.post(f"{TG_API}/editMessageText", json={
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }, timeout=10)


# ── Notion helpers ─────────────────────────────────────────────────────────────
def update_last_contact(page_id, contact_date=None):
    if not contact_date:
        contact_date = date.today().isoformat()
    notion.pages.update(
        page_id=page_id,
        properties={"Последний контакт": {"date": {"start": contact_date}}}
    )


def update_next_contact(page_id, next_date):
    notion.pages.update(
        page_id=page_id,
        properties={"Следующий контакт": {"date": {"start": next_date}}}
    )


def delete_contact(page_id):
    notion.pages.update(page_id=page_id, archived=True)


def update_last_contact_approx(page_id, when):
    today = date.today()
    if when == "recent":
        contact_date = (today - timedelta(days=7)).isoformat()
    elif when == "medium":
        contact_date = (today - timedelta(days=45)).isoformat()
    else:  # long_ago
        contact_date = (today - timedelta(days=120)).isoformat()
    update_last_contact(page_id, contact_date)


# ── Главная функция ───────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().isoformat()}] Проверка нажатий кнопок...")

    updates = tg_get_updates()
    if not updates:
        print("  Нет новых обновлений")
        return

    last_update_id = None
    processed = 0

    for update in updates:
        last_update_id = update["update_id"]
        callback = update.get("callback_query")
        if not callback:
            continue

        data = callback.get("data", "")
        parts = data.split("|")
        action = parts[0]
        page_id = parts[1] if len(parts) > 1 else None

        msg_id   = callback["message"]["message_id"]
        chat_id  = callback["message"]["chat"]["id"]
        orig_text = callback["message"].get("text", "")

        print(f"  Обрабатываю: {action} для {page_id}")

        try:
            if action == "done" and page_id:
                update_last_contact(page_id)
                tg_answer_callback(callback["id"], "✅ Отмечено! Дата обновлена")
                tg_edit_message(chat_id, msg_id,
                    orig_text + "\n\n<i>✅ Связался сегодня — дата обновлена</i>")
                processed += 1

            elif action == "snooze" and page_id:
                new_date = (date.today() + timedelta(days=7)).isoformat()
                update_next_contact(page_id, new_date)
                tg_answer_callback(callback["id"], "⏭ Перенесено на неделю")
                tg_edit_message(chat_id, msg_id,
                    orig_text + "\n\n<i>⏭ Перенесено на 7 дней</i>")
                processed += 1

            elif action in ("recent", "medium", "long_ago") and page_id:
                update_last_contact_approx(page_id, action)
                labels = {
                    "recent":   "📅 Недавно (в течение 2 недель)",
                    "medium":   "🕐 1-3 месяца назад",
                    "long_ago": "⏳ Давно (3+ месяца)"
                }
                label = labels[action]
                tg_answer_callback(callback["id"], "✅ Записано")
                tg_edit_message(chat_id, msg_id,
                    orig_text + f"\n\n<i>✅ {label}</i>")
                processed += 1

            elif action == "delete" and page_id:
                delete_contact(page_id)
                tg_answer_callback(callback["id"], "🗑 Контакт архивирован")
                tg_edit_message(chat_id, msg_id,
                    orig_text + "\n\n<i>🗑 Удалён из базы</i>")
                processed += 1

            elif action == "notion" and page_id:
                notion_url = f"https://www.notion.so/{page_id.replace('-', '')}"
                tg_answer_callback(callback["id"], notion_url, show_alert=True)
                # Кнопки не убираем — пользователь просто смотрит ссылку

        except Exception as e:
            print(f"  Ошибка обработки {action}: {e}")
            try:
                tg_answer_callback(callback["id"], "⚠️ Ошибка, попробуй ещё раз")
            except Exception:
                pass

    print(f"  Обработано: {processed} нажатий")

    # Подтверждаем обработку всех обновлений
    if last_update_id:
        requests.get(f"{TG_API}/getUpdates",
                     params={"offset": last_update_id + 1, "limit": 1}, timeout=10)


if __name__ == "__main__":
    main()
