import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "skills" / "jobloom" / "scripts"
SPEC = importlib.util.spec_from_file_location("ranking_score_impact",
                                              SCRIPTS / "ranking_score_impact.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class Reachability(unittest.TestCase):
    """Established by behaviour, so reordering the sort tuple cannot make this stale."""

    def setUp(self):
        self.result = MODULE.reachability()

    def test_the_score_decides_only_when_every_earlier_key_ties(self):
        self.assertTrue(self.result["decides_only_on_a_full_tie"])

    def test_direct_evidence_outranks_any_score(self):
        self.assertTrue(self.result["outranked_by_direct_evidence"],
                        "a 999 score must not beat one more directly covered requirement")

    def test_every_key_that_should_decide_does(self):
        self.assertEqual(set(self.result["sort_key"]["per_key"].values()), {"decides"})

    def test_the_score_sits_behind_the_four_evidence_keys(self):
        positions = self.result["sort_key"]["tuple_positions"]
        self.assertEqual(positions["ranking_score"], [4])
        for key in ("weight_percent", "direct", "covered", "technical_hits"):
            self.assertLess(positions[key][0], positions["ranking_score"][0])


class StoredData(unittest.TestCase):
    def build(self, rows):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "db.sqlite"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE routing_records (job_id TEXT, direction_id TEXT, "
                           "decision TEXT, ranking_score INTEGER)")
        connection.executemany("INSERT INTO routing_records VALUES (?,?,?,?)", rows)
        connection.commit()
        connection.close()
        return path

    def test_too_few_jobs_is_reported_as_insufficient_never_extrapolated(self):
        path = self.build([("job-1", "d1", "review", 122), ("job-1", "d2", "fail", 124)])
        result = MODULE.stored_records(path)
        self.assertEqual(result["distinct_jobs"], 1)
        self.assertEqual(result["counterfactual"], "insufficient_data")

    def test_enough_jobs_becomes_computable(self):
        path = self.build([(f"job-{i}", "d1", "review", 100 + i) for i in range(30)])
        self.assertEqual(MODULE.stored_records(path)["counterfactual"], "computable")

    def test_the_audit_only_reads(self):
        path = self.build([("job-1", "d1", "review", 122)])
        before = path.read_bytes()
        MODULE.stored_records(path)
        self.assertEqual(path.read_bytes(), before)


class Substitution(unittest.TestCase):
    def test_only_the_score_is_replaced(self):
        """A change downstream can then only have come from the score."""
        original = {"decision": "review", "ranking_score": 130, "review_reasons": ["x"],
                    "required_skill_evidence": ["a", "b"]}
        router = MODULE.route_with_score(lambda routing, card: 0)
        MODULE.direction_core.route_job = lambda profile, candidate, card: dict(original)
        replaced = router({}, {}, {})
        self.assertEqual(replaced["ranking_score"], 0)
        self.assertEqual({k: v for k, v in replaced.items() if k != "ranking_score"},
                         {k: v for k, v in original.items() if k != "ranking_score"})

    def test_the_evidence_substitution_uses_the_evidence_count(self):
        MODULE.direction_core.route_job = lambda profile, candidate, card: {
            "ranking_score": 130, "required_skill_evidence": ["a", "b", "c"]}
        router = MODULE.route_with_score(
            lambda routing, card: len(routing.get("required_skill_evidence") or []))
        self.assertEqual(router({}, {}, {})["ranking_score"], 3)


class TiebreakCounting(unittest.TestCase):
    def row(self, rank, ranking, direct=1):
        return {"job_id": f"j{rank}", "rank": rank, "weight_percent": 50,
                "ranking_score": ranking, "employer": "acme", "title": "analyst",
                "evidence": {"direct": direct, "covered": 1, "technical_hits": 1}}

    def test_a_pair_separated_only_by_the_score_is_counted(self):
        queue = {"rows": [self.row(1, 130), self.row(2, 120)]}
        self.assertEqual(MODULE.tiebreak_reached(queue)["separated_only_by_ranking_score"], 1)

    def test_a_pair_separated_by_evidence_is_not(self):
        queue = {"rows": [self.row(1, 120, direct=2), self.row(2, 130, direct=1)]}
        self.assertEqual(MODULE.tiebreak_reached(queue)["separated_only_by_ranking_score"], 0)

    def test_identical_rows_are_not_counted_as_separated(self):
        queue = {"rows": [self.row(1, 120), self.row(2, 120)]}
        self.assertEqual(MODULE.tiebreak_reached(queue)["separated_only_by_ranking_score"], 0)

    def test_an_empty_queue_has_no_pairs(self):
        self.assertEqual(MODULE.tiebreak_reached({"rows": []})["adjacent_pairs"], 0)


class Conclusions(unittest.TestCase):
    def test_the_report_never_states_a_cause(self):
        text = json.dumps(MODULE.reachability())
        for word in ("because", "therefore", "improves", "better"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
