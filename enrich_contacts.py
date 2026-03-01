#!/usr/bin/env python3
"""
Social Capital Monitor — Скрипт 3: Обогащение контактов
Однократно заполняет поле «Чем занимается» для контактов с пустым полем,
используя bio из Telegram-профиля и/или описание канала.
Запускается вручную или раз в месяц через GitHub Actions.
"""
import os
import re
import time
import requests
from datetime import datetime
from notion_client import Client
from gemini import generate_with_retry

# ── Конфигурация ──────────────────────────────────────────────────────────────
NOTION_TOKEN       = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

# Категории, которые обогащаем
ENRICHED_CIRCLES = {
    "Клиент активный",
    "Клиент бывший",
    "Партнер",
    "Близкий круг",
    "Знакомый",
    "Зона развития",
}


# ── Notion helpers ─────────────────────────────────────────────────────────────
def get_contacts_to_enrich():
    """Возвращает контакты с пустым полем «Чем занимается»."""
    notion = Client(auth=NOTION_TOKEN)

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
        if not circle_sel or circle_sel.get("name") not in ENRICHED_CIRCLES:
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

        name = get_title()
        occupation = get_text("Чем занимается")

        # Пропускаем если поле уже заполнено
        if occupation.strip():
            continue

        tg_personal = get_url("Личный TG")
        tg_channel = get_url("Telegram канал")

        # Нужен хотя бы один Telegram-источник
        if not tg_personal and not tg_channel:
            continue

        contacts.append({
            "page_id": page["id"],
            "name": name,
            "tg_personal": tg_personal,
            "tg_channel": tg_channel,
        })

    return contacts


def update_occupation(page_id, occupation):
    """Обновляет поле «Чем занимается» в Notion."""
    notion = Client(auth=NOTION_TOKEN)
    notion.pages.update(
        page_id=page_id,
        properties={
            "Чем занимается": {
                "rich_text": [{"type": "text", "text": {"content": occupation[:2000]}}]
            }
        }
    )


# ── Парсинг Telegram ──────────────────────────────────────────────────────────
def extract_tg_username(url):
    """Извлекает username из t.me ссылки."""
    if not url:
        return None
    url = url.rstrip("/")
    parts = url.split("/")
    for i, p in enumerate(parts):
        if p in ("t.me", "telegram.me") and i + 1 < len(parts):
            username = parts[i + 1].lstrip("@")
            if not username.startswith("+"):
                return username
    return None


def get_telegram_bio(username):
    """Получает bio из публичного Telegram-профиля через t.me."""
    if not username:
        return ""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        }
        url = f"https://t.me/{username}"
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return ""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        # Bio в профиле
        bio_tag = soup.select_one(".tgme_page_description")
        if bio_tag:
            return bio_tag.get_text(separator=" ", strip=True)
        return ""
    except Exception as e:
        print(f"  Telegram bio error @{username}: {e}")
        return ""


def get_telegram_channel_description(channel):
    """Получает описание и последние посты из публичного Telegram-канала."""
    if not channel:
        return "", []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        url = f"https://t.me/s/{channel}"
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return "", []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        # Описание канала
        desc_tag = soup.select_one(".tgme_channel_info_description")
        description = desc_tag.get_text(separator=" ", strip=True) if desc_tag else ""

        # Последние 3 поста для контекста
        posts = []
        for msg in soup.select(".tgme_widget_message_text")[:3]:
            text = msg.get_text(separator=" ", strip=True)
            if text and len(text) > 20:
                posts.append(text[:400])

        return description, posts
    except Exception as e:
        print(f"  Telegram channel error @{channel}: {e}")
        return "", []


# ── Regex fallback ────────────────────────────────────────────────────────────
def clean_bio_regex(bio_text):
    """Простая очистка bio без AI — убирает ссылки, телефоны, @теги."""
    if not bio_text:
        return ""
    # Убираем URL
    text = re.sub(r'https?://\S+', '', bio_text)
    # Убираем @username
    text = re.sub(r'@\w+', '', text)
    # Убираем телефоны
    text = re.sub(r'[\+\d][\d\s\-\(\)]{7,}', '', text)
    # Убираем лишние пробелы и переносы
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:300] if text else ""


# ── AI-обогащение через Gemini ────────────────────────────────────────────────
def generate_occupation(name, bio, channel_desc, sample_posts):
    """Генерирует описание «Чем занимается» через Gemini."""
    context_parts = []
    if bio:
        context_parts.append(f"Bio профиля: {bio}")
    if channel_desc:
        context_parts.append(f"Описание канала: {channel_desc}")
    if sample_posts:
        context_parts.append("Примеры постов:\n" + "\n---\n".join(sample_posts))

    if not context_parts:
        return ""

    context = "\n\n".join(context_parts)
    prompt = f"""Ты помогаешь заполнить CRM-карточку контакта.

Человек: {name}
Источник информации:
{context}

Напиши ОДНО короткое предложение (максимум 15 слов) — чем занимается этот человек профессионально.
Формат: просто текст, без кавычек, без точки в конце, без вводных слов.
Примеры хорошего ответа:
- Основатель EdTech-стартапа, развивает онлайн-образование для взрослых
- Партнёр в венчурном фонде, инвестирует в B2B SaaS
- Маркетолог, помогает брендам расти в соцсетях

Отвечай на русском языке."""

    result = generate_with_retry(prompt)
    if result:
        # Убираем кавычки и лишние символы если AI добавил
        result = result.strip('"\'').strip()
        # Берём только первую строку
        result = result.split('\n')[0].strip()
    return result


# ── Главная функция ───────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().isoformat()}] Запуск обогащения контактов...")

    contacts = get_contacts_to_enrich()
    print(f"  Контактов с пустым «Чем занимается»: {len(contacts)}")

    if not contacts:
        print("  Все контакты уже заполнены, выходим.")
        return

    enriched = 0
    for c in contacts:
        name = c["name"]
        print(f"\n  → {name}")

        bio = ""
        channel_desc = ""
        sample_posts = []

        # Парсим личный профиль
        tg_username = extract_tg_username(c.get("tg_personal"))
        if tg_username:
            bio = get_telegram_bio(tg_username)
            if bio:
                print(f"    Bio: {bio[:80]}...")
            else:
                print(f"    Bio: не найдено")

        # Парсим канал
        tg_channel = extract_tg_username(c.get("tg_channel"))
        if tg_channel:
            channel_desc, sample_posts = get_telegram_channel_description(tg_channel)
            if channel_desc:
                print(f"    Описание канала: {channel_desc[:80]}...")

        # Пробуем AI
        occupation = generate_occupation(name, bio, channel_desc, sample_posts)

        # Fallback: regex-очистка bio
        if not occupation and bio:
            occupation = clean_bio_regex(bio)
            if occupation:
                print(f"    Использован regex-fallback")

        if occupation:
            update_occupation(c["page_id"], occupation)
            print(f"    ✓ Записано: {occupation}")
            enriched += 1
        else:
            print(f"    ✗ Не удалось определить занятие")

        # Пауза между запросами (rate limiting)
        time.sleep(3)

    print(f"\n[{datetime.now().isoformat()}] Обогащение завершено. Обновлено: {enriched}/{len(contacts)}")


if __name__ == "__main__":
    main()
