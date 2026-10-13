"""Конвейер: файл → распознавание → проверка → 1С → отчёт.

Отдельно от Telegram, чтобы его можно было вызвать из бота, из API или из
пакетной обработки папки и чтобы тестировать без сети.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from docbot.schemas import Document, check

log = logging.getLogger(__name__)


class Extractor(Protocol):
    def extract(self, data: bytes, filename: str) -> Document: ...


class OneC(Protocol):
    def create_document(self, doc: Document, issues: list[str] | None = None) -> dict: ...


@dataclass
class Result:
    document: Document
    issues: list[str] = field(default_factory=list)
    onec_number: str | None = None
    onec_error: str | None = None

    def summary(self) -> str:
        doc = self.document
        lines = [
            f"📄 {'Акт выполненных работ' if doc.is_act else 'Накладная'}",
            f"№ {doc.number or '—'} от {doc.date or '—'}",
            f"Контрагент: {doc.counterparty.name or '—'}",
            f"Сумма: {doc.grand_total or '—'} ₸",
            "",
        ]
        if self.onec_number:
            lines.append(f"✅ Черновик создан в 1С, №{self.onec_number}")
        else:
            lines.append(f"⚠️ В 1С не отправлено: {self.onec_error}. Данные — в Excel ниже.")
        if self.issues:
            lines += ["", "🔎 Проверьте:"] + [f"• {issue}" for issue in self.issues]
        return "\n".join(lines)


def process(data: bytes, filename: str, extractor: Extractor, onec: OneC) -> Result:
    document = extractor.extract(data, filename)
    result = Result(document=document, issues=check(document))

    try:
        response = onec.create_document(document, result.issues)
        result.onec_number = str(response.get("Number") or "—")
    except Exception as exc:  # 1С недоступна — распознанные данные всё равно отдаём
        log.exception("Не удалось создать документ в 1С")
        result.onec_error = str(exc)[:120]

    return result
