import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"review_queue_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


QUEUE = load_script("review_queue")

ALLOCATIONS = [{"direction_id": "wide", "weight_percent": 85},
               {"direction_id": "narrow", "weight_percent": 10}]
PROFILES = {"wide": {"direction_id": "wide"}, "narrow": {"direction_id": "narrow"}}


def card(job_id, title, *, employer="Acme", stated=0, preferred=0):
    sections = {}
    if stated:
        sections["required_skills_stated"] = [f"requirement {i}" for i in range(stated)]
    if preferred:
        sections["preferred_skills_stated"] = [f"nice to have {i}" for i in range(preferred)]
    return {
        "job_id": job_id, "employer": employer, "title": title, "location": "Boston, MA",
        "country": "US", "work_arrangement": "remote", "employment_type": "full_time",
        "salary": None, "required_skills": [], "canonical_url": f"https://e.com/{job_id}",
        "description_sha256": job_id, "extraction": {"ats": {"apply_url": None}, "sections": sections},
    }


def routing(*, decision="review", score=100, evidence=(), technical=0):
    return {
        "decision": decision, "ranking_score": score, "review_reasons": [],
        "field_hit_totals": {"discovery_keywords": technical},
        "required_skill_evidence": [
            {"requirement": name, "covered_by_candidate_fact": strength != "none",
             "strength": strength} for name, strength in evidence],
    }


class EvidenceSummaryTests(unittest.TestCase):
    def test_only_direct_coverage_counts_as_direct(self):
        summary = QUEUE.evidence_summary(
            routing(evidence=(("R", "direct"), ("SQL", "mention_only"), ("Epic", "none"))),
            card("j1", "Analyst"))
        self.assertEqual((summary["direct"], summary["covered"], summary["uncovered"]), (1, 2, 1))
        self.assertEqual(summary["direct_requirements"], ["R"])

    def test_stated_requirements_come_from_the_posting_not_the_distiller(self):
        # `required_skill_evidence` only ever holds controlled terms that were recognised.
        # A posting writing its requirements as capabilities contributes none of them while
        # still stating requirements, and counting zero there would erase the posting.
        summary = QUEUE.evidence_summary(routing(), card("j1", "Analyst", stated=8, preferred=2))
        self.assertEqual(summary["recognized_requirements"], 0)
        self.assertEqual(summary["stated_requirements"], 10)


class OrderingTests(unittest.TestCase):
    def queue(self, cards, results):
        def route(profile, _candidate, job):
            return results[(profile["direction_id"], job["job_id"])]
        return QUEUE.build_queue(cards, {}, ALLOCATIONS, PROFILES, route=route)

    def test_evidence_outranks_the_routing_score(self):
        # The finding this ordering exists for: context-saturated postings with no
        # requirements scored higher than evidenced ones, and led the queue.
        cards = [card("j1", "Clinical Study Design Manager"), card("j2", "Biostatistician")]
        results = {("wide", "j1"): routing(score=132),
                   ("wide", "j2"): routing(score=120, evidence=(("R", "direct"), ("SAS", "direct"))),
                   ("narrow", "j1"): routing(decision="fail"),
                   ("narrow", "j2"): routing(decision="fail")}
        rows = self.queue(cards, results)["rows"]
        self.assertEqual([row["title"] for row in rows],
                         ["Biostatistician", "Clinical Study Design Manager"])

    def test_direction_weight_still_comes_first(self):
        # A heavier direction is read first even when a lighter one has better evidence;
        # the weights are the user's allocation of attention, not a quality signal.
        cards = [card("j1", "Wide role"), card("j2", "Narrow role")]
        results = {("wide", "j1"): routing(score=100),
                   ("wide", "j2"): routing(decision="fail"),
                   ("narrow", "j1"): routing(decision="fail"),
                   ("narrow", "j2"): routing(score=100, evidence=(("R", "direct"),))}
        rows = self.queue(cards, results)["rows"]
        self.assertEqual([row["weight_percent"] for row in rows], [85, 10])

    def test_a_posting_two_directions_want_is_recorded_once(self):
        cards = [card("j1", "Both")]
        results = {("wide", "j1"): routing(score=100), ("narrow", "j1"): routing(score=100)}
        queue = self.queue(cards, results)
        self.assertEqual(queue["openings_in_queue"], 1)
        self.assertEqual(queue["rows"][0]["weight_percent"], 85)
        self.assertEqual(queue["rows"][0]["also_matches"], ["narrow"])

    def test_hits_and_openings_are_reported_as_different_numbers(self):
        # Summing per-direction hits overstates the queue; that is how 93 became 119.
        cards = [card("j1", "Both"), card("j2", "Wide only")]
        results = {("wide", "j1"): routing(), ("narrow", "j1"): routing(),
                   ("wide", "j2"): routing(), ("narrow", "j2"): routing(decision="fail")}
        queue = self.queue(cards, results)
        self.assertEqual(queue["openings_in_queue"], 2)
        self.assertEqual(queue["direction_hits"], {"wide": 2, "narrow": 1})

    def test_the_unevidenced_tail_is_counted_not_ordered_by_length(self):
        # Nothing computed separates a distillation gap from a different job, so a longer
        # requirement list must not float to the top of the tail.
        cards = [card("j1", "Nursing Operations", stated=29),
                 card("j2", "Data Scientist", stated=8, preferred=1)]
        results = {("wide", "j1"): routing(score=110), ("wide", "j2"): routing(score=120),
                   ("narrow", "j1"): routing(decision="fail"),
                   ("narrow", "j2"): routing(decision="fail")}
        queue = self.queue(cards, results)
        self.assertEqual(queue["with_direct_evidence"], 0)
        self.assertEqual(queue["without_direct_evidence"], 2)
        self.assertEqual([row["title"] for row in queue["rows"]],
                         ["Data Scientist", "Nursing Operations"])  # by score, not by length
        self.assertEqual([row["evidence"]["stated_requirements"] for row in queue["rows"]], [9, 29])

    def test_ordering_never_changes_a_decision(self):
        cards = [card("j1", "Rejected"), card("j2", "Kept")]
        results = {("wide", "j1"): routing(decision="fail", score=999),
                   ("wide", "j2"): routing(score=1),
                   ("narrow", "j1"): routing(decision="fail"),
                   ("narrow", "j2"): routing(decision="fail")}
        self.assertEqual([row["title"] for row in self.queue(cards, results)["rows"]], ["Kept"])


class GroupingTests(unittest.TestCase):
    """A group is a reading aid. It may never become a merge — two postings differing only
    in one word score 0.997 on their text, so nothing may collapse them on title."""

    def queue(self, cards, results):
        def route(profile, _candidate, job):
            return results[(profile["direction_id"], job["job_id"])]
        return QUEUE.build_queue(cards, {}, ALLOCATIONS, PROFILES, route=route)

    def three_cities(self):
        cards = [card(f"j{i}", "Analytics Manager") for i in range(3)]
        for index, city in enumerate(("Boston", "Durham", "Evanston")):
            cards[index]["location"] = city
            cards[index]["canonical_url"] = f"https://e.com/j{index}"
            cards[index]["extraction"]["ats"]["apply_url"] = f"https://e.com/j{index}/apply"
        results = {}
        for index in range(3):
            results[("wide", f"j{index}")] = routing(score=100 - index)
            results[("narrow", f"j{index}")] = routing(decision="fail")
        return self.queue(cards, results)

    def test_grouping_never_removes_an_opening(self):
        queue = self.three_cities()
        self.assertEqual(queue["openings_in_queue"], 3)
        self.assertEqual(queue["in_title_groups"], 3)

    def test_every_member_keeps_its_own_identity_and_way_in(self):
        # A group offering one link would perform the merge this queue refuses.
        queue = self.three_cities()
        self.assertEqual({row["job_id"] for row in queue["rows"]}, {"j0", "j1", "j2"})
        self.assertEqual({row["apply_url"] for row in queue["rows"]},
                         {f"https://e.com/j{i}/apply" for i in range(3)})
        for row in queue["rows"]:
            siblings = row["group"]["siblings"]
            self.assertEqual(len(siblings), 2)
            self.assertNotIn(row["job_id"], [s["job_id"] for s in siblings])
            self.assertTrue(all(s["apply_url"] for s in siblings))

    def test_the_group_counts_independent_openings(self):
        queue = self.three_cities()
        self.assertTrue(all(row["group"]["independent_openings"] == 3 for row in queue["rows"]))
        self.assertTrue(all(row["group"]["shares"] == "employer and title only"
                            for row in queue["rows"]))

    def test_a_lone_opening_is_not_given_a_group(self):
        cards = [card("j1", "Only one")]
        results = {("wide", "j1"): routing(), ("narrow", "j1"): routing(decision="fail")}
        queue = self.queue(cards, results)
        self.assertNotIn("group", queue["rows"][0])
        self.assertEqual(queue["in_title_groups"], 0)

    def test_grouping_does_not_reorder_the_queue(self):
        # Evidence decides the order; grouping annotates it afterwards.
        cards = [card("j1", "Shared title"), card("j2", "Other role"), card("j3", "Shared title")]
        results = {("wide", "j1"): routing(score=100),
                   ("wide", "j2"): routing(score=90, evidence=(("R", "direct"),)),
                   ("wide", "j3"): routing(score=80)}
        results.update({("narrow", f"j{i}"): routing(decision="fail") for i in (1, 2, 3)})
        rows = self.queue(cards, results)["rows"]
        self.assertEqual([row["title"] for row in rows],
                         ["Other role", "Shared title", "Shared title"])

    def test_the_rendered_queue_says_the_openings_are_independent(self):
        queue = self.three_cities()
        text = QUEUE.render(queue)
        self.assertIn("3 independent openings", text)
        self.assertIn("same title is not the same job", text)


class CardLoadingTests(unittest.TestCase):
    def test_one_card_per_opening_not_per_posting(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for index, fingerprint in enumerate(("a", "a", "b")):
            payload = card(f"job-{index}", "Nurse")
            payload["description_sha256"] = fingerprint
            (root / f"job-{index}.json").write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(len(QUEUE.load_cards(root)), 2)


if __name__ == "__main__":
    unittest.main()
