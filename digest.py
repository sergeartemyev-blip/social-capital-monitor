#!/usr/bin/env python3
"""
Social Capital Monitor — Скрипт 2: Дайджест
Каждое утро в 8:00 читает базу Notion, формирует дайджест
и отправляет в Telegram с интерактивными кнопками.
Структура дайджеста:
  1. Заголовок + дни рождения
  2. 📰 Новостная лента (ключевые события из мониторинга)
  3. 📞 Пора связаться (до 5 самых горячих)
  4. ❓ Обновление базы (контакты без данных, высокий приоритет, до 3 в день)
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

MAX_DUE_CONTACTS   = 5   # Максимум в блоке «Пора связаться»
MAX_EMPTY_PER_DAY  = 3   # Максимум в блоке «Обновление базы»
DIGEST_DAYS_BEFORE = 6   # За сколько дней до срока показываем


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


def tg_answer_callback(callback_query_id, text="", show_alert=False):
    """Показывает toast-уведомление при нажатии кнопки."""
    requests.post(f"{TG_API}/answerCallbackQuery", json={
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": show_alert,
    }, timeout=10)


def tg_edit_message(chat_id, message_id, text, parse_mode="HTML", remove_keyboard=True):
    """Редактирует сообщение и опционально убирает кнопки."""
    if remove_keyboard:
        requests.post(f"{TG_API}/editMessageReplyMarkup", json={
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": json.dumps({"inline_keyboard": []})
        }, timeout=10)
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

    # Telegram username из поля «Личный TG»
    tg_personal = get_url("Личный TG")
    tg_username = None
    if tg_personal:
        parts = tg_personal.rstrip("/").split("/")
        for i, p in enumerate(parts):
            if p in ("t.me", "telegram.me") and i + 1 < len(parts):
                u = parts[i + 1].lstrip("@")
                if not u.startswith("+"):
                    tg_username = u

    # Поле «Новости» (заполняется ботом из мониторинга)
    news = get_text("Новости")
    # Поле «Чем занимается»
    occupation = get_text("Чем занимается")

    return {
        "page_id": page["id"],
        "name": get_title(),
        "circle": circle,
        "priority": priority,
        "last_contact": last_contact_str,
        "next_contact": next_contact_str,
        "computed_next": computed_next,
        "frequency": frequency,
        "tg_personal": tg_personal,
        "tg_username": tg_username,
        "telegram_channel": get_url("Telegram канал"),
        "instagram": get_url("Insta"),
        "birthday": get_date_val("ДР"),
        "notes": get_text("Заметки"),
        "news": news,
        "occupation": occupation,
        "notion_url": f"https://www.notion.so/{page['id'].replace('-', '')}",
    }


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


# ── Формирование карточек ─────────────────────────────────────────────────────
def build_contact_card(c):
    """Формирует текст карточки контакта для блока «Пора связаться»."""
    today = date.today()

    priority_emoji = {"Высокий": "🔴", "Средний": "🟡", "Низкий": "🟢"}.get(c["priority"], "⚪")

    # Имя — активная ссылка на личный TG если есть
    if c["tg_username"]:
        name_link = f'<a href="https://t.me/{c["tg_username"]}">{c["name"]}</a>'
    else:
        name_link = f'<b>{c["name"]}</b>'

    lines = [f'{priority_emoji} {name_link} · {c["circle"]}']

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

    # Чем занимается
    if c["occupation"]:
        lines.append(f"💼 {c['occupation']}")

    # Новости из мониторинга (до 3 строк)
    if c["news"]:
        news_lines = [l.strip() for l in c["news"].split("\n") if l.strip()]
        if news_lines:
            lines.append("")
            lines.append("📌 <b>Последнее:</b>")
            lines.extend(news_lines[:3])

    # Контакты
    lines.append("")
    if c["tg_username"]:
        lines.append(f'✈️ <a href="https://t.me/{c["tg_username"]}">Написать в Telegram</a>')
    if c["instagram"]:
        ig_url = c["instagram"]
        ig_handle = ig_url.rstrip("/").split("/")[-1].lstrip("@")
        lines.append(f'📸 <a href="{ig_url}">@{ig_handle}</a> в Instagram')

    return "\n".join(lines)


def build_keyboard_normal(c):
    """Клавиатура для блока «Пора связаться»."""
    page_id = c["page_id"]
    row1 = [{"text": "✅ Связался", "callback_data": f"done|{page_id}"}]
    row2 = [
        {"text": "⏭ Через неделю", "callback_data": f"snooze|{page_id}"},
        {"text": "🗑 Удалить", "callback_data": f"delete|{page_id}"},
    ]
    row3 = [{"text": "📋 Открыть в Notion", "callback_data": f"notion|{page_id}"}]
    return {"inline_keyboard": [row1, row2, row3]}


def build_keyboard_empty(c):
    """Клавиатура для блока «Обновление базы»."""
    page_id = c["page_id"]
    return {
        "inline_keyboard": [
            [
                {"text": "📅 Недавно", "callback_data": f"recent|{page_id}"},
                {"text": "🕐 1-3 месяца", "callback_data": f"medium|{page_id}"},
            ],
            [
                {"text": "⏳ Давно (3+)", "callback_data": f"long_ago|{page_id}"},
                {"text": "🗑 Удалить", "callback_data": f"delete|{page_id}"},
            ]
        ]
    }


# ── Обработка нажатий кнопок ──────────────────────────────────────────────────
def process_callbacks():
    """Обрабатывает накопившиеся нажатия кнопок."""
    updates = tg_get_updates()
    if not updates:
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

        msg_id  = callback["message"]["message_id"]
        chat_id = callback["message"]["chat"]["id"]
        orig_text = callback["message"].get("text", "")

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
                    "recent": "📅 Недавно (в течение 2 недель)",
                    "medium": "🕐 1-3 месяца назад",
                    "long_ago": "⏳ Давно (3+ месяца)"
                }
                label = labels[action]
                tg_answer_callback(callback["id"], f"✅ Записано")
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

        except Exception as e:
            print(f"  Ошибка обработки callback {action}: {e}")
            tg_answer_callback(callback["id"], "⚠️ Ошибка, попробуй ещё раз")

    if processed:
        print(f"  Обработано нажатий: {processed}")

    # Подтверждаем обработку обновлений
    if last_update_id:
        requests.get(f"{TG_API}/getUpdates",
                     params={"offset": last_update_id + 1, "limit": 1}, timeout=10)


# ── Новостная лента ───────────────────────────────────────────────────────────
def build_news_feed(due_contacts):
    """
    Формирует блок новостей из поля «Новости» контактов, которые в дайджесте.
    Берёт первую строку из «Новостей» каждого контакта как заголовок события.
    """
    news_items = []
    for c in due_contacts:
        if not c["news"]:
            continue
        lines = [l.strip() for l in c["news"].split("\n") if l.strip()]
        if not lines:
            continue
        # Берём первую строку как самое свежее событие
        first_line = lines[0]
        # Имя — ссылка на TG если есть
        if c["tg_username"]:
            name_link = f'<a href="https://t.me/{c["tg_username"]}">{c["name"]}</a>'
        else:
            name_link = f'<b>{c["name"]}</b>'
        news_items.append(f"• {name_link} — {first_line}")

    return news_items


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

    # Фильтруем контакты
    due_contacts   = []  # Срок подошёл
    empty_contacts = []  # Нет данных, высокий приоритет

    for c in contacts:
        if c["circle"] not in ACTIVE_CIRCLES:
            continue
        if c["computed_next"] and c["computed_next"] <= cutoff:
            due_contacts.append(c)
        elif not c["last_contact"] and not c["next_contact"] and c["priority"] == "Высокий":
            empty_contacts.append(c)

    # Сортируем: сначала самые просроченные, потом по приоритету
    priority_order = {"Высокий": 0, "Средний": 1, "Низкий": 2}
    due_contacts.sort(key=lambda x: (
        x["computed_next"] or date.max,
        priority_order.get(x["priority"], 9)
    ))

    # Ограничиваем до MAX_DUE_CONTACTS
    due_contacts_display = due_contacts[:MAX_DUE_CONTACTS]
    due_total = len(due_contacts)

    print(f"  Контактов в дайджесте: {due_total} (показываем {len(due_contacts_display)})")
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
                birthday_alerts.append((c["name"], days_until, bday_this_year, c.get("tg_username")))
        except Exception:
            pass

    # Если нечего отправлять
    if not due_contacts and not empty_contacts and not birthday_alerts:
        tg_send("☀️ <b>Доброе утро!</b>\n\nСегодня нет контактов, требующих внимания. Хороший день!")
        print("  Нет контактов для дайджеста")
        return

    # ── Блок 1: Заголовок + дни рождения ──────────────────────────────────────
    header_lines = [f"☀️ <b>Дайджест · {today.strftime('%d.%m.%Y')}</b>"]

    if birthday_alerts:
        header_lines.append("")
        header_lines.append("🎂 <b>Дни рождения:</b>")
        for name, days, bday, tg_u in sorted(birthday_alerts, key=lambda x: x[1]):
            name_str = f'<a href="https://t.me/{tg_u}">{name}</a>' if tg_u else f'<b>{name}</b>'
            if days == 0:
                header_lines.append(f"  🎉 {name_str} — сегодня!")
            elif days == 1:
                header_lines.append(f"  🎂 {name_str} — завтра")
            else:
                header_lines.append(f"  🎂 {name_str} — через {days} дн. ({bday.strftime('%d.%m')})")

    # ── Блок 2: Новостная лента ────────────────────────────────────────────────
    news_items = build_news_feed(due_contacts_display)
    if news_items:
        header_lines.append("")
        header_lines.append("━━━ 📰 <b>НОВОСТИ</b> ━━━")
        header_lines.extend(news_items)

    # ── Блок 3: Сводка «Пора связаться» ───────────────────────────────────────
    if due_contacts:
        header_lines.append("")
        header_lines.append("━━━ 📞 <b>ПОРА СВЯЗАТЬСЯ</b> ━━━")
        if due_total > MAX_DUE_CONTACTS:
            header_lines.append(f"<i>Показываю {MAX_DUE_CONTACTS} из {due_total} — самые горячие</i>")

    tg_send("\n".join(header_lines))
    time.sleep(0.5)

    # Карточки контактов
    for c in due_contacts_display:
        card_text = build_contact_card(c)
        keyboard  = build_keyboard_normal(c)
        tg_send(card_text, reply_markup=keyboard)
        time.sleep(0.3)

    # ── Блок 4: Обновление базы ────────────────────────────────────────────────
    if empty_contacts:
        time.sleep(0.5)
        tg_send(
            f"━━━ ❓ <b>ОБНОВЛЕНИЕ БАЗЫ</b> ━━━\n"
            f"Нет данных по {len(empty_contacts)} контактам с высоким приоритетом.\n"
            f"<i>Когда последний раз общались?</i>"
        )
        for c in empty_contacts[:MAX_EMPTY_PER_DAY]:
            if c["tg_username"]:
                name_link = f'<a href="https://t.me/{c["tg_username"]}">{c["name"]}</a>'
            else:
                name_link = f'<b>{c["name"]}</b>'
            card_text = f"👤 {name_link} · {c['circle']}\n📅 Дата последнего контакта неизвестна"
            keyboard  = build_keyboard_empty(c)
            tg_send(card_text, reply_markup=keyboard)
            time.sleep(0.3)

    print(f"[{datetime.now().isoformat()}] Дайджест отправлен")


if __name__ == "__main__":
    main()
