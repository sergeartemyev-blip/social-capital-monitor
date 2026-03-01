#!/usr/bin/env python3
"""
Social Capital Monitor — Скрипт 1: Мониторинг соцсетей
Собирает посты из Instagram и Telegram, анализирует через Gemini AI,
записывает ключевые события в поле «Новости» в Notion.
Запускается ежедневно через GitHub Actions.
"""
import os
import time
import requests
from datetime import datetime, timedelta, date
from notion_client import Client
from gemini import generate_with_retry # Импортируем новую функцию

# ── Конфигурация ──────────────────────────────────────────────────────────────
NOTION_TOKEN       = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

# За сколько дней до срока начинаем мониторинг
MONITOR_DAYS_BEFORE = 7

# Категории, которые мониторим
MONITORED_CIRCLES = {
    "Клиент активный",
    "Клиент бывший",
    "Партнер",
    "Близкий круг",
    "Знакомый",
    "Зона развития",
}

# Приоритеты для сбора новостей
HIGH_PRIORITY_NEWS = {"Высокий", "Средний"}

# ── Notion helpers ─────────────────────────────────────────────────────────────
def get_contacts_to_monitor():
    """Возвращает контакты, которые нужно мониторить сегодня."""
    notion = Client(auth=NOTION_TOKEN)
    today = date.today()
    cutoff = today + timedelta(days=MONITOR_DAYS_BEFORE)

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

    contacts = []
    for page in all_pages:
        props = page["properties"]

        # Фильтр по кругу
        circle_sel = props.get("Круг", {}).get("select")
        if not circle_sel or circle_sel.get("name") not in MONITORED_CIRCLES:
            continue

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

        name = get_title()
        priority = (props.get("Приоритет", {}).get("select") or {}).get("name", "")
        last_contact = get_date_val("Последний контакт")
        next_contact = get_date_val("Следующий контакт")
        frequency = props.get("Частота контактов дни", {}).get("number")
        instagram = get_url("Insta")
        tg_channel = get_url("Telegram канал")

        # Определяем нужен ли мониторинг
        needs_monitoring = False

        if next_contact:
            try:
                next_dt = date.fromisoformat(next_contact)
                if next_dt <= cutoff:
                    needs_monitoring = True
            except Exception:
                pass
        elif last_contact and frequency:
            try:
                last_dt = date.fromisoformat(last_contact)
                computed_next = last_dt + timedelta(days=int(frequency))
                if computed_next <= cutoff:
                    needs_monitoring = True
            except Exception:
                pass
        elif not last_contact and not next_contact and priority == "Высокий":
            needs_monitoring = True

        if not needs_monitoring:
            continue

        # Есть ли соцсети для мониторинга
        if not instagram and not tg_channel:
            continue

        # Фильтр по приоритету для новостей
        if priority not in HIGH_PRIORITY_NEWS:
            continue

        contacts.append({
            "page_id": page["id"],
            "name": name,
            "priority": priority,
            "instagram": instagram,
            "telegram_channel": tg_channel,
        })

    return contacts


def update_notion_field(page_id, field_name, content):
    """Обновляет указанное текстовое поле в Notion."""
    notion = Client(auth=NOTION_TOKEN)
    notion.pages.update(
        page_id=page_id,
        properties={
            field_name: {
                "rich_text": [{"type": "text", "text": {"content": content[:2000]}}]
            }
        }
    )

# ── Парсинг соцсетей ──────────────────────────────────────────────────────────
def extract_instagram_username(url):
    if not url:
        return None
    url = url.rstrip("/")
    parts = url.split("/")
    for i, p in enumerate(parts):
        if p in ("instagram.com", "www.instagram.com") and i + 1 < len(parts):
            return parts[i + 1].lstrip("@")
    return None

def get_instagram_posts(username, max_posts=5):
    if not username:
        return []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        }
        url = f"https://www.picuki.com/profile/{username}"
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        posts = []
        for item in soup.select(".photo-description")[:max_posts]:
            text = item.get_text(strip=True)
            if text and len(text) > 10:
                posts.append(text[:500])
        return posts
    except Exception as e:
        print(f"  Instagram error @{username}: {e}")
        return []

def extract_telegram_channel(url):
    if not url:
        return None
    url = url.rstrip("/")
    parts = url.split("/")
    for i, p in enumerate(parts):
        if p in ("t.me", "telegram.me") and i + 1 < len(parts):
            ch = parts[i + 1].lstrip("@")
            if not ch.startswith("+"):
                return ch
    return None

def get_telegram_posts(channel, max_posts=5):
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
                posts.append(text[:500])
        return posts
    except Exception as e:
        print(f"  Telegram error @{channel}: {e}")
        return []

# ── AI-анализ через Gemini ────────────────────────────────────────────────────
def analyze_posts_with_gemini(name, posts):
    """Извлекает ключевые события из постов через Gemini AI."""
    if not posts:
        return ""

    posts_text = "\n---\n".join(posts)
    prompt = f"""Ты анализируешь публикации человека по имени {name} в соцсетях.

Вот его последние посты:
{posts_text}

Извлеки ТОЛЬКО ключевые факты и события, которые полезны для личного общения:
- Текущие проекты (с названием и ссылкой если есть)
- Ключевые события (конференции, запуски, достижения)
- Важные изменения в жизни или бизнесе
- Темы, которые его сейчас волнуют

Формат ответа — строго список, каждый пункт начинается с •
Максимум 4-5 пунктов. Только факты, без воды. Если ничего важного нет — напиши "• Нет значимых событий"
Отвечай на русском языке."""

    return generate_with_retry(prompt)

# ── Главная функция ───────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().isoformat()}] Запуск мониторинга соцсетей...")

    contacts = get_contacts_to_monitor()
    print(f"  Контактов для мониторинга: {len(contacts)}")

    for c in contacts:
        name = c["name"]
        print(f"\n  → {name}")

        posts = []

        # Instagram
        ig_user = extract_instagram_username(c.get("instagram"))
        if ig_user:
            ig_posts = get_instagram_posts(ig_user)
            posts.extend(ig_posts)
            print(f"    Instagram @{ig_user}: {len(ig_posts)} постов")

        # Telegram
        tg_ch = extract_telegram_channel(c.get("telegram_channel"))
        if tg_ch:
            tg_posts = get_telegram_posts(tg_ch)
            posts.extend(tg_posts)
            print(f"    Telegram @{tg_ch}: {len(tg_posts)} постов")

        if not posts:
            print(f"    Постов не найдено, пропускаем")
            continue

        # AI-анализ
        analysis = analyze_posts_with_gemini(name, posts)
        if analysis:
            today_str = date.today().strftime("%d.%m.%Y")
            news_content = f"[Обновлено {today_str}]\n{analysis}"
            update_notion_field(c["page_id"], "Новости", news_content)
            print(f"    Новости обновлены в Notion")
        else:
            print(f"    AI не вернул результат")

    print(f"\n[{datetime.now().isoformat()}] Мониторинг завершён")


if __name__ == "__main__":
    main()
