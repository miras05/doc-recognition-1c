"""Создание документа в 1С через стандартный интерфейс OData.

Код одинаков для mock-сервера и для настоящей 1С: меняется только адрес
и учётные данные. Документ создаётся черновиком (Posted=false) — проводит
его бухгалтер после проверки.
"""

from __future__ import annotations

import os

import httpx

from docbot.schemas import Document

# Имена документов зависят от конфигурации 1С. Для Бухгалтерии для Казахстана:
DOC_NAMES = {
    "накладная": os.environ.get("ODATA_DOC_INVOICE", "ПоступлениеТоваровУслуг"),
    "авр": os.environ.get("ODATA_DOC_ACT", "АктВыполненныхРабот"),
}


def date_to_1c(date: str) -> str:
    """'12.06.2026' → '2026-06-12T00:00:00'. Если формат другой — отдаём как есть."""
    try:
        day, month, year = date.strip().split(".")
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}T00:00:00"
    except (ValueError, AttributeError):
        return date


def build_payload(doc: Document, issues: list[str] | None = None) -> dict:
    comment = f"Создано AI / {doc.kind}"
    if issues:
        comment += " / ПРОВЕРИТЬ: " + "; ".join(issues)
    return {
        "Number": doc.number,
        "Date": date_to_1c(doc.date),
        "Posted": False,
        "Контрагент": doc.counterparty.name,
        "БИН": doc.counterparty.bin,
        "СуммаДокумента": doc.grand_total,
        "НДС": doc.vat,
        "Комментарий": comment,
        "Товары": [
            {"Номенклатура": line.title, "Количество": line.quantity, "Цена": line.price, "Сумма": line.amount}
            for line in doc.lines
        ],
    }


class OneCClient:
    def __init__(
        self,
        base_url: str | None = None,
        user: str | None = None,
        password: str | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("ODATA_URL", "http://127.0.0.1:8000/odata/standard.odata")).rstrip("/")
        user = user if user is not None else os.environ.get("ODATA_USER", "")
        password = password if password is not None else os.environ.get("ODATA_PASS", "")
        self._http = http or httpx.Client(timeout=30.0, auth=(user, password) if user else None)

    def create_document(self, doc: Document, issues: list[str] | None = None) -> dict:
        url = f"{self.base_url}/Document_{DOC_NAMES[doc.kind]}?$format=json"
        response = self._http.post(url, json=build_payload(doc, issues))
        response.raise_for_status()
        return response.json()
