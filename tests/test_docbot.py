from __future__ import annotations

from decimal import Decimal

import openpyxl
import pytest
from fastapi.testclient import TestClient

from docbot.excel import make_excel
from docbot.extractor import RecognitionError, file_to_images, parse_response
from docbot.onec import OneCClient, build_payload, date_to_1c
from docbot.pipeline import process
from docbot.schemas import Document, check, parse_amount

INVOICE = {
    "тип": "накладная",
    "номер": "НК-125",
    "дата": "12.06.2026",
    "поставщик": {"название": "ТОО «Поставщик»", "ИИН_БИН": "123456789012"},
    "получатель": {"название": "ТОО «Покупатель»", "ИИН_БИН": "210987654321"},
    "товары": [
        {"название": "Бумага А4", "количество": "10", "единица": "пач", "цена": "2 500", "сумма": "25 000"},
        {"название": "Ручка", "количество": "100", "единица": "шт", "цена": "150,50", "сумма": "15 050"},
    ],
    "итого": "40 050",
    "НДС": "4 806",
    "итого_с_НДС": "44 856",
}

ACT = {
    "тип": "авр",
    "номер": "7",
    "дата": "01.07.2026",
    "исполнитель": {"название": "ИП Исполнитель", "ИИН_БИН": "900101300123"},
    "заказчик": {"название": "ТОО «Заказчик»", "ИИН_БИН": "210987654321"},
    "работы": [{"описание": "Настройка 1С", "количество": "1", "единица": "усл", "цена": "150000", "сумма": "150000"}],
    "итого": "150000",
}


# ------------------------------------------------------------- суммы и проверка

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 250 000", Decimal("1250000")),
        ("1 250 000,50", Decimal("1250000.50")),
        ("1 250 000 ₸", Decimal("1250000")),
        ("1.250.000.50", Decimal("1250000.50")),
        ("", None),
        ("н/д", None),
        (None, None),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


def test_valid_invoice_has_no_issues():
    assert check(Document.model_validate(INVOICE)) == []


def test_valid_act_has_no_issues():
    doc = Document.model_validate(ACT)
    assert doc.is_act and doc.counterparty.name == "ИП Исполнитель"
    assert check(doc) == []


def test_check_catches_model_mistakes():
    broken = {
        **INVOICE,
        "дата": "12 июня",
        "поставщик": {"название": "ТОО", "ИИН_БИН": "12345"},
        "товары": [
            {"название": "Бумага", "количество": "10", "цена": "2 500", "сумма": "52 000"},  # 10×2500 ≠ 52 000
            {"название": "Ручка", "сумма": ""},
        ],
    }
    issues = " | ".join(check(Document.model_validate(broken)))
    assert "Дата" in issues
    assert "12 цифр" in issues
    assert "Строка 1" in issues
    assert "Строка 2: не распознана сумма" in issues
    assert "не совпадает с итогом" in issues


def test_check_vat_mismatch():
    issues = check(Document.model_validate({**INVOICE, "итого_с_НДС": "50 000"}))
    assert any("НДС" in issue for issue in issues)


# ------------------------------------------------------------- ответ модели

def test_parse_response_strips_markdown_fence():
    doc = parse_response('```json\n{"тип": "авр", "номер": "1"}\n```')
    assert doc.kind == "авр" and doc.number == "1"


def test_parse_response_rejects_garbage():
    with pytest.raises(RecognitionError):
        parse_response("Извините, не могу прочитать документ")
    with pytest.raises(RecognitionError):
        parse_response("[1, 2, 3]")


def test_image_passes_through_unchanged():
    assert file_to_images(b"jpeg-bytes", "scan.JPG") == [b"jpeg-bytes"]


def test_pdf_is_split_into_pages():
    import fitz

    pdf = fitz.open()
    for _ in range(2):
        pdf.new_page()
    pages = file_to_images(pdf.tobytes(), "scan.pdf", dpi=50)
    assert len(pages) == 2
    assert all(page.startswith(b"\x89PNG") for page in pages)


# ------------------------------------------------------------- 1С

def test_date_to_1c():
    assert date_to_1c("1.6.2026") == "2026-06-01T00:00:00"
    assert date_to_1c("вчера") == "вчера"


def test_payload_marks_issues_in_comment():
    doc = Document.model_validate(INVOICE)
    payload = build_payload(doc, ["Сумма не сходится"])
    assert payload["Posted"] is False
    assert payload["СуммаДокумента"] == "44 856"
    assert payload["Товары"][1]["Номенклатура"] == "Ручка"
    assert "ПРОВЕРИТЬ: Сумма не сходится" in payload["Комментарий"]


@pytest.fixture
def mock_1c(tmp_path, monkeypatch):
    from mock_1c import server

    monkeypatch.setattr(server, "DB_PATH", tmp_path / "db.json")
    return TestClient(server.app)


def test_client_creates_draft_in_mock_1c(mock_1c):
    client = OneCClient(base_url="http://testserver/odata/standard.odata", http=mock_1c)
    response = client.create_document(Document.model_validate(ACT))

    assert response["Number"] == "7" and response["Posted"] is False
    stored = mock_1c.get("/odata/standard.odata/Document_АктВыполненныхРабот").json()["value"]
    assert stored[0]["Контрагент"] == "ИП Исполнитель"
    assert "ИП Исполнитель" in mock_1c.get("/").text


# ------------------------------------------------------------- конвейер

class FakeExtractor:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def extract(self, data: bytes, filename: str) -> Document:
        return Document.model_validate(self.payload)


class FailingOneC:
    def create_document(self, doc, issues=None):
        raise ConnectionError("1С недоступна")


def test_pipeline_end_to_end_with_mock_1c(mock_1c):
    onec = OneCClient(base_url="http://testserver/odata/standard.odata", http=mock_1c)
    result = process(b"...", "scan.jpg", FakeExtractor(INVOICE), onec)

    assert result.onec_number == "НК-125"
    assert result.issues == []
    assert "Черновик создан в 1С" in result.summary()


def test_pipeline_survives_1c_outage():
    result = process(b"...", "scan.jpg", FakeExtractor({**INVOICE, "итого": "1"}), FailingOneC())

    assert result.onec_number is None
    summary = result.summary()
    assert "В 1С не отправлено" in summary
    assert "Проверьте" in summary


def test_excel_contains_lines_and_issues():
    doc = Document.model_validate(INVOICE)
    sheet = openpyxl.load_workbook(make_excel(doc, ["Сумма не сходится"])).active
    values = [cell for row in sheet.iter_rows(values_only=True) for cell in row if cell]
    assert "Бумага А4" in values
    assert "Сумма не сходится" in values
