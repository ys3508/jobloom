import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"e2e_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


EXTRACT = load_script("extract_candidate_facts")
FINALIZE = load_script("finalize_candidate")
INGEST = load_script("ingest_job")
EVALUATE = load_script("evaluate_job")


class EndToEndTests(unittest.TestCase):
    def test_confirmed_resume_and_reviewed_jd_reach_evidence_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            resume = Path(directory) / "master-resume.txt"
            resume.write_text("SKILLS\nPython\n\nEXPERIENCE\nBuilt Python services.\n", encoding="utf-8")
            review = EXTRACT.build_review_packet(resume)

        for fact in review["facts"]:
            fact["decision"] = "confirmed"
            if fact["type"] == "skill":
                fact["evidence_strength"] = "direct"
                fact["review_note"] = "Confirmed against the experience entry."

        settings = {
            "profile_id": "candidate-e2e",
            "work_authorization": {
                "country": "US", "authorized_now": True, "sponsorship_now": False,
                "sponsorship_future": False, "employer_action_required": False,
                "confirmed": True, "expires_at": "2099-01-01",
            },
            "search": {
                "countries": ["US"], "locations": [], "work_arrangements": ["remote"],
                "employment_types": ["full_time"], "salary_floor": 100000,
                "salary_currency": "USD", "excluded_employers": [],
            },
        }
        candidate = FINALIZE.finalize(review, settings)
        posting = {
            "@type": "JobPosting", "title": "Backend Engineer", "url": "/jobs/1",
            "description": "Build Python services.", "employmentType": "FULL_TIME",
            "jobLocationType": "TELECOMMUTE", "hiringOrganization": {"name": "Example"},
            "jobLocation": {"address": {"addressCountry": {"name": "United States"}}},
            "skills": "Python", "validThrough": "2099-01-01",
            "baseSalary": {"currency": "USD", "value": {"minValue": 120000, "maxValue": 150000}},
        }
        card = INGEST.card_from_posting(posting, "https://example.com/listing", "")

        blocked = EVALUATE.evaluate(candidate, card)
        self.assertEqual(blocked["eligibility"], "uncertain")
        self.assertIn("job_requirements_unreviewed", blocked["uncertainties"])

        card["requirements_reviewed"] = True
        card["extraction"]["needs_user_review"] = False
        result = EVALUATE.evaluate(candidate, card)
        self.assertEqual(card["canonical_url"], "https://example.com/jobs/1")
        self.assertEqual(card["country"], "US")
        self.assertEqual(result["eligibility"], "pass")
        self.assertEqual(result["match"], "strong")
        self.assertEqual(result["evidence_matches"][0]["fact_ids"], ["fact-0001"])


if __name__ == "__main__":
    unittest.main()
