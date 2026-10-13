"""Модель распознанного документа и проверка того, что вернула модель.

Модель просим отвечать JSON с русскими ключами: так промпт ближе к самому
документу и меньше путаницы в полях. В коде работаем с английскими именами
через алиасы.

LLM может ошибиться в цифре или пропустить строку. Поэтому после разбора
документ проверяется арифметикой: сумма по строкам против итога, количество ×
цена против суммы строки, формат БИН. Найденные расхождения не блокируют
документ, а показываются бухгалтеру — проверять ему или нет, решает он.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Допуск на округление в документах: копейки/тиыны.
TOLERANCE = Decimal("1.00")


def parse_amount(value: str | int | float | None) -> Decimal | None:
    """'1 250 000,50' / '1250000.5' / '1 250 000 ₸' → Decimal. Пусто или мусор → None."""
    if value is None:
        return None
    text = str(value).replace(" ", " ").replace("₸", "").replace("тг", "").strip()
    text = re.sub(r"\s+", "", text).replace(",", ".")
    if not text:
        return None
    # «1.250.000.50» — точки как разделители разрядов: оставляем последнюю.
    if text.count(".") > 1:
        head, _, tail = text.rpartition(".")
        text = head.replace(".", "") + "." + tail
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class Party(_Model):
    name: str = Field("", alias="название")
    bin: str = Field("", alias="ИИН_БИН")


class Line(_Model):
    name: str = Field("", alias="название")
    description: str = Field("", alias="описание")
    quantity: str = Field("", alias="количество")
    unit: str = Field("", alias="единица")
    price: str = Field("", alias="цена")
    amount: str = Field("", alias="сумма")

    @property
    def title(self) -> str:
        return self.name or self.description


class Document(_Model):
    kind: Literal["накладная", "авр"] = Field("накладная", alias="тип")
    number: str = Field("", alias="номер")
    date: str = Field("", alias="дата")
    # Накладная: поставщик → получатель. Акт: исполнитель → заказчик.
    supplier: Party | None = Field(None, alias="поставщик")
    buyer: Party | None = Field(None, alias="получатель")
    contractor: Party | None = Field(None, alias="исполнитель")
    customer: Party | None = Field(None, alias="заказчик")
    goods: list[Line] = Field(default_factory=list, alias="товары")
    works: list[Line] = Field(default_factory=list, alias="работы")
    total: str = Field("", alias="итого")
    vat: str = Field("", alias="НДС")
    total_with_vat: str = Field("", alias="итого_с_НДС")

    @property
    def is_act(self) -> bool:
        return self.kind == "авр"

    @property
    def counterparty(self) -> Party:
        party = self.contractor if self.is_act else self.supplier
        return party or Party()

    @property
    def lines(self) -> list[Line]:
        return self.works if self.is_act else self.goods

    @property
    def grand_total(self) -> str:
        return self.total_with_vat or self.total


def check(doc: Document) -> list[str]:
    """Арифметическая проверка документа. Возвращает список замечаний."""
    issues: list[str] = []

    if not doc.number:
        issues.append("Не найден номер документа")
    if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", doc.date.strip()):
        issues.append(f"Дата не в формате ДД.ММ.ГГГГ: «{doc.date}»")

    bin_ = re.sub(r"\D", "", doc.counterparty.bin)
    if len(bin_) != 12:
        issues.append(f"ИИН/БИН контрагента должен содержать 12 цифр: «{doc.counterparty.bin}»")

    if not doc.lines:
        issues.append("Не распознано ни одной строки")
        return issues

    lines_sum = Decimal(0)
    for i, line in enumerate(doc.lines, start=1):
        qty, price, amount = (parse_amount(v) for v in (line.quantity, line.price, line.amount))
        if amount is None:
            issues.append(f"Строка {i}: не распознана сумма")
            continue
        lines_sum += amount
        if qty is not None and price is not None and abs(qty * price - amount) > TOLERANCE:
            issues.append(f"Строка {i}: {line.quantity} × {line.price} ≠ {line.amount}")

    total = parse_amount(doc.total)
    if total is not None and abs(lines_sum - total) > TOLERANCE:
        issues.append(f"Сумма строк {lines_sum} не совпадает с итогом {doc.total}")

    vat = parse_amount(doc.vat)
    with_vat = parse_amount(doc.total_with_vat)
    if total is not None and vat is not None and with_vat is not None:
        if abs(total + vat - with_vat) > TOLERANCE:
            issues.append(f"Итого {doc.total} + НДС {doc.vat} ≠ {doc.total_with_vat}")

    return issues
