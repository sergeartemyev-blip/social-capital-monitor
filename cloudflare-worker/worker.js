/**
 * Social Capital Monitor — Cloudflare Worker
 * Принимает Telegram webhook, обрабатывает нажатия кнопок,
 * обновляет Notion и отвечает мгновенным toast-уведомлением.
 *
 * Переменные окружения (Cloudflare Secrets):
 *   TELEGRAM_BOT_TOKEN  — токен бота
 *   NOTION_TOKEN        — токен интеграции Notion
 *   NOTION_DATABASE_ID  — ID базы данных Notion
 *   WEBHOOK_SECRET      — случайная строка для защиты вебхука
 */

const TG_API_BASE = "https://api.telegram.org/bot";
const NOTION_API  = "https://api.notion.com/v1";

// ── Telegram helpers ──────────────────────────────────────────────────────────
async function tgAnswerCallback(token, callbackQueryId, text, showAlert = false) {
  await fetch(`${TG_API_BASE}${token}/answerCallbackQuery`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      callback_query_id: callbackQueryId,
      text: text,
      show_alert: showAlert,
    }),
  });
}

async function tgEditMessage(token, chatId, messageId, text) {
  // Убираем кнопки
  await fetch(`${TG_API_BASE}${token}/editMessageReplyMarkup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      message_id: messageId,
      reply_markup: JSON.stringify({ inline_keyboard: [] }),
    }),
  });
  // Обновляем текст с подтверждением
  await fetch(`${TG_API_BASE}${token}/editMessageText`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      message_id: messageId,
      text: text,
      parse_mode: "HTML",
      disable_web_page_preview: true,
    }),
  });
}

// ── Notion helpers ─────────────────────────────────────────────────────────────
async function notionUpdate(notionToken, pageId, properties) {
  const resp = await fetch(`${NOTION_API}/pages/${pageId}`, {
    method: "PATCH",
    headers: {
      "Authorization": `Bearer ${notionToken}`,
      "Content-Type": "application/json",
      "Notion-Version": "2022-06-28",
    },
    body: JSON.stringify({ properties }),
  });
  return resp.ok;
}

function todayISO() {
  return new Date().toISOString().split("T")[0];
}

function daysAgoISO(days) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().split("T")[0];
}

function daysFromNowISO(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().split("T")[0];
}

async function updateLastContact(notionToken, pageId, isoDate) {
  return notionUpdate(notionToken, pageId, {
    "Последний контакт": { date: { start: isoDate } }
  });
}

async function updateNextContact(notionToken, pageId, isoDate) {
  return notionUpdate(notionToken, pageId, {
    "Следующий контакт": { date: { start: isoDate } }
  });
}

async function archiveContact(notionToken, pageId) {
  const resp = await fetch(`${NOTION_API}/pages/${pageId}`, {
    method: "PATCH",
    headers: {
      "Authorization": `Bearer ${notionToken}`,
      "Content-Type": "application/json",
      "Notion-Version": "2022-06-28",
    },
    body: JSON.stringify({ archived: true }),
  });
  return resp.ok;
}

// ── Главный обработчик ────────────────────────────────────────────────────────
export default {
  async fetch(request, env) {
    // Проверяем секрет вебхука
    const url = new URL(request.url);
    const secret = url.searchParams.get("secret");
    if (secret !== env.WEBHOOK_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }

    if (request.method !== "POST") {
      return new Response("OK", { status: 200 });
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return new Response("Bad Request", { status: 400 });
    }

    const callback = body.callback_query;
    if (!callback) {
      // Не callback — просто подтверждаем получение
      return new Response("OK", { status: 200 });
    }

    const data     = callback.data || "";
    const parts    = data.split("|");
    const action   = parts[0];
    const pageId   = parts[1] || null;

    const msgId   = callback.message.message_id;
    const chatId  = callback.message.chat.id;
    const origText = callback.message.text || "";

    const token       = env.TELEGRAM_BOT_TOKEN;
    const notionToken = env.NOTION_TOKEN;

    try {
      if (action === "done" && pageId) {
        await updateLastContact(notionToken, pageId, todayISO());
        await tgAnswerCallback(token, callback.id, "✅ Отмечено! Дата обновлена");
        await tgEditMessage(token, chatId, msgId,
          origText + "\n\n<i>✅ Связался сегодня — дата обновлена</i>");

      } else if (action === "snooze" && pageId) {
        await updateNextContact(notionToken, pageId, daysFromNowISO(7));
        await tgAnswerCallback(token, callback.id, "⏭ Перенесено на неделю");
        await tgEditMessage(token, chatId, msgId,
          origText + "\n\n<i>⏭ Перенесено на 7 дней</i>");

      } else if (action === "recent" && pageId) {
        await updateLastContact(notionToken, pageId, daysAgoISO(7));
        await tgAnswerCallback(token, callback.id, "✅ Записано");
        await tgEditMessage(token, chatId, msgId,
          origText + "\n\n<i>✅ Недавно (в течение 2 недель)</i>");

      } else if (action === "medium" && pageId) {
        await updateLastContact(notionToken, pageId, daysAgoISO(45));
        await tgAnswerCallback(token, callback.id, "✅ Записано");
        await tgEditMessage(token, chatId, msgId,
          origText + "\n\n<i>✅ 1-3 месяца назад</i>");

      } else if (action === "long_ago" && pageId) {
        await updateLastContact(notionToken, pageId, daysAgoISO(120));
        await tgAnswerCallback(token, callback.id, "✅ Записано");
        await tgEditMessage(token, chatId, msgId,
          origText + "\n\n<i>✅ Давно (3+ месяца)</i>");

      } else if (action === "delete" && pageId) {
        await archiveContact(notionToken, pageId);
        await tgAnswerCallback(token, callback.id, "🗑 Контакт архивирован");
        await tgEditMessage(token, chatId, msgId,
          origText + "\n\n<i>🗑 Удалён из базы</i>");

      } else {
        await tgAnswerCallback(token, callback.id, "");
      }
    } catch (err) {
      console.error("Error:", err);
      await tgAnswerCallback(token, callback.id, "⚠️ Ошибка, попробуй ещё раз");
    }

    return new Response("OK", { status: 200 });
  }
};
