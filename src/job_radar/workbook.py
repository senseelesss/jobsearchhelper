"""Runtime Excel interchange; the database remains authoritative."""

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile


def export_workbook(rows, output: Path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.worksheet.datavalidation import DataValidation

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Вакансії"
    edits = workbook.create_sheet("Зміни статусів")
    for page in (sheet, edits):
        page.sheet_view.showGridLines = False
        page.freeze_panes = "D6" if page == sheet else "C6"
        page["A1"] = "Job Radar" if page == sheet else "Зміни статусів подач"
        page["A1"].font = Font(name="Arial", size=16, bold=True, color="243B53")
        page["A2"] = (
            "Знімок БД. Зміни подач вносьте на другому аркуші."
            if page == sheet
            else "Вкажіть ID, поточну ревізію та новий статус. Імпорт виконується окремою командою."
        )
        page["A3"] = "schema_version=1"
        page["B3"] = datetime.now(timezone.utc).replace(tzinfo=None)
        page["B3"].number_format = "dd.mm.yyyy hh:mm"
        page.row_dimensions[3].hidden = True
        page["A4"] = "Оновлено (UTC)"
        page["B4"] = page["B3"].value
        page["B4"].number_format = "dd.mm.yyyy hh:mm"
    columns = [
        ("ID вакансії", "application_key", 30),
        ("Компанія", "company", 30),
        ("Посада", "title", 55),
        ("Локація", "location", 28),
        ("Статус подачі", "application_state", 20),
        ("Ревізія", "application_revision", 14),
        ("Відбір", "screening_status", 18),
        ("Джерело", "source", 16),
        ("Посилання", "url", 55),
        ("Опис", "fetch_status", 18),
        ("Перевірено (UTC)", "fetched_at", 28),
        ("Помилка опису", "fetch_error", 40),
        ("AI оцінка 0–100", "match_score", 20),
        ("Рекомендація", "recommendation", 20),
        ("Впевненість AI", "score_confidence", 20),
    ]
    for index, (label, _, width) in enumerate(columns, 1):
        sheet.cell(5, index, label)
        sheet.column_dimensions[sheet.cell(5, index).column_letter].width = width
    for row_number, row in enumerate(rows, 6):
        for column, (_, key, _) in enumerate(columns, 1):
            value = row.get(key, "")
            if key == "application_key":
                value = row.get(key, row.get("dedupe_key", ""))
            cell = sheet.cell(row_number, column, value)
            cell.font = Font(name="Arial", size=10)
            if isinstance(value, str):
                cell.data_type = "s"
            if key == "fetched_at" and value:
                try:
                    cell.value = datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
                        tzinfo=None
                    )
                    cell.number_format = "dd.mm.yyyy hh:mm"
                except ValueError:
                    pass
            cell.alignment = Alignment(vertical="center", wrap_text=key in {"title", "company"})
        sheet.row_dimensions[row_number].height = 32
    for i, (label, width) in enumerate(
        [
            ("ID вакансії", 30),
            ("Очікувана ревізія", 22),
            ("Новий статус", 20),
            ("Примітка", 55),
        ],
        1,
    ):
        edits.cell(5, i, label)
        edits.column_dimensions[edits.cell(5, i).column_letter].width = width
    validation = DataValidation(
        type="list",
        formula1='"not_applied,saved,applied,interview,offer,rejected,withdrawn,skipped"',
    )
    validation.error = "Оберіть статус зі списку"
    validation.showErrorMessage = True
    edits.add_data_validation(validation)
    validation.add("C6:C1005")
    for row in edits.iter_rows(min_row=6, max_row=105, max_col=4):
        for cell in row:
            cell.fill = PatternFill("solid", fgColor="FFF5D6")
    for page in (sheet, edits):
        for cell in page[5]:
            cell.font = Font(name="Arial", bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="243B53")
            cell.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
        page.row_dimensions[5].height = 32
        page.auto_filter.ref = f"A5:{'O' if page == sheet else 'D'}{max(6, page.max_row)}"
    output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(suffix=".xlsx", dir=output.parent, delete=False) as temporary:
        name = Path(temporary.name)
    try:
        workbook.save(name)
        os.replace(name, output)
    except PermissionError:
        versioned = output.with_name(
            f"{output.stem}-{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"
        )
        os.replace(name, versioned)
        return versioned
    finally:
        name.unlink(missing_ok=True)
    return output


def import_status_edits(repository, path: Path, receipt: Path):
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        sheet = workbook["Зміни статусів"]
        if sheet["A3"].value != "schema_version=1":
            raise ValueError("Unsupported workbook schema")
        inputs = list(
            sheet.iter_rows(
                min_row=6, max_row=min(sheet.max_row, 10005), max_col=4, values_only=True
            )
        )
        counts = Counter(str(row[0]) for row in inputs if row[0])
        results = []
        for number, (key, revision, state, note) in enumerate(inputs, 6):
            if not any(value is not None for value in (key, revision, state, note)):
                continue
            try:
                if not isinstance(key, str) or counts[key] != 1:
                    raise ValueError("Missing or duplicate vacancy ID")
                if (
                    isinstance(revision, bool)
                    or not isinstance(revision, (int, float))
                    or revision != int(revision)
                    or revision < 0
                ):
                    raise ValueError("Expected a nonnegative integer revision")
                if not isinstance(state, str) or any(
                    isinstance(v, str) and v.startswith("=") for v in (key, state, note)
                ):
                    raise ValueError("Formulas are not accepted in edit fields")
                fingerprint = hashlib.sha256(
                    json.dumps([key, int(revision), state, note or ""]).encode()
                ).hexdigest()
                result = repository.set_application(
                    key,
                    state,
                    "xlsx:" + fingerprint,
                    actor="excel",
                    note=str(note or ""),
                    expected_revision=int(revision),
                )
                results.append({"row": number, "result": result})
            except ValueError as error:
                results.append({"row": number, "result": "rejected", "error": str(error)})
    finally:
        workbook.close()
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    return results
