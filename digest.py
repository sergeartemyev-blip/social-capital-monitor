#!/usr/bin/env python3
"""
Social Capital Monitor — Скрипт 2: Дайджест
Каждое утро в 8:00 читает базу Notion, формирует дайджест
и отправляет в Telegram с интерактивными кнопками.
Также обрабатывает нажатия кнопок (webhook-режим не нужен —
используем getUpdates polling при каждом запуске).
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

# Категории, которые включаем в дайджест
ACTIVE_CIRCLES = {
    "Клиент активный",
    "Клиент бывший",
    "Партнер",
    "Близкий круг",
    "Знакомый",
    "Зона развития",
}

# Максимум контактов «без данных» в одном дайджесте
MAX_EMPTY_PER_DAY = 3

# За сколько дней до срока показываем в дайджесте
DIGEST_DAYS_BEFORE = 6


# ── Telegram helpers ──────────────────────────────────────────────────────────
def tg_send(text, reply_markup=None, parse_mode="HTML"):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    resp = requests.post(f"{TG_API}/sendMessage", json=payload, timeout=15)
    return resp.json()


def tg_get_updates(offset=None):
    params = {"timeout": 5, "limit": 100}
    if offset:
        params["offset"] = offset
    resp = requests.get(f"{TG_API}/getUpdates", params=params, timeout=15)
    return resp.json().get("result", [])


def tg_answer_callback(callback_query_id, text=""):
    requests.post(f"{TG_API}/answerCallbackQuery", json={
        "callback_query_id": callback_query_id,
        "text": text,
    }, timeout=10)


def tg_edit_message(chat_id, message_id, text, parse_mode="HTML"):
    requests.post(f"{TG_API}/editMessageText", json={
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }, timeout=10)


# ── Notion helpers ─────────────────────────────────────────────────────────────
notion = Client(auth=NOTION_TOKEN)


def get_all_contacts():
    """Читает все контакты из Notion."""
    all_pages = []
    cursor = None
    while True:
        kwargs = {"database_id": NOTION_DATABASE_ID, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.databases.query(**kwargs)
        all_pages.extend(resp["results"])
        if not resp.get("has_more"):
            break
        cursor = resp["next_cursor"]
    return all_pages


def parse_contact(page):
    """Парсит страницу Notion в словарь контакта."""
    props = page["properties"]

    def get_url(field):
        v = props.get(field, {}).get("url")
        return v if v else None

    def get_text(field):
        rich = props.get(field, {}).get("rich_text", [])
        return "".join(r.get("plain_text", "") for r in rich) if rich else ""

    def get_title():
        t = props.get("Имя", {}).get("title", [])
        return "".join(r.get("plain_text", "") for r in t) if t else "Без имени"

    def get_date_val(field):
        d = props.get(field, {}).get("date")
        return d.get("start") if d else None

    def get_select(field):
        sel = props.get(field, {}).get("select")
        return sel.get("name") if sel else None

    def get_phone():
        p = props.get("Телефон", {}).get("phone_number")
        return p if p else None

    def get_birthday():
        d = props.get("День рождения", {}).get("date")
        return d.get("start") if d else None

    circle = get_select("Круг")
    priority = get_select("Приоритет")
    last_contact_str = get_date_val("Последний контакт")
    next_contact_str = get_date_val("Следующий контакт")
    frequency = props.get("Частота контактов дни", {}).get("number")

    # Вычисляем дату следующего контакта
    computed_next = None
    if next_contact_str:
        try:
            computed_next = date.fromisoformat(next_contact_str)
        except Exception:
            pass
    elif last_contact_str and frequency:
        try:
            last_dt = date.fromisoformat(last_contact_str)
            computed_next = last_dt + timedelta(days=int(frequency))
        except Exception:
            pass

    # Telegram username из ссылки
    tg_personal = get_url("Telegram")
    tg_username = None
    if tg_personal:
        parts = tg_personal.rstrip("/").split("/")
        for i, p in enumerate(parts):
            if p in ("t.me", "telegram.me") and i + 1 < len(parts):
                u = parts[i + 1].lstrip("@")
                if not u.startswith("+"):
                    tg_username = u

    return {
        "page_id": page["id"],
        "name": get_title(),
        "circle": circle,
        "priority": priority,
        "last_contact": last_contact_str,
        "next_contact": next_contact_str,
        "computed_next": computed_next,
        "frequency": frequency,
        "telegram": tg_personal,
        "tg_username": tg_username,
        "telegram_channel": get_url("Telegram канал"),
        "instagram": get_url("Insta"),
        "phone": get_phone(),
        "birthday": get_birthday(),
        "notes": get_text("Заметки"),
        "notion_url": f"https://www.notion.so/{page['id'].replace('-', '')}",
    }


def update_last_contact(page_id, contact_date=None):
    """Обновляет поле «Последний контакт» в Notion."""
    if not contact_date:
        contact_date = date.today().isoformat()
    notion.pages.update(
        page_id=page_id,
        properties={
            "Последний контакт": {
                "date": {"start": contact_date}
            }
        }
    )


def update_next_contact(page_id, next_date):
    """Обновляет поле «Следующий контакт» в Notion."""
    notion.pages.update(
        page_id=page_id,
        properties={
            "Следующий контакт": {
                "date": {"start": next_date}
            }
        }
    )


def delete_contact(page_id):
    """Архивирует контакт в Notion (мягкое удаление)."""
    notion.pages.update(page_id=page_id, archived=True)


def update_last_contact_approx(page_id, when):
    """Обновляет дату последнего контакта приблизительно."""
    today = date.today()
    if when == "recent":
        contact_date = (today - timedelta(days=7)).isoformat()
    elif when == "medium":
        contact_date = (today - timedelta(days=45)).isoformat()
    else:  # long_ago
        contact_date = (today - timedelta(days=120)).isoformat()
    update_last_contact(page_id, contact_date)


# ── Формирование дайджеста ────────────────────────────────────────────────────
def build_contact_card(c, overdue=False):
    """Формирует текст карточки контакта для Telegram."""
    today = date.today()

    # Заголовок
    priority_emoji = {"Высокий": "🔴", "Средний": "🟡", "Низкий": "🟢"}.get(c["priority"], "⚪")
    lines = [f"{priority_emoji} <b>{c['name']}</b> · {c['circle']}"]

    # Срок
    if c["computed_next"]:
        delta = (today - c["computed_next"]).days
        if delta > 0:
            lines.append(f"📅 Просрочено на {delta} дн.")
        elif delta == 0:
            lines.append(f"📅 Срок сегодня")
        else:
            lines.append(f"📅 Через {-delta} дн.")
    else:
        lines.append("📅 Дата неизвестна")

    # Заметки из мониторинга
    if c["notes"]:
        lines.append("")
        lines.append("💡 <b>Актуально:</b>")
        # Берём только строки с •
        note_lines = [l.strip() for l in c["notes"].split("\n") if l.strip().startswith("•")]
        if note_lines:
            lines.extend(note_lines[:4])
        else:
            # Если нет маркеров — берём первые 200 символов
            short = c["notes"].replace("\n", " ")[:200]
            lines.append(short)

    # Контакты
    lines.append("")
    if c["tg_username"]:
        tg_user = c["tg_username"]
        lines.append(f"\u2708\ufe0f <a href='https://t.me/{tg_user}'>@{tg_user}</a>")
    if c["instagram"]:
        ig_url = c["instagram"]
        ig = ig_url.rstrip("/").split("/")[-1]
        lines.append(f"\U0001f4f8 <a href='{ig_url}'>@{ig}</a>")

    return "\n".join(lines)


def build_keyboard(c, card_type="normal"):
    """Строит inline-клавиатуру для карточки контакта."""
    page_id = c["page_id"]

    if card_type == "empty":
        # Для контактов без данных
        return {
            "inline_keyboard": [
                [
                    {"text": "📅 Недавно (до 2 нед)", "callback_data": f"recent|{page_id}"},
                    {"text": "🕐 Давно (1-3 мес)", "callback_data": f"medium|{page_id}"},
                ],
                [
                    {"text": "⏳ Очень давно (3+ мес)", "callback_data": f"long_ago|{page_id}"},
                    {"text": "🗑 Удалить из базы", "callback_data": f"delete|{page_id}"},
                ]
            ]
        }
    else:
        # Для обычных контактов
        buttons = []
        row1 = [{"text": "✅ Связался", "callback_data": f"done|{page_id}"}]
        if c.get("tg_username"):
            row1.append({"text": "✈️ Написать", "callback_data": f"open_tg|{page_id}|{c['tg_username']}"})
        buttons.append(row1)
        buttons.append([
            {"text": "⏭ Через неделю", "callback_data": f"snooze|{page_id}"},
            {"text": "📋 В Notion", "callback_data": f"notion|{page_id}"},
        ])
        return {"inline_keyboard": buttons}


# ── Обработка нажатий кнопок ──────────────────────────────────────────────────
def process_callbacks():
    """Обрабатывает накопившиеся нажатия кнопок."""
    updates = tg_get_updates()
    if not updates:
        return

    last_update_id = None
    for update in updates:
        last_update_id = update["update_id"]
        callback = update.get("callback_query")
        if not callback:
            continue

        data = callback.get("data", "")
        parts = data.split("|")
        action = parts[0]
        page_id = parts[1] if len(parts) > 1 else None
        extra = parts[2] if len(parts) > 2 else None

        msg_id = callback["message"]["message_id"]
        chat_id = callback["message"]["chat"]["id"]

        if action == "done" and page_id:
            update_last_contact(page_id)
            tg_answer_callback(callback["id"], "✅ Отмечено! Дата обновлена в Notion")
            tg_edit_message(chat_id, msg_id,
                callback["message"]["text"] + "\n\n<i>✅ Связался сегодня</i>")

        elif action == "snooze" and page_id:
            new_date = (date.today() + timedelta(days=7)).isoformat()
            update_next_contact(page_id, new_date)
            tg_answer_callback(callback["id"], "⏭ Перенесено на неделю")
            tg_edit_message(chat_id, msg_id,
                callback["message"]["text"] + "\n\n<i>⏭ Перенесено на 7 дней</i>")

        elif action in ("recent", "medium", "long_ago") and page_id:
            update_last_contact_approx(page_id, action)
            labels = {"recent": "недавно", "medium": "около месяца назад", "long_ago": "давно"}
            tg_answer_callback(callback["id"], f"✅ Записано: общались {labels[action]}")
            tg_edit_message(chat_id, msg_id,
                callback["message"]["text"] + f"\n\n<i>✅ Записано: общались {labels[action]}</i>")

        elif action == "delete" and page_id:
            delete_contact(page_id)
            tg_answer_callback(callback["id"], "🗑 Контакт архивирован")
            tg_edit_message(chat_id, msg_id,
                callback["message"]["text"] + "\n\n<i>🗑 Контакт удалён из базы</i>")

        elif action == "open_tg" and extra:
            tg_answer_callback(callback["id"], f"Открываю @{extra}")

        elif action == "notion" and page_id:
            notion_url = f"https://www.notion.so/{page_id.replace('-', '')}"
            tg_answer_callback(callback["id"], notion_url)

    # Подтверждаем обработку обновлений
    if last_update_id:
        requests.get(f"{TG_API}/getUpdates", params={"offset": last_update_id + 1, "limit": 1}, timeout=10)


# ── Главная функция ───────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().isoformat()}] Запуск дайджеста...")

    # Сначала обрабатываем накопившиеся нажатия кнопок
    print("  Обрабатываем нажатия кнопок...")
    process_callbacks()

    # Читаем базу
    print("  Читаем базу Notion...")
    all_pages = get_all_contacts()
    contacts = [parse_contact(p) for p in all_pages]
    print(f"  Всего контактов: {len(contacts)}")

    today = date.today()
    cutoff = today + timedelta(days=DIGEST_DAYS_BEFORE)

    # Фильтруем контакты для дайджеста
    due_contacts = []      # Срок подошёл
    empty_contacts = []    # Нет данных, высокий приоритет

    for c in contacts:
        if c["circle"] not in ACTIVE_CIRCLES:
            continue

        if c["computed_next"] and c["computed_next"] <= cutoff:
            due_contacts.append(c)
        elif not c["last_contact"] and not c["next_contact"] and c["priority"] == "Высокий":
            empty_contacts.append(c)

    # Сортируем: сначала просроченные, потом по дате
    due_contacts.sort(key=lambda x: x["computed_next"] or date.max)

    print(f"  Контактов в дайджесте: {len(due_contacts)}")
    print(f"  Контактов без данных (высокий приоритет): {len(empty_contacts)}")

    # Проверяем дни рождения
    birthday_alerts = []
    for c in contacts:
        if not c["birthday"]:
            continue
        try:
            bday = date.fromisoformat(c["birthday"])
            bday_this_year = bday.replace(year=today.year)
            if bday_this_year < today:
                bday_this_year = bday_this_year.replace(year=today.year + 1)
            days_until = (bday_this_year - today).days
            if 0 <= days_until <= 14:
                birthday_alerts.append((c["name"], days_until, bday_this_year))
        except Exception:
            pass

    # Если нечего отправлять — короткое сообщение
    if not due_contacts and not empty_contacts and not birthday_alerts:
        tg_send("☀️ <b>Доброе утро!</b>\n\nСегодня нет контактов, требующих внимания. Хороший день!")
        print("  Нет контактов для дайджеста")
        return

    # Заголовок дайджеста
    header_lines = [f"☀️ <b>Дайджест на {today.strftime('%d.%m.%Y')}</b>"]

    if birthday_alerts:
        header_lines.append("")
        header_lines.append("🎂 <b>Дни рождения:</b>")
        for name, days, bday in sorted(birthday_alerts, key=lambda x: x[1]):
            if days == 0:
                header_lines.append(f"  🎉 <b>{name}</b> — сегодня!")
            elif days == 1:
                header_lines.append(f"  🎂 {name} — завтра")
            else:
                header_lines.append(f"  🎂 {name} — через {days} дн. ({bday.strftime('%d.%m')})")

    if due_contacts:
        header_lines.append("")
        header_lines.append(f"📋 <b>Нужно связаться: {len(due_contacts)}</b>")

    tg_send("\n".join(header_lines))
    time.sleep(0.5)

    # Отправляем карточки контактов
    for c in due_contacts:
        card_text = build_contact_card(c)
        keyboard = build_keyboard(c, "normal")
        tg_send(card_text, reply_markup=keyboard)
        time.sleep(0.3)

    # Контакты без данных (до MAX_EMPTY_PER_DAY)
    if empty_contacts:
        time.sleep(0.5)
        tg_send(f"❓ <b>Нет данных по {len(empty_contacts)} контактам с высоким приоритетом.</b>\nКогда последний раз общались?")
        for c in empty_contacts[:MAX_EMPTY_PER_DAY]:
            card_text = f"👤 <b>{c['name']}</b> · {c['circle']}\n📅 Дата последнего контакта неизвестна"
            keyboard = build_keyboard(c, "empty")
            tg_send(card_text, reply_markup=keyboard)
            time.sleep(0.3)

    print(f"[{datetime.now().isoformat()}] Дайджест отправлен")


if __name__ == "__main__":
    main()
