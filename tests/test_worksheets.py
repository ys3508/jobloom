import csv
import importlib.util
import json
import re
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"worksheets_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


WRITER = load_script("worksheet_writer")
BUILD = load_script("build_worksheets")
SAVED = load_script("saved_jobs")
AT = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
TODAY = date(2026, 8, 29)


class ColumnNameTests(unittest.TestCase):
    def test_it_counts_in_bijective_base_26(self):
        # Not plain base 26: there is no zero digit, so Z is followed by AA, not by BA.
        for index, expected in ((1, "A"), (26, "Z"), (27, "AA"), (52, "AZ"), (53, "BA")):
            self.assertEqual(WRITER.column_name(index), expected)

    def test_a_column_index_is_one_based(self):
        with self.assertRaises(ValueError):
            WRITER.column_name(0)


class CellTests(unittest.TestCase):
    def test_a_number_is_written_as_a_number(self):
        self.assertIn("<v>42</v>", WRITER._cell("A1", 42))
        self.assertNotIn("inlineStr", WRITER._cell("A1", 42))

    def test_a_bool_does_not_become_one_or_zero(self):
        # A bool is an int in Python, so an unguarded number branch turns "did they follow
        # the suggestion" into an arithmetic 1 that later gets summed.
        self.assertIn("<t>yes</t>", WRITER._cell("A1", True))
        self.assertIn("<t>no</t>", WRITER._cell("A1", False))

    def test_text_that_would_break_the_document_is_escaped(self):
        cell = WRITER._cell("A1", 'Ben & Jerry <script> "x"')
        self.assertIn("&amp;", cell)
        self.assertIn("&lt;script&gt;", cell)
        ElementTree.fromstring(cell)

    def test_surrounding_space_is_preserved_rather_than_silently_trimmed(self):
        self.assertIn('xml:space="preserve"', WRITER._cell("A1", "  padded  "))

    def test_an_empty_value_writes_no_cell_at_all(self):
        self.assertEqual(WRITER._cell("A1", None), "")
        self.assertEqual(WRITER._cell("A1", ""), "")


class SheetTests(unittest.TestCase):
    def test_a_row_must_have_one_value_per_column(self):
        with self.assertRaises(ValueError):
            WRITER.Sheet("S", ["a", "b"], [["only one"]])

    def test_a_sheet_name_excel_would_reject_is_refused_here(self):
        for name in ("", "a" * 32, "has/slash", "has:colon"):
            with self.assertRaises(ValueError):
                WRITER.Sheet(name, ["a"], [])

    def test_a_link_column_outside_the_sheet_is_refused(self):
        with self.assertRaises(ValueError):
            WRITER.Sheet("S", ["a"], [], link_column=2)

    def test_only_http_targets_become_clickable(self):
        # A hyperlink is followed on a click, so a file: or javascript: target reaching one
        # would be this writer's doing.
        sheet = WRITER.Sheet("S", ["link"], [["https://example.com/1"], ["file:///etc/passwd"],
                                             ["javascript:alert(1)"], [""]], link_column=1)
        self.assertEqual([target for _, target in sheet._links()], ["https://example.com/1"])

    def test_a_link_points_at_the_row_it_came_from(self):
        sheet = WRITER.Sheet("S", ["a", "link"],
                             [["x", ""], ["y", "https://example.com/2"]], link_column=2)
        self.assertEqual(sheet._links(), [("B3", "https://example.com/2")])


class WorkbookTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def _book(self, sheet):
        path = WRITER.write_xlsx(self.dir / "book.xlsx", [sheet])
        return zipfile.ZipFile(path)

    def test_every_part_is_well_formed_xml(self):
        book = self._book(WRITER.Sheet("S", ["a"], [["x"]]))
        for part in book.namelist():
            ElementTree.fromstring(book.read(part))

    def test_the_required_parts_are_all_present(self):
        book = self._book(WRITER.Sheet("S", ["a"], [["x"]]))
        self.assertLessEqual({"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                              "xl/_rels/workbook.xml.rels", "xl/worksheets/sheet1.xml"},
                             set(book.namelist()))

    def test_every_hyperlink_resolves_to_a_relationship(self):
        # A dangling r:id is the failure that makes a reader reject the whole file.
        book = self._book(WRITER.Sheet("S", ["link"], [["https://example.com/a"],
                                                       ["https://example.com/b"]], link_column=1))
        sheet = book.read("xl/worksheets/sheet1.xml").decode()
        rels = book.read("xl/worksheets/_rels/sheet1.xml.rels").decode()
        refs = set(re.findall(r'r:id="(rId\d+)"', sheet))
        self.assertTrue(refs)
        self.assertLessEqual(refs, set(re.findall(r'Id="(rId\d+)"', rels)))

    def test_a_sheet_without_links_writes_no_relationship_part(self):
        book = self._book(WRITER.Sheet("S", ["a"], [["x"]]))
        self.assertNotIn("xl/worksheets/_rels/sheet1.xml.rels", book.namelist())

    def test_each_sheet_is_declared_in_the_content_types(self):
        path = WRITER.write_xlsx(self.dir / "two.xlsx",
                                 [WRITER.Sheet("One", ["a"], [["x"]]),
                                  WRITER.Sheet("Two", ["b"], [["y"]])])
        book = zipfile.ZipFile(path)
        types = book.read("[Content_Types].xml").decode()
        for index in (1, 2):
            self.assertIn(f'PartName="/xl/worksheets/sheet{index}.xml"', types)

    def test_a_workbook_needs_a_sheet(self):
        with self.assertRaises(ValueError):
            WRITER.write_xlsx(self.dir / "empty.xlsx", [])

    def test_the_csv_carries_a_bom_so_excel_reads_the_encoding(self):
        # Without it Excel on macOS reads UTF-8 as the system encoding and every non-ASCII
        # employer name arrives as mojibake.
        sheet = WRITER.Sheet("S", ["employer"], [["Genmab A/S — Köln"]])
        path = WRITER.write_csv(self.dir / "s.csv", sheet)
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        with path.open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(list(csv.reader(handle))[1], ["Genmab A/S — Köln"])


class QueueSheetTests(unittest.TestCase):
    def _queue(self, **overrides):
        row = {
            "rank": 1, "direction_id": "research-clinical-data-v1", "weight_percent": 85,
            "evidence": {"direct": 3, "recognized_requirements": 4, "stated_requirements": 15,
                         "direct_requirements": ["R", "SAS", "Python"]},
            "review_reasons": ["direction_context_without_title_match"],
            "also_matches": ["biostatistics-statistical-analytics-v2"],
            "employer": "Acme Health", "title": "Clinical Data Analyst", "location": "Boston, MA",
            "work_arrangement": "hybrid",
            "salary": {"currency": "USD", "minimum": 100000, "maximum": 140000},
            "canonical_url": "https://jobs.example.com/1",
            "apply_url": "https://jobs.example.com/1/apply",
            "posted_at": "2026-08-27",
        }
        row.update(overrides)
        return {"rows": [row]}

    def test_every_queued_opening_reaches_the_sheet(self):
        # A row dropped here would be a decision made by a spreadsheet writer.
        queue = {"rows": [dict(self._queue()["rows"][0], rank=n) for n in range(1, 21)]}
        self.assertEqual(len(BUILD.queue_sheet(queue, today=TODAY).rows), 20)

    def test_the_queues_order_is_kept_exactly(self):
        rows = [dict(self._queue()["rows"][0], rank=n, employer=f"E{n}") for n in (3, 1, 2)]
        sheet = BUILD.queue_sheet({"rows": rows}, today=TODAY)
        self.assertEqual([row[0] for row in sheet.rows], [3, 1, 2])

    def test_direct_and_stated_are_kept_as_separate_counts(self):
        # Never divided into a fit percentage: 0 of 12 may be a posting nothing parsed.
        sheet = BUILD.queue_sheet(self._queue(), today=TODAY)
        columns = dict(zip(sheet.columns, sheet.rows[0]))
        self.assertEqual(columns["direct"], 3)
        self.assertEqual(columns["stated"], 15)

    def test_the_apply_url_is_the_link_and_it_is_the_last_column(self):
        sheet = BUILD.queue_sheet(self._queue(), today=TODAY)
        self.assertEqual(sheet.link_column, len(sheet.columns))
        self.assertEqual(sheet.rows[0][-1], "https://jobs.example.com/1/apply")

    def test_a_posting_without_an_apply_url_falls_back_to_its_own_page(self):
        sheet = BUILD.queue_sheet(self._queue(apply_url=None), today=TODAY)
        self.assertEqual(sheet.rows[0][-1], "https://jobs.example.com/1")

    def test_an_unstated_salary_is_blank_rather_than_guessed(self):
        sheet = BUILD.queue_sheet(self._queue(salary=None), today=TODAY)
        self.assertEqual(dict(zip(sheet.columns, sheet.rows[0]))["salary"], "")

    def test_a_title_group_says_the_openings_are_independent(self):
        # Sharing an employer and title is not being the same job, so the sheet counts them
        # rather than implying one job in N cities.
        sheet = BUILD.queue_sheet(
            self._queue(group={"members": ["job-a", "job-b"]}), today=TODAY)
        self.assertEqual(dict(zip(sheet.columns, sheet.rows[0]))["duplicates"],
                         "3 independent openings")

    def test_a_lone_opening_is_not_marked_as_a_duplicate(self):
        sheet = BUILD.queue_sheet(self._queue(), today=TODAY)
        self.assertEqual(dict(zip(sheet.columns, sheet.rows[0]))["duplicates"], "")


class RecordSheetTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        SAVED.initialize(self.db)
        self.addCleanup(self.db.close)

    def _card(self, url="https://jobs.example.com/1"):
        return {"canonical_url": url, "title": "Analyst", "employer": "Acme",
                "location": "Boston, MA", "country": "US",
                "extraction": {"ats": {"posted_at": "2026-08-01T00:00:00+00:00"}}}

    def test_an_empty_record_still_writes_its_header(self):
        # The sheet exists before the first application so there is somewhere to look.
        sheet = BUILD.record_sheet(self.db, today=TODAY)
        self.assertEqual(sheet.rows, [])
        self.assertIn("confirmed submitted", sheet.columns)

    def test_a_decision_and_a_confirmation_are_shown_in_separate_columns(self):
        SAVED.save(self.db, self._card(), actor="user", decision=SAVED.APPLIED, at=AT)
        sheet = BUILD.record_sheet(self.db, today=TODAY)
        columns = dict(zip(sheet.columns, sheet.rows[0]))
        self.assertEqual(columns["evidence"], "stated at decision")
        self.assertIsNone(columns["confirmed submitted"])
        SAVED.confirm_submitted(self.db, "https://jobs.example.com/1", at=AT)
        columns = dict(zip(BUILD.record_sheet(self.db, today=TODAY).columns,
                           BUILD.record_sheet(self.db, today=TODAY).rows[0]))
        self.assertEqual(columns["evidence"], "confirmed after applying")
        self.assertEqual(columns["confirmed submitted"], AT.isoformat())


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_both_sheets_are_separate_files_with_a_csv_beside_each(self):
        # Separate because the queue is replaced whole by the next pull and the record grows
        # a row at a time; one workbook would put them on the same clock.
        queue = self.dir / "queue.json"
        queue.write_text(json.dumps({"rows": []}), encoding="utf-8")
        db = self.dir / "j.db"
        sqlite3.connect(db).close()
        result = BUILD.build(queue, db, self.dir / "out", today=TODAY)
        for name in ("to-apply.xlsx", "to-apply.csv", "applied.xlsx", "applied.csv"):
            self.assertTrue((self.dir / "out" / name).is_file(), name)
        self.assertEqual({item["sheet"] for item in result["written"]}, {"to-apply", "applied"})

    def test_the_record_reports_decisions_and_submissions_as_different_numbers(self):
        db = self.dir / "j.db"
        connection = sqlite3.connect(db)
        connection.row_factory = sqlite3.Row
        SAVED.initialize(connection)
        SAVED.save(connection, {"canonical_url": "https://jobs.example.com/1", "title": "A",
                                "employer": "Acme"}, actor="user", decision=SAVED.APPLIED, at=AT)
        connection.close()
        written = BUILD.build(None, db, self.dir / "out", today=TODAY)["written"][0]
        self.assertEqual(written["decided_to_apply"], 1)
        self.assertEqual(written["confirmed_submitted"], 0)
        self.assertEqual(written["stated_not_confirmed"], 1)

    def test_building_nothing_is_refused_rather_than_writing_an_empty_directory(self):
        # An out-dir with no sheets in it reads as "the export ran and found nothing",
        # which is a different and worse claim than "you asked for nothing".
        import contextlib
        import io
        import sys
        argv = sys.argv
        sys.argv = ["build_worksheets.py", "--out-dir", str(self.dir / "out")]
        try:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                BUILD.main()
        finally:
            sys.argv = argv
        self.assertFalse((self.dir / "out").exists())


if __name__ == "__main__":
    unittest.main()
