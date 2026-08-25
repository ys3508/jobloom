import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills" / "jobloom" / "scripts" / "ingest_job.py"
SPEC = importlib.util.spec_from_file_location("ingest_job", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


HTML = """
<html><head><script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Backend Engineer",
  "url": "https://example.com/jobs/backend-1",
  "description": "<p>Build reliable services using Python.</p>",
  "employmentType": "FULL_TIME",
  "jobLocationType": "TELECOMMUTE",
  "hiringOrganization": {"name": "Example Corp"},
  "jobLocation": {"address": {"addressCountry": "US", "addressLocality": "New York", "addressRegion": "NY"}},
  "skills": "Python; SQL",
  "baseSalary": {"currency": "USD", "value": {"minValue": 120000, "maxValue": 150000, "unitText": "YEAR"}},
  "validThrough": "2099-01-01"
}
</script></head><body><h1>Backend Engineer</h1></body></html>
"""


class JobIngestionTests(unittest.TestCase):
    def test_json_ld_builds_reviewable_card(self):
        card = MODULE.build_card(HTML, "https://example.com/jobs/backend-1", "html")
        self.assertEqual(card["employer"], "Example Corp")
        self.assertEqual(card["work_arrangement"], "remote")
        self.assertEqual(card["required_skills"], ["Python", "SQL"])
        self.assertFalse(card["requirements_reviewed"])
        self.assertTrue(card["description_sha256"])

    def test_plain_text_never_guesses_identity_fields(self):
        card = MODULE.build_card("Backend role using Python", "local-file", "text")
        self.assertEqual(card["employer"], "unknown")
        self.assertEqual(card["title"], "unknown")
        self.assertFalse(card["requirements_reviewed"])


if __name__ == "__main__":
    unittest.main()
