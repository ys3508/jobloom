import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "skills" / "jobloom" / "scripts" / "evaluate_job.py"
SPEC = importlib.util.spec_from_file_location("evaluate_job", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def candidate():
    return {
        "profile_id": "candidate-1",
        "work_authorization": {
            "country": "US",
            "authorized_now": True,
            "sponsorship_now": False,
            "sponsorship_future": False,
            "employer_action_required": False,
            "confirmed": True,
            "expires_at": "2099-01-01",
        },
        "search": {
            "countries": ["US"],
            "work_arrangements": ["remote", "hybrid"],
            "employment_types": ["full_time"],
            "salary_floor": 100000,
            "salary_currency": "USD",
            "excluded_employers": [],
        },
        "citizenships": [],
        "security_clearances": [],
        "certifications": [],
        "facts": [
            {"id": "f-python", "value": "Python", "keywords": ["python"], "evidence_strength": "direct", "status": "locked"},
            {"id": "f-sql", "value": "SQL", "keywords": ["sql"], "evidence_strength": "strongly_related", "status": "confirmed"},
        ],
    }


def job():
    return {
        "job_id": "job-1",
        "canonical_url": "https://example.com/jobs/1",
        "employer": "Example",
        "title": "Backend Engineer",
        "country": "US",
        "location": "New York, NY",
        "work_arrangement": "hybrid",
        "employment_type": "full_time",
        "salary": {"currency": "USD", "min": 120000, "max": 150000},
        "status": "open",
        "sponsorship": "unknown",
        "required_skills": ["Python", "SQL"],
        "required_certifications": [],
        "already_applied": False,
    }


class EvaluateJobTests(unittest.TestCase):
    def test_supported_job_is_broad(self):
        result = MODULE.evaluate(candidate(), job())
        self.assertEqual(result["eligibility"], "pass")
        self.assertEqual(result["match"], "worth_applying")
        self.assertEqual(result["action"], "broad")

    def test_sponsorship_uncertainty_requires_review(self):
        profile = candidate()
        profile["work_authorization"]["sponsorship_future"] = True
        result = MODULE.evaluate(profile, job())
        self.assertEqual(result["eligibility"], "uncertain")
        self.assertEqual(result["action"], "review")

    def test_explicit_no_sponsorship_is_hard_failure(self):
        profile = candidate()
        profile["work_authorization"]["sponsorship_now"] = True
        posting = job()
        posting["sponsorship"] = "does_not_support"
        result = MODULE.evaluate(profile, posting)
        self.assertEqual(result["eligibility"], "fail")
        self.assertIn("required_sponsorship_not_supported", result["hard_filter_failures"])

    def test_missing_skill_never_becomes_supported(self):
        posting = job()
        posting["required_skills"].append("Rust")
        result = MODULE.evaluate(candidate(), posting)
        self.assertEqual(result["action"], "review")
        self.assertEqual(result["main_gap"], "Rust")

    def test_duplicate_is_hard_failure(self):
        posting = job()
        posting["already_applied"] = True
        result = MODULE.evaluate(candidate(), posting)
        self.assertEqual(result["action"], "skip")
        self.assertIn("duplicate_application", result["hard_filter_failures"])

    def test_salary_currency_is_not_compared_silently(self):
        posting = job()
        posting["salary"] = {"currency": "CAD", "min": 120000, "max": 150000}
        result = MODULE.evaluate(candidate(), posting)
        self.assertEqual(result["eligibility"], "uncertain")
        self.assertIn("salary_currency_requires_review", result["uncertainties"])


if __name__ == "__main__":
    unittest.main()
