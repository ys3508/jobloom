import importlib.util
import json
import sqlite3
import tempfile
import unittest
from unittest import mock
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
    """Patched rather than assigned: a router left replaced leaks into every later test."""

    def routing(self, **overrides):
        return {"decision": "review", "ranking_score": 130, "review_reasons": ["x"],
                "required_skill_evidence": [], **overrides}

    def test_only_the_score_is_replaced(self):
        """A change downstream can then only have come from the score."""
        original = self.routing(required_skill_evidence=[{"covered_by_candidate_fact": True}])
        with mock.patch.object(MODULE.direction_core, "route_job",
                               return_value=dict(original)):
            replaced = MODULE.route_with_score(lambda routing, card: 0)({}, {}, {})
        self.assertEqual(replaced["ranking_score"], 0)
        self.assertEqual({k: v for k, v in replaced.items() if k != "ranking_score"},
                         {k: v for k, v in original.items() if k != "ranking_score"})

    def test_the_router_is_restored_after_a_substitution(self):
        before = MODULE.direction_core.route_job
        with mock.patch.object(MODULE.direction_core, "route_job", return_value={}):
            pass
        self.assertIs(MODULE.direction_core.route_job, before)


class EvidenceSubstitution(unittest.TestCase):
    def entries(self, covered):
        return [{"requirement": f"r{i}", "covered_by_candidate_fact": flag}
                for i, flag in enumerate(covered)]

    def test_only_covered_requirements_are_counted(self):
        """The length of the list is how many requirements were recognised, which a posting
        can raise just by listing more of them."""
        routing = {"required_skill_evidence": self.entries([True, False, False, True])}
        self.assertEqual(MODULE.covered_requirement_count(routing, {}), 2)
        self.assertNotEqual(MODULE.covered_requirement_count(routing, {}),
                            len(routing["required_skill_evidence"]))

    def test_no_evidence_counts_zero(self):
        self.assertEqual(MODULE.covered_requirement_count(
            {"required_skill_evidence": self.entries([False, False])}, {}), 0)

    def test_a_missing_field_counts_zero(self):
        self.assertEqual(MODULE.covered_requirement_count({}, {}), 0)


class DirectionHits(unittest.TestCase):
    """The regression for a comparison that read a key build_queue does not return."""

    def test_a_missing_key_can_no_longer_pass_for_agreement(self):
        baseline = {"direction_hits": {"d1": 7}, "rows": []}
        neutral = {"direction_hits": {"d1": 9}, "rows": []}
        self.assertNotEqual(baseline["direction_hits"], neutral["direction_hits"])
        self.assertNotIn("hits_per_direction", baseline,
                         "build_queue returns direction_hits; reading another key "
                         "compared None with None and called it identical")

    def test_the_audit_reads_the_key_the_queue_returns(self):
        source = (Path(__file__).parents[1] / "skills" / "jobloom" / "scripts"
                  / "ranking_score_impact.py").read_text()
        self.assertIn('baseline["direction_hits"]', source)
        self.assertNotIn('get("hits_per_direction")', source)


class AssistBridgeOrdering(unittest.TestCase):
    """Scores differing is not an order changing."""

    def order(self, members):
        with_score = [d for _, d in sorted(members, key=lambda m: (-m[0], m[1]))]
        without_score = sorted(direction_id for _, direction_id in members)
        return with_score, without_score

    def test_different_scores_that_agree_with_alphabetical_order_change_nothing(self):
        with_score, without = self.order([(130, "alpha"), (120, "beta")])
        self.assertEqual(with_score, without,
                         "a bucket counted as decided here would be an overstatement")

    def test_different_scores_that_disagree_do_change_the_order(self):
        with_score, without = self.order([(120, "alpha"), (130, "beta")])
        self.assertNotEqual(with_score, without)

    def test_equal_scores_leave_the_order_to_the_direction_id(self):
        with_score, without = self.order([(130, "beta"), (130, "alpha")])
        self.assertEqual(with_score, without)


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
