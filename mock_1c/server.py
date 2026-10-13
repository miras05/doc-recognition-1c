"""Mock-сервер 1С с интерфейсом OData.

Позволяет разрабатывать и показывать интеграцию без доступа к настоящей 1С:
бот отправляет документы по тому же адресу и в том же формате, что и в бою.
На главной странице — журнал документов, стилизованный под интерфейс 1С.

Запуск:  uvicorn mock_1c.server:app --port 8000
"""

from __future__ import annotations

import html
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

DB_PATH = Path(os.environ.get("MOCK_1C_DB", "data/mock_1c_db.json"))
_lock = threading.Lock()

app = FastAPI(title="Mock 1С OData")


def _load() -> list[dict]:
    if not DB_PATH.exists():
        return []
    return json.loads(DB_PATH.read_text(encoding="utf-8"))


def _save(documents: list[dict]) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DB_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DB_PATH)


@app.post("/odata/standard.odata/Document_{doc_type}", status_code=201)
def create_document(doc_type: str, payload: dict) -> JSONResponse:
    """Как настоящая 1С: создаёт документ и отвечает объектом с Ref_Key."""
    with _lock:
        documents = _load()
        document = {
            "Ref_Key": str(uuid.uuid4()),
            "Type": doc_type,
            "Number": payload.get("Number") or f"{len(documents) + 1:08d}",
            "Date": payload.get("Date") or datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "Posted": False,
            **{k: payload.get(k, "") for k in ("Контрагент", "БИН", "СуммаДокумента", "НДС", "Комментарий")},
            "Товары": payload.get("Товары", []),
            "ReceivedAt": datetime.now().strftime("%H:%M:%S"),
        }
        documents.insert(0, document)
        _save(documents)

    return JSONResponse(
        status_code=201,
        content={"Ref_Key": document["Ref_Key"], "Number": document["Number"], "Posted": False},
    )


@app.get("/odata/standard.odata/Document_{doc_type}")
def list_documents(doc_type: str) -> dict:
    return {"value": [d for d in _load() if d["Type"] == doc_type]}


@app.delete("/odata/standard.odata")
def clear() -> dict:
    with _lock:
        _save([])
    return {"ok": True}


def _e(value: object) -> str:
    """Данные пришли из распознавания — экранируем перед вставкой в HTML."""
    return html.escape(str(value))


def _fmt_date(raw: str) -> str:
    try:
        return datetime.fromisoformat(raw).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return raw


@app.get("/", response_class=HTMLResponse)
def journal() -> str:
    rows = "".join(
        f"<tr><td><span class='draft'></span></td><td>{_e(d['Number'])}</td><td>{_e(_fmt_date(d['Date']))}</td>"
        f"<td>{_e(d['Контрагент'])}</td><td>{_e(d['БИН'])}</td><td class='sum'>{_e(d['СуммаДокумента'])}</td>"
        f"<td>{'<span class=ai>Создано AI</span>' if 'AI' in d['Комментарий'] else ''}"
        f"{'<span class=warn>Проверить</span>' if 'ПРОВЕРИТЬ' in d['Комментарий'] else ''}</td>"
        f"<td class='muted'>{d['ReceivedAt']}</td></tr>"
        for d in _load()
    ) or "<tr><td colspan=8 class='empty'>Документов пока нет. Отправьте скан в бот.</td></tr>"
    return JOURNAL_HTML.replace("{{rows}}", rows)


JOURNAL_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta http-equiv="refresh" content="3">
<title>1С:Предприятие — Поступление</title>
<style>
 *{box-sizing:border-box;margin:0;font-family:'Segoe UI',Tahoma,sans-serif}
 body{background:#e8eef3}
 .top{background:#c5d4e0;padding:8px 16px;display:flex;gap:12px;align-items:center}
 .logo{background:#d4a017;color:#fff;font-weight:700;padding:4px 10px;border-radius:3px}
 h1{font-size:15px;color:#2c3e50}
 table{width:100%;border-collapse:collapse;background:#fff}
 th{background:#dde7ee;padding:8px;text-align:left;font-size:12px;border:1px solid #c5d2dc}
 td{padding:7px 8px;font-size:13px;border:1px solid #e1e8ed}
 .draft{display:inline-block;width:14px;height:14px;border:2px solid #b0b0b0}
 .ai{background:#e8f4fd;color:#2980b9;font-size:10px;padding:1px 6px;border-radius:8px;margin-right:4px}
 .warn{background:#fff2cc;color:#9c6500;font-size:10px;padding:1px 6px;border-radius:8px}
 .sum{text-align:right;font-variant-numeric:tabular-nums}
 .muted{color:#90a4b0}.empty{padding:40px;text-align:center;color:#90a4b0}
</style></head><body>
<div class="top"><span class="logo">1С</span><h1>Поступление (акты, накладные)</h1></div>
<table><tr><th></th><th>Номер</th><th>Дата</th><th>Контрагент</th><th>БИН</th>
<th class="sum">Сумма</th><th>Отметки</th><th>Получен</th></tr>{{rows}}</table>
</body></html>"""
