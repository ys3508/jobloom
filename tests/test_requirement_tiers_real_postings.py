"""Whole real postings, not isolated sentences.

Every defect in `requirement_tiers` so far came from a complete description rather than from
a phrase: a sub-heading that did not end a section, a tool named in three paragraphs, a
parenthesis that downgraded the requirement in front of it. Short fixtures cannot produce any
of those, because each is about what a line inherits from the several hundred around it.

**The postings are not committed.** `docs/implementation-plan-2026-08-31.md` forbids complete
job descriptions in git-tracked fixtures, so these read the private corpus under `.jobloom/`
and skip when it is not there. The structural fixture in `test_requirement_tiers.py` pins the
same behaviours in a form that always runs; this file is what checks them against the text an
employer actually wrote.
"""

import collections
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
CORPUS = ROOT / ".jobloom" / "jobs-wide-20260907"


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"real_postings_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


TIERS = load_script("requirement_tiers")
MUST, PREFERRED, UNKNOWN = TIERS.MUST_HAVE, TIERS.PREFERRED, TIERS.UNKNOWN


def find_by_id(job_id):
    """The exact card, because several postings share an employer and a title.

    The first Komodo "Infrastructure Engineer" in the corpus is not the one that reached
    rank 1; matching on employer and title found a different variant with different tools
    and made the test assert things about a posting nobody had reviewed.
    """
    if not CORPUS.is_dir():
        raise unittest.SkipTest(f"private posting corpus not present at {CORPUS}")
    path = CORPUS / f"{job_id}.json"
    if not path.is_file():
        raise unittest.SkipTest(f"{job_id} not in the corpus")
    return json.loads(path.read_text(encoding="utf-8"))


def find(employer_fragment, title_fragment):
    if not CORPUS.is_dir():
        raise unittest.SkipTest(f"private posting corpus not present at {CORPUS}")
    for path in sorted(CORPUS.glob("job-*.json")):
        card = json.loads(path.read_text(encoding="utf-8"))
        if employer_fragment in (card.get("employer") or "") \
                and title_fragment in (card.get("title") or ""):
            return card
    raise unittest.SkipTest(f"{employer_fragment} / {title_fragment} not in the corpus")


class KomodoInfrastructureEngineerTests(unittest.TestCase):
    """The posting that reached rank 1 on three repetitions of one tool.

    Its shape is why: a required block, then an AI-expectations sub-heading, then a
    "we'll prioritize" transition, then salary sub-headings, a policy section and a location
    section — none of which ended the requirement list, so all of it was must-have.
    """

    def setUp(self):
        # The card that ranked 1 in the 2026-09-09 queue and failed the spot check.
        self.card = find_by_id("job-f93ad94ccd33")
        self.text = self.card["description"]
        self.lines = TIERS.tier_lines(self.text)
        self.counts = collections.Counter(entry["tier"] for entry in self.lines)

    def test_the_must_have_list_is_requirements_and_nothing_else(self):
        must = [entry["line"] for entry in self.lines if entry["tier"] == MUST]
        self.assertLessEqual(len(must), 12, must)
        for line in must:
            with self.subTest(line=line[:60]):
                self.assertNotIn("$", line)
                self.assertFalse(line.startswith("#"))
                self.assertNotRegex(line.lower(), r"\bpay range\b|\bbase pay\b")

    def test_the_salary_subheading_ends_the_requirement_list(self):
        for line in (entry["line"] for entry in self.lines):
            self.assertNotIn("USD", line)

    def test_the_prioritize_transition_moves_to_preferred(self):
        """"Additional skills and experience we'll prioritize…" opens a preferred block."""
        preferred = [entry["line"] for entry in self.lines if entry["tier"] == PREFERRED]
        self.assertTrue(any("FinOps" in line for line in preferred), preferred)
        self.assertTrue(any("Spark" in line or "dbt" in line for line in preferred), preferred)

    def test_the_ai_expectations_subheading_stays_required(self):
        """It says "(required)", so it opens a mandatory block rather than ending one."""
        must = [entry["line"] for entry in self.lines if entry["tier"] == MUST]
        self.assertTrue(any("AI-native" in line or "AI coding" in line for line in must), must)

    def test_a_tool_named_three_times_is_counted_once(self):
        report = TIERS.summarize(self.text, [
            {"id": "fact-git", "type": "experience_claim", "value": "Used GitHub daily",
             "status": "confirmed", "locked": False, "evidence_strength": "direct",
             "keywords": ["GitHub"]}])
        must = report["tiers"][MUST]
        self.assertEqual(must["unique_direct"], 1)
        self.assertEqual(must["direct_terms"], ["GitHub"])

    def test_its_real_gaps_are_still_reported(self):
        report = TIERS.summarize(self.text, [
            {"id": "fact-git", "type": "experience_claim", "value": "Used GitHub daily",
             "status": "confirmed", "locked": False, "evidence_strength": "direct",
             "keywords": ["GitHub"]}])
        gaps = " ".join(report["tiers"][MUST]["gap_terms"]).lower()
        for missing in ("aws", "docker", "snowflake"):
            with self.subTest(missing=missing):
                self.assertIn(missing, gaps)


class ParentheticalScopeTests(unittest.TestCase):
    """A preferred word inside a bracket must not downgrade the requirement in front of it."""

    def test_a_consulting_years_line_is_not_downgraded(self):
        card = find("Beghou", "Associate Partner")
        for entry in TIERS.tier_lines(card["description"]):
            if "Life Sciences Consulting" in entry["line"] and "(" in entry["line"]:
                with self.subTest(line=entry["line"][:70]):
                    self.assertNotEqual(entry["tier"], PREFERRED)


class CurlyApostropheTests(unittest.TestCase):
    """21 of the 112 postings write "What you'll do" with U+2019."""

    def test_a_curly_responsibilities_heading_still_ends_the_requirement_list(self):
        if not CORPUS.is_dir():
            raise unittest.SkipTest("private posting corpus not present")
        checked = 0
        for path in sorted(CORPUS.glob("job-*.json")):
            text = json.loads(path.read_text(encoding="utf-8")).get("description") or ""
            if "’ll Work" not in text and "’ll work" not in text:
                continue
            checked += 1
            lines = [entry["line"] for entry in TIERS.tier_lines(text)]
            self.assertFalse([line for line in lines if "hybrid" in line.lower()
                              and "office" in line.lower()], lines[:3])
            if checked >= 5:
                break
        if not checked:
            raise unittest.SkipTest("no posting with a curly heading in the corpus")


class CorpusWideSanityTests(unittest.TestCase):
    """Properties that must hold across every posting, not just the ones examined by hand."""

    def setUp(self):
        if not CORPUS.is_dir():
            raise unittest.SkipTest("private posting corpus not present")
        self.cards = [json.loads(path.read_text(encoding="utf-8"))
                      for path in sorted(CORPUS.glob("job-*.json"))[:400]]

    def test_no_offset_ever_points_at_the_wrong_text(self):
        for card in self.cards:
            text = card.get("description") or ""
            for entry in TIERS.tier_lines(text):
                if text[entry["offset"]:entry["offset"] + len(entry["line"])] != entry["line"]:
                    self.fail(f"offset mismatch in {card.get('job_id')}: {entry['line'][:60]}")

    def test_no_tier_ever_holds_a_salary_line(self):
        for card in self.cards:
            for entry in TIERS.tier_lines(card.get("description") or ""):
                with self.subTest(job=card.get("job_id")):
                    self.assertNotRegex(entry["line"], r"\$\d[\d,]*\s*[—–-]\s*\$?\d")

    def test_every_line_carries_a_reason_that_maps_to_its_tier(self):
        for card in self.cards:
            for entry in TIERS.tier_lines(card.get("description") or ""):
                self.assertEqual(TIERS.REASONS[entry["reason"]], entry["tier"])


if __name__ == "__main__":
    unittest.main()
