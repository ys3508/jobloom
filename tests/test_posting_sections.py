import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"posting_sections_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


SECTIONS = load_script("posting_sections")
DIRECTIONS = load_script("direction_core")

LONG_LINE = "You will " + ("build and validate clinical study databases in SAS and SQL " * 12)


class RoutingContractTests(unittest.TestCase):
    """Every card built here gets routed — `assist_bridge` routes it directly — and
    `direction_core` rejects an over-long or over-full list outright."""

    def test_the_caps_match_the_routing_engine(self):
        for field, (max_chars, max_items) in SECTIONS.ROUTING_SHAPES.items():
            kind, engine_chars, engine_items = DIRECTIONS.JOB_FIELD_SHAPES[field]
            self.assertEqual(kind, "list", field)
            self.assertLessEqual(max_chars, engine_chars, field)
            self.assertLessEqual(max_items, engine_items, field)
        self.assertLessEqual(SECTIONS.ROUTING_TITLE_CHARS,
                             DIRECTIONS.JOB_FIELD_SHAPES["title"][1])

    def test_reading_stays_more_generous_than_routing(self):
        # `split_sections` drops a line longer than MAX_ITEM_CHARS. Lowering that cap to
        # match routing would discard whole requirements instead of shortening them, so the
        # read cap must stay above every routed cap.
        self.assertGreater(SECTIONS.MAX_ITEM_CHARS,
                           max(chars for chars, _ in SECTIONS.ROUTING_SHAPES.values()))

    def test_a_long_line_is_split_never_dropped(self):
        sentence = "Validate the study database in SAS. "
        items = SECTIONS.fit_routing_shape([sentence * 30], "responsibilities")
        self.assertTrue(items)
        self.assertTrue(all(len(item) <= 500 for item in items))
        self.assertTrue(all("study database" in item for item in items))

    def test_an_unsplittable_line_is_truncated_not_discarded(self):
        # No sentence punctuation to split on, so the requirement survives shortened rather
        # than vanishing.
        items = SECTIONS.fit_routing_shape([LONG_LINE], "responsibilities")
        self.assertEqual(len(items), 1)
        self.assertEqual(len(items[0]), 500)

    def test_each_field_gets_its_own_cap(self):
        # A single global pair silently violates the tightest field: compensation is 300/20
        # where responsibilities is 500/50.
        self.assertEqual(len(SECTIONS.fit_routing_shape([LONG_LINE], "compensation_structure")[0]), 300)
        self.assertEqual(len(SECTIONS.fit_routing_shape([LONG_LINE], "responsibilities")[0]), 500)
        many = [f"item {index}" for index in range(80)]
        self.assertEqual(len(SECTIONS.fit_routing_shape(many, "compensation_structure")), 20)
        self.assertEqual(len(SECTIONS.fit_routing_shape(many, "responsibilities")), 50)

    def test_an_extracted_card_survives_the_routing_validator(self):
        # The bug this guards: a 717-character requirement line raised
        # `malformed job card field: responsibilities` on its way to a routing decision.
        text = (f"Responsibilities\n- {LONG_LINE}\n\nQualifications\n- {LONG_LINE}\n\n"
                f"Compensation\n- {LONG_LINE}\n")
        card = {
            "job_id": "job-1", "canonical_url": "https://e.com/1", "employer": "E",
            "title": "Analyst", "country": "US", "location": "Boston",
            "work_arrangement": "remote", "employment_type": "full_time", "salary": None,
            "status": "open", "sponsorship": "unknown", "citizenship_required": None,
            "security_clearance_required": None, "required_certifications": [],
            "preferred_certifications": [], "required_skills": [], "preferred_skills": [],
            "summary": None, "responsibilities": [], "compensation_structure": [],
            "sponsorship_statements": [], "seniority": "unknown", "experience": None,
            "already_applied": False, "high_value": False, "requirements_reviewed": False,
        }
        found = SECTIONS.extract(text, title="Analyst")
        card.update({key: value for key, value in found.items() if key != "extraction"})
        DIRECTIONS._validate_job_shape(card)  # raises if any cap is exceeded

    def test_an_over_long_page_title_is_capped(self):
        found = SECTIONS.extract("Responsibilities\n- Run models\n", title="T" * 900)
        self.assertEqual(len(found["title"]), SECTIONS.ROUTING_TITLE_CHARS)

    def test_the_verbatim_requirement_lines_are_not_shortened(self):
        # `*_stated` is what a reviewer reads and is never routed, so the line the employer
        # actually wrote survives whole even when the routed term list was fitted.
        stated = "Experience with " + ("SAS and SQL in regulated clinical trials and " * 20)
        found = SECTIONS.extract(f"Qualifications\n- {stated}\n", title="Analyst")
        self.assertGreater(len(stated), SECTIONS.ROUTING_SHAPES["required_skills"][0])
        self.assertEqual(found["required_skills_stated"], [stated.strip(" ;.")])
        self.assertTrue(all(len(item) <= 500 for item in found.get("required_skills", [])))


if __name__ == "__main__":
    unittest.main()
