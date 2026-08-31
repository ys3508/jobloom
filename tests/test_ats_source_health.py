import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "ats_source_health",
    Path(__file__).parents[1] / "skills" / "jobloom" / "scripts" / "ats_source_health.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def source(company="Acme", ats="greenhouse", board="https://boards.greenhouse.io/acme"):
    return {"company": company, "ats": ats, "board_url": board, "enabled": True}


def card(employer="Acme", ats="greenhouse", **overrides):
    base = {"employer": employer, "ats": ats, "title": "Data Analyst",
            "location": "New York, NY", "canonical_url": "https://boards.greenhouse.io/acme/1",
            "description": "work", "description_sha256": "sha-1", "requisition_id": "1"}
    return {**base, **overrides}


def corpus(tmp: Path, cards):
    directory = tmp / "jobs"
    directory.mkdir(parents=True)
    for index, item in enumerate(cards):
        (directory / f"job-{index:04d}.json").write_text(json.dumps(item))
    (directory / "manifest.json").write_text(json.dumps({"not": "a card"}))
    return directory


class Loading(unittest.TestCase):
    def test_a_file_without_an_ats_is_not_a_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = corpus(Path(tmp), [card(), card()])
            self.assertEqual(len(MODULE.load_corpus(directory)), 2)


class SignalRates(unittest.TestCase):
    def rates(self, cards, board_host="boards.greenhouse.io"):
        return MODULE.signal_rates(cards, board_host)

    def test_a_blank_field_is_counted_as_a_rate_not_a_count(self):
        rates = self.rates([card(), card(employer="  ")])
        self.assertEqual(rates["blank_employer"], 0.5)

    def test_undecoded_entities_are_caught(self):
        self.assertEqual(self.rates([card(title="R&amp;D Lead")])["undecoded_entities_title"], 1.0)

    def test_a_missing_id_that_sits_in_the_url_is_reported_as_recoverable(self):
        """Two adapters never extract an id that is the last path segment."""
        rates = self.rates([card(requisition_id=None,
                                 canonical_url="https://jobs.lever.co/acme/8f3a91b2c4d5")])
        self.assertEqual(rates["missing_requisition_id"], 1.0)
        self.assertEqual(rates["requisition_id_recoverable_from_url"], 1.0)

    def test_a_missing_id_with_no_id_in_the_url_is_not_called_recoverable(self):
        rates = self.rates([card(requisition_id=None,
                                 canonical_url="https://boards.greenhouse.io/acme/apply")])
        self.assertNotIn("requisition_id_recoverable_from_url", rates)

    def test_a_url_is_measured_against_the_registered_board_not_the_ats_name(self):
        """Four real boards serve from the employer's own domain; that is hosting, not a fault."""
        own = card(canonical_url="https://www.acme.com/careers/1")
        self.assertEqual(self.rates([own], "www.acme.com").get(
            "url_off_registered_board_host", 0), 0)
        self.assertEqual(self.rates([own], "boards.greenhouse.io")[
            "url_off_registered_board_host"], 1.0)

    def test_a_field_that_changed_type_is_caught(self):
        self.assertEqual(self.rates([card(required_skills="R, SAS")])[
            "required_skills_not_a_list"], 1.0)

    def test_an_empty_source_has_no_rates(self):
        self.assertEqual(self.rates([]), {})


class MassPosting(unittest.TestCase):
    def test_one_description_across_locations_is_a_cluster(self):
        cards = [card(location=city, description_sha256="same") for city in ("NY", "SF", "LA")]
        result = MODULE.mass_posting_clusters(cards)
        self.assertEqual(result["clusters"], 1)
        self.assertEqual(result["cards_in_clusters"], 3)
        self.assertEqual(result["widest_cluster_locations"], 3)

    def test_the_same_description_in_one_location_is_not_a_cluster(self):
        cards = [card(location="NY", description_sha256="same") for _ in range(3)]
        self.assertEqual(MODULE.mass_posting_clusters(cards)["clusters"], 0)

    def test_a_card_without_a_description_hash_is_not_clustered(self):
        self.assertEqual(MODULE.mass_posting_clusters(
            [card(description_sha256=None)])["clusters"], 0)


class Profiling(unittest.TestCase):
    def test_an_employer_the_registry_does_not_know_is_reported_not_folded_in(self):
        profile = MODULE.profile_sources([source()], [card(), card(employer="Other Co")])
        self.assertEqual(profile["sources"]["acme::greenhouse"]["cards"], 1)
        self.assertEqual(profile["unregistered_employers"], {"other co::greenhouse": 1})

    def test_a_registered_source_with_no_cards_still_gets_a_profile(self):
        profile = MODULE.profile_sources([source(), source("Ghost")], [card()])
        self.assertEqual(profile["sources"]["ghost::greenhouse"]["cards"], 0)

    def test_a_signal_at_one_is_reported_as_adapter_shape_not_a_fault(self):
        cards = [card(requisition_id=None,
                      canonical_url="https://jobs.lever.co/acme/8f3a91b2c4d5")
                 for _ in range(MODULE.MIN_CARDS_FOR_DRIFT)]
        gaps = MODULE.known_gaps(MODULE.profile_sources([source(ats="lever")],
                                                        [{**c, "ats": "lever"} for c in cards]))
        self.assertIn("missing_requisition_id", {g["signal"] for g in gaps})

    def test_a_signal_at_one_from_too_few_cards_is_not_called_a_shape(self):
        profile = MODULE.profile_sources([source()], [card(title="")])
        self.assertEqual(MODULE.known_gaps(profile), [])


class Drift(unittest.TestCase):
    def profile(self, cards, sources=None):
        return MODULE.profile_sources(sources or [source()], cards)

    def healthy(self, n=10):
        return [card() for _ in range(n)]

    def test_an_identical_corpus_is_unchanged_not_merely_healthy(self):
        base = self.profile(self.healthy())
        result = MODULE.compare(base, self.profile(self.healthy()))
        self.assertEqual(result["findings"]["acme::greenhouse"]["verdict"], "unchanged")

    def test_a_rate_that_was_always_high_is_not_drift(self):
        """The finding that reshaped this tool: 1,883 cards would have alarmed on day one."""
        def always(n):
            return [card(requisition_id=None,
                         canonical_url=f"https://jobs.lever.co/acme/8f3a91b2c4d{i}")
                    for i in range(n)]
        # A grown corpus, so this takes the drift path rather than the identical shortcut.
        result = MODULE.compare(self.profile(always(10)), self.profile(always(14)))
        self.assertEqual(result["findings"]["acme::greenhouse"]["verdict"], "healthy")

    def test_a_field_going_blank_is_drift(self):
        now = [card(employer="Acme", title="") for _ in range(10)]
        result = MODULE.compare(self.profile(self.healthy()), self.profile(now))
        finding = result["findings"]["acme::greenhouse"]
        self.assertEqual(finding["verdict"], "suspect_parser_drift")
        self.assertTrue(any(r.startswith("blank_title") for r in finding["reasons"]))

    def test_a_signal_disappearing_is_drift_too(self):
        was = [card(title="R&amp;D") for _ in range(10)]
        result = MODULE.compare(self.profile(was), self.profile(self.healthy()))
        self.assertEqual(result["findings"]["acme::greenhouse"]["verdict"], "suspect_parser_drift")

    def test_yield_falling_to_zero_needs_no_threshold(self):
        result = MODULE.compare(self.profile(self.healthy()), self.profile([]))
        finding = result["findings"]["acme::greenhouse"]
        self.assertEqual(finding["verdict"], "no_yield")
        self.assertEqual(finding["reasons"], ["yield_fell_to_zero_from_10"],
                         "rate deltas over an empty source are zero by construction")

    def test_a_source_that_never_yielded_is_not_reported_as_broken(self):
        """Comparing a corpus against itself must not accuse anything."""
        base = self.profile([], [source("Ghost")])
        result = MODULE.compare(base, self.profile([], [source("Ghost")]))
        self.assertEqual(result["findings"]["ghost::greenhouse"]["verdict"], "never_yielded")

    def test_a_corpus_compared_against_itself_reports_no_faults(self):
        base = self.profile(self.healthy())
        result = MODULE.compare(base, self.profile(self.healthy()))
        self.assertEqual(result["counts"], {"unchanged": 1})

    def test_a_zero_yield_board_whose_cards_arrived_under_another_name(self):
        """All three real zero-yield boards were this, and all three were working."""
        cards = [card(employer="Acme Inc.") for _ in range(6)]
        base = MODULE.profile_sources([source("Acme Corporation")], cards)
        result = MODULE.compare(base, MODULE.profile_sources([source("Acme Corporation")], cards))
        finding = result["findings"]["acme corporation::greenhouse"]
        self.assertEqual(finding["verdict"], "employer_name_mismatch")
        self.assertIn("cards_present_under_acme inc.::greenhouse", finding["reasons"])

    def test_a_rename_is_only_claimed_on_the_same_ats(self):
        cards = [card(employer="Acme Inc.", ats="lever",
                      canonical_url="https://jobs.lever.co/acme/1") for _ in range(6)]
        profile = MODULE.profile_sources([source("Acme Corporation")], cards)
        self.assertIsNone(MODULE.likely_rename(profile["sources"]["acme corporation::greenhouse"],
                                               profile["unregistered_employers"]))

    def test_a_handful_of_cards_is_insufficient_data_not_a_verdict(self):
        result = MODULE.compare(self.profile(self.healthy()), self.profile([card(title="")]))
        self.assertEqual(result["findings"]["acme::greenhouse"]["verdict"], "insufficient_data")

    def test_a_source_absent_from_the_baseline_is_named_not_judged(self):
        base = self.profile(self.healthy())
        now = MODULE.profile_sources([source(), source("New Co")], self.healthy())
        result = MODULE.compare(base, now)
        self.assertEqual(result["findings"]["new co::greenhouse"]["verdict"], "not_in_baseline")

    def test_a_changed_but_sound_source_is_healthy(self):
        result = MODULE.compare(self.profile(self.healthy()), self.profile(self.healthy(12)))
        self.assertEqual(result["counts"], {"healthy": 1})


class BaselineFile(unittest.TestCase):
    def test_a_baseline_is_private_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "baseline.json"
            MODULE.write_private(path, "{}")
            self.assertEqual(oct(path.stat().st_mode)[-3:], "600")
            self.assertEqual(oct(path.parent.stat().st_mode)[-3:], "700")
            with self.assertRaises(FileExistsError):
                MODULE.write_private(path, "{}")


if __name__ == "__main__":
    unittest.main()
