import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


EXTRACT = load_script("extract_candidate_facts")
FINALIZE = load_script("finalize_candidate")


def settings():
    return {
        "profile_id": "candidate-real",
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
    }


class CandidatePipelineTests(unittest.TestCase):
    def packet(self):
        with tempfile.TemporaryDirectory() as directory:
            resume = Path(directory) / "resume.txt"
            resume.write_text("SKILLS\nPython, SQL\n\nEXPERIENCE\nBuilt data services.\n", encoding="utf-8")
            return EXTRACT.build_review_packet(resume)

    def test_extraction_is_proposed_and_traceable(self):
        packet = self.packet()
        self.assertTrue(packet["source_document"]["sha256"])
        self.assertTrue(all(fact["decision"] == "pending" for fact in packet["facts"]))
        self.assertTrue(all("locator" in fact["source"] for fact in packet["facts"]))

    def test_pending_fact_blocks_candidate_generation(self):
        with self.assertRaisesRegex(ValueError, "pending review"):
            FINALIZE.finalize(self.packet(), settings())

    def test_confirmed_facts_generate_hashed_candidate(self):
        packet = self.packet()
        for fact in packet["facts"]:
            fact["decision"] = "confirmed"
        candidate = FINALIZE.finalize(packet, settings())
        self.assertEqual(len(candidate["facts"]), 3)
        self.assertTrue(candidate["content_sha256"])
        self.assertTrue(all(fact["status"] == "confirmed" for fact in candidate["facts"]))

    def test_rejected_fact_is_excluded(self):
        packet = self.packet()
        for index, fact in enumerate(packet["facts"]):
            fact["decision"] = "rejected" if index == 0 else "confirmed"
        candidate = FINALIZE.finalize(packet, settings())
        self.assertEqual(len(candidate["facts"]), 2)


if __name__ == "__main__":
    unittest.main()
