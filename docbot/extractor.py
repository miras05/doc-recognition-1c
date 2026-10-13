"""Распознавание документа через Gemini Vision.

PDF раскладывается на страницы-картинки, все страницы уходят в один запрос:
у многостраничной накладной таблица товаров часто продолжается на второй странице.
"""

from __future__ import annotations

import json
import os

import fitz  # PyMuPDF

from docbot.schemas import Document

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

PROMPT = """
Ты — система распознавания первичных бухгалтерских документов Казахстана.
Документ на русском или казахском языке.
Сначала определи тип: "накладная" или "авр" (акт выполненных работ).
Затем извлеки данные и верни ТОЛЬКО валидный JSON.

Если накладная:
{
  "тип": "накладная", "номер": "", "дата": "ДД.ММ.ГГГГ",
  "поставщик": { "название": "", "ИИН_БИН": "" },
  "получатель": { "название": "", "ИИН_БИН": "" },
  "товары": [ { "название": "", "количество": "", "единица": "", "цена": "", "сумма": "" } ],
  "итого": "", "НДС": "", "итого_с_НДС": ""
}

Если акт выполненных работ:
{
  "тип": "авр", "номер": "", "дата": "ДД.ММ.ГГГГ",
  "исполнитель": { "название": "", "ИИН_БИН": "" },
  "заказчик": { "название": "", "ИИН_БИН": "" },
  "работы": [ { "описание": "", "количество": "", "единица": "", "цена": "", "сумма": "" } ],
  "итого": "", "НДС": "", "итого_с_НДС": ""
}
Если поле не найдено — пустая строка. Числа как строки, сохраняя формат документа.
"""


class RecognitionError(Exception):
    """Модель вернула не то, что можно разобрать как документ."""


def file_to_images(data: bytes, filename: str, dpi: int = 200) -> list[bytes]:
    """PDF → PNG-страницы. Картинка возвращается как есть."""
    if not filename.lower().endswith(".pdf"):
        return [data]
    with fitz.open(stream=data, filetype="pdf") as pdf:
        return [page.get_pixmap(dpi=dpi).tobytes("png") for page in pdf]


def parse_response(raw: str) -> Document:
    """Ответ модели → Document. Терпим к обёртке ```json ... ```."""
    text = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RecognitionError(f"Модель вернула не JSON: {text[:200]}") from exc
    if not isinstance(payload, dict):
        raise RecognitionError("Модель вернула не объект")
    return Document.model_validate(payload)


class GeminiExtractor:
    def __init__(self, api_key: str | None = None, model: str = MODEL) -> None:
        from google import genai  # тяжёлый импорт — только когда реально нужен

        key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not key:
            raise RuntimeError("GEMINI_API_KEY не задан")
        self._client = genai.Client(api_key=key)
        self._model = model

    def extract(self, data: bytes, filename: str) -> Document:
        from google.genai import types

        parts = [types.Part.from_text(text=PROMPT)]
        mime = "image/png" if filename.lower().endswith((".pdf", ".png")) else "image/jpeg"
        for image in file_to_images(data, filename):
            parts.append(types.Part.from_bytes(data=image, mime_type=mime))

        response = self._client.models.generate_content(
            model=self._model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
        )
        return parse_response(response.text or "")
