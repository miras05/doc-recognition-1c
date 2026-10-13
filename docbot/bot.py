"""Telegram-бот: бухгалтер присылает фото или PDF — получает черновик в 1С и Excel.

Запуск:  python -m docbot.bot   (переменные окружения — см. .env.example)
"""

from __future__ import annotations

import asyncio
import logging
import os

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from docbot.excel import make_excel
from docbot.extractor import GeminiExtractor, RecognitionError
from docbot.onec import OneCClient
from docbot.pipeline import process

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger(__name__)

MAX_FILE_MB = 20


async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Бот распознавания документов\n\n"
        "Пришлите фото или PDF накладной либо акта выполненных работ.\n"
        "Я распознаю данные, проверю суммы, создам черновик в 1С и пришлю Excel."
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    status = await message.reply_text("📥 Получил. Распознаю…")

    if message.photo:
        tg_file, filename = await message.photo[-1].get_file(), "photo.jpg"
    elif message.document:
        if (message.document.file_size or 0) > MAX_FILE_MB * 1024 * 1024:
            await status.edit_text(f"⚠️ Файл больше {MAX_FILE_MB} МБ.")
            return
        tg_file, filename = await message.document.get_file(), message.document.file_name or "document.pdf"
    else:
        await status.edit_text("⚠️ Пришлите фото или PDF документа.")
        return

    data = bytes(await tg_file.download_as_bytearray())
    deps = context.application.bot_data

    try:
        # Распознавание и запрос к 1С — блокирующие вызовы, уносим из event loop.
        result = await asyncio.to_thread(process, data, filename, deps["extractor"], deps["onec"])
    except RecognitionError:
        log.exception("Не удалось распознать %s", filename)
        await status.edit_text("❌ Не удалось распознать документ. Попробуйте более чёткое фото.")
        return
    except Exception:
        log.exception("Ошибка обработки %s", filename)
        await status.edit_text("❌ Техническая ошибка. Попробуйте ещё раз позже.")
        return

    await status.edit_text(result.summary())
    doc = result.document
    await message.reply_document(
        document=make_excel(doc, result.issues),
        filename=f"{doc.kind}_{doc.number or 'без_номера'}.xlsx",
        caption="📊 Результат распознавания",
    )


def main() -> None:
    token = os.environ.get("TELEGRAM_TOKEN", "")
    if not token:
        raise SystemExit("Задайте TELEGRAM_TOKEN")

    app = Application.builder().token(token).build()
    app.bot_data["extractor"] = GeminiExtractor()
    app.bot_data["onec"] = OneCClient()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_document))
    log.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
