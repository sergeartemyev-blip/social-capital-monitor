#!/usr/bin/env python3
"""
Social Capital Monitor
Читает базу Notion, определяет контакты для мониторинга,
собирает данные из Instagram, Telegram, YouTube
и отправляет дайджест в Telegram.
"""

import os
import json
import requests
from datetime import datetime, timedelta, date
from notion_client import Client

# ── Конфигурация ──────────────────────────────────────────────────────────────
NOTION_TOKEN       = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]   # b03e121d-30da-4657-b092-27bfcb449f23
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
YOUTUBE_API_KEY    = os.environ.get("YOUTUBE_API_KEY", "")

# Категории, которые мониторим (исключаем врагов/конкурентов и "Не помню")
MONITORED_CIRCLES = {
    "Клиент активный",
    "Клиент бывший",
    "Партнер",
    "Близкий круг",
    "Знакомый",
    "Зона развития",
}

# За сколько дней до срока начинаем мониторинг
MONITOR_DAYS_BEFORE = 6  # начинаем за 5-7 дней (берём 6 как середину)

# ── Notion ────────────────────────────────────────────────────────────────────
def get_contacts_to_monitor():
    """Возвращает контакты, у которых срок касания наступает через MONITOR_DAYS_BEFORE дней или уже прошёл."""
    notion = Client(auth=NOTION_TOKEN)
    today = date.today()
    cutoff = today + timedelta(days=MONITOR_DAYS_BEFORE)

    results = []
    cursor = None

    while True:
        kwargs = {
            "database_id": NOTION_DATABASE_ID,
            "filter": {
                "and": [
                    {
                        "property": "Круг",
                        "select": {"is_not_empty": True}
                    },
                    {
                        "property": "Следующий контакт",
                        "date": {"on_or_before": cutoff.isoformat()}
                    }
                ]
            }
        }
        if cursor:
            kwargs["start_cursor"] = cursor

        response = notion.databases.query(**kwargs)
        results.extend(response["results"])

        if not response.get("has_more"):
            break
        cursor = response["next_cursor"]

    # Фильтруем по нужным категориям
    contacts = []
    for page in results:
        props = page["properties"]
        circle = props.get("Круг", {}).get("select")
        if not circle or circle.get("name") not in MONITORED_CIRCLES:
            continue

        def get_url(field):
            v = props.get(field, {}).get("url")
            return v if v else None

        def get_text(field):
            rich = props.get(field, {}).get("rich_text", [])
            return "".join(r.get("plain_text", "") for r in rich) if rich else None

        def get_date(field):
            d = props.get(field, {}).get("date")
            return d.get("start") if d else None

        def get_title():
            t = props.get("Имя", {}).get("title", [])
            return "".join(r.get("plain_text", "") for r in t) if t else "Без имени"

        contacts.append({
            "page_id": page["id"],
            "name": get_title(),
            "circle": circle.get("name"),
            "instagram": get_url("Insta"),
            "telegram_channel": get_url("Telegram канал"),
            "youtube": get_url("YouTube"),
            "birthday": get_date("ДР"),
            "next_contact": get_date("Следующий контакт"),
            "last_contact": get_date("Последний контакт"),
            "frequency": props.get("Частота контактов дни", {}).get("number"),
            "notes": get_text("Заметки"),
            "goals": get_text("Цели"),
            "manus_command": get_text("Команда для Manus"),
        })

    return contacts


# ── Instagram (без авторизации, публичные профили) ────────────────────────────
def extract_instagram_username(url):
    if not url:
        return None
    url = url.rstrip("/")
    parts = url.split("/")
    for i, p in enumerate(parts):
        if p in ("instagram.com", "www.instagram.com") and i + 1 < len(parts):
            return parts[i + 1].lstrip("@")
    return None


def get_instagram_posts(username, max_posts=3):
    """Получаем последние посты через публичный веб-интерфейс Instagram."""
    if not username:
        return []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        }
        # Используем Picuki как зеркало для публичных профилей
        url = f"https://www.picuki.com/profile/{username}"
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return []

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        posts = []

        # Ищем посты в разметке Picuki
        for item in soup.select(".photo-description")[:max_posts]:
            text = item.get_text(strip=True)
            if text:
                posts.append({"text": text[:300], "source": "instagram"})

        return posts
    except Exception as e:
        print(f"  Instagram error for @{username}: {e}")
        return []


# ── Telegram ──────────────────────────────────────────────────────────────────
def extract_telegram_channel(url):
    if not url:
        return None
    url = url.rstrip("/")
    parts = url.split("/")
    for i, p in enumerate(parts):
        if p in ("t.me", "telegram.me") and i + 1 < len(parts):
            ch = parts[i + 1].lstrip("@")
            # Исключаем личные ссылки (начинаются с +)
            if not ch.startswith("+"):
                return ch
    return None


def get_telegram_posts(channel, max_posts=3):
    """Получаем последние посты из публичного Telegram-канала через t.me/s/."""
    if not channel:
        return []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        url = f"https://t.me/s/{channel}"
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return []

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        posts = []

        for msg in soup.select(".tgme_widget_message_text")[:max_posts]:
            text = msg.get_text(separator=" ", strip=True)
            if text and len(text) > 10:
                posts.append({"text": text[:400], "source": "telegram"})

        return posts
    except Exception as e:
        print(f"  Telegram error for @{channel}: {e}")
        return []


# ── YouTube ───────────────────────────────────────────────────────────────────
def extract_youtube_channel_id(url):
    if not url:
        return None
    # Поддерживаем форматы: /channel/UC..., /@username, /c/name
    url = url.rstrip("/")
    if "/channel/" in url:
        return url.split("/channel/")[-1].split("/")[0]
    if "/@" in url:
        return url.split("/@")[-1].split("/")[0]  # это handle, не ID
    if "/c/" in url:
        return url.split("/c/")[-1].split("/")[0]
    return None


def get_youtube_videos(channel_url, max_videos=2):
    """Получаем последние видео через YouTube Data API v3."""
    if not channel_url or not YOUTUBE_API_KEY:
        return []
    try:
        handle = extract_youtube_channel_id(channel_url)
        if not handle:
            return []

        # Сначала получаем channel ID по handle
        search_url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "part": "snippet",
            "q": handle,
            "type": "channel",
            "maxResults": 1,
            "key": YOUTUBE_API_KEY,
        }
        resp = requests.get(search_url, params=params, timeout=10)
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return []

        channel_id = items[0]["id"]["channelId"]

        # Получаем последние видео
        params2 = {
            "part": "snippet",
            "channelId": channel_id,
            "order": "date",
            "maxResults": max_videos,
            "type": "video",
            "key": YOUTUBE_API_KEY,
        }
        resp2 = requests.get(search_url, params=params2, timeout=10)
        data2 = resp2.json()

        videos = []
        for item in data2.get("items", []):
            snippet = item["snippet"]
            title = snippet.get("title", "")
            desc = snippet.get("description", "")[:200]
            pub = snippet.get("publishedAt", "")[:10]
            vid_id = item["id"].get("videoId", "")
            videos.append({
                "text": f"📹 {title} ({pub})\n{desc}",
                "url": f"https://youtu.be/{vid_id}",
                "source": "youtube"
            })
        return videos
    except Exception as e:
        print(f"  YouTube error: {e}")
        return []


# ── Дни рождения ──────────────────────────────────────────────────────────────
def check_birthdays(contacts):
    today = date.today()
    upcoming = []
    for c in contacts:
        if not c.get("birthday"):
            continue
        try:
            bday = date.fromisoformat(c["birthday"])
            this_year = bday.replace(year=today.year)
            if this_year < today:
                this_year = bday.replace(year=today.year + 1)
            days_left = (this_year - today).days
            if 0 <= days_left <= 14:
                upcoming.append({
                    "name": c["name"],
                    "days_left": days_left,
                    "date": this_year.strftime("%d.%m"),
                })
        except Exception:
            pass
    return sorted(upcoming, key=lambda x: x["days_left"])


# ── Формирование дайджеста ────────────────────────────────────────────────────
def format_digest(contacts_data, birthdays):
    today = date.today().strftime("%d.%m.%Y")
    lines = [f"📊 *Дайджест социального капитала* — {today}\n"]

    # Дни рождения
    if birthdays:
        lines.append("🎂 *Дни рождения:*")
        for b in birthdays:
            if b["days_left"] == 0:
                lines.append(f"  🎉 Сегодня у {b['name']}!")
            elif b["days_left"] == 1:
                lines.append(f"  ⚠️ Завтра у {b['name']}")
            else:
                lines.append(f"  📅 Через {b['days_left']} дн. у {b['name']} ({b['date']})")
        lines.append("")

    # Контакты для касания
    overdue = [c for c in contacts_data if c.get("overdue")]
    upcoming = [c for c in contacts_data if not c.get("overdue")]

    if overdue:
        lines.append(f"🔴 *Просрочено касаний: {len(overdue)}*")
        for c in overdue:
            lines.append(f"\n👤 *{c['name']}* ({c['circle']})")
            if c.get("news"):
                for n in c["news"][:2]:
                    src_icon = {"instagram": "📸", "telegram": "✈️", "youtube": "▶️"}.get(n["source"], "•")
                    lines.append(f"  {src_icon} {n['text'][:200]}")
            else:
                lines.append("  _(новостей не найдено)_")
        lines.append("")

    if upcoming:
        lines.append(f"🟡 *Скоро касание: {len(upcoming)}*")
        for c in upcoming:
            next_date = c.get("next_contact", "?")
            lines.append(f"\n👤 *{c['name']}* ({c['circle']}) — {next_date}")
            if c.get("news"):
                for n in c["news"][:2]:
                    src_icon = {"instagram": "📸", "telegram": "✈️", "youtube": "▶️"}.get(n["source"], "•")
                    lines.append(f"  {src_icon} {n['text'][:200]}")
            else:
                lines.append("  _(новостей не найдено)_")

    if not overdue and not upcoming:
        lines.append("✅ Сегодня нет контактов для касания.")

    return "\n".join(lines)


# ── Отправка в Telegram ───────────────────────────────────────────────────────
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Разбиваем на части если текст длинный (лимит 4096 символов)
    max_len = 4000
    parts = [text[i:i+max_len] for i in range(0, len(text), max_len)]
    for part in parts:
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": part,
            "parse_mode": "Markdown",
        }, timeout=15)
        if not resp.ok:
            print(f"Telegram send error: {resp.text}")


# ── Главная функция ───────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().isoformat()}] Запуск мониторинга...")

    # 1. Получаем контакты из Notion
    print("Читаем базу Notion...")
    contacts = get_contacts_to_monitor()
    print(f"  Найдено контактов для мониторинга: {len(contacts)}")

    if not contacts:
        send_telegram("✅ *Дайджест:* Сегодня нет контактов для касания. Хорошего дня!")
        return

    # 2. Проверяем дни рождения (по всем контактам из базы, не только текущим)
    birthdays = check_birthdays(contacts)

    # 3. Собираем новости по каждому контакту
    today = date.today()
    contacts_data = []
    for c in contacts:
        print(f"  Мониторинг: {c['name']}...")
        news = []

        # Instagram
        ig_user = extract_instagram_username(c.get("instagram"))
        if ig_user:
            posts = get_instagram_posts(ig_user)
            news.extend(posts)

        # Telegram
        tg_channel = extract_telegram_channel(c.get("telegram_channel"))
        if tg_channel:
            posts = get_telegram_posts(tg_channel)
            news.extend(posts)

        # YouTube
        if c.get("youtube"):
            videos = get_youtube_videos(c["youtube"])
            news.extend(videos)

        # Определяем просрочен ли контакт
        overdue = False
        if c.get("next_contact"):
            try:
                next_dt = date.fromisoformat(c["next_contact"])
                overdue = next_dt <= today
            except Exception:
                pass

        contacts_data.append({**c, "news": news, "overdue": overdue})

    # 4. Формируем и отправляем дайджест
    digest = format_digest(contacts_data, birthdays)
    print("Отправляем дайджест в Telegram...")
    send_telegram(digest)
    print("Готово!")


if __name__ == "__main__":
    main()
