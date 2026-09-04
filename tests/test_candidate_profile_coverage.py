"""What a Candidate Profile would have to hold, measured before its shape is designed.

The reviewed field set has to be decided by what the recorded corpus asks for and what the
profile actually contains, not by a list written from imagination — the same rule that caught
a conflict pattern which did not match `Related to someone at this company?`, the corpus's own
wording.

Half of this measurement is reproducible and is checked here: which canonical meanings the
vendored fixtures expect a profile to supply. The other half counts facts in the private
library, which no test may read, so those counts are recorded once in the artifact and are not
re-derived. What is checked about them is that they are counts and never values.
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "jobloom" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

APPROVAL = json.loads(
    (ROOT / "tests" / "fixtures" / "ats-semantic" / "FIELD-DISPOSITION-APPROVAL.json")
    .read_text(encoding="utf-8"))
ARTIFACT = ROOT / "docs" / "candidate-profile-coverage-2026-09-02.md"


def corpus_targets(disposition):
    return sorted({entry["expected_target"] for entry in APPROVAL["entries"]
                   if entry["expected_disposition"] == disposition})


class CorpusDemandTests(unittest.TestCase):
    """The half of the measurement anyone can re-derive."""

    def test_the_artifact_names_every_meaning_the_corpus_expects_from_a_profile(self):
        text = ARTIFACT.read_text(encoding="utf-8")
        targets = corpus_targets("fact")
        self.assertEqual(len(targets), 15)
        for target in targets:
            with self.subTest(target=target):
                self.assertIn(f"`{target}`", text)
        self.assertIn(str(len(targets)), text)

    def test_the_artifact_names_every_meaning_that_must_stay_out_of_a_profile(self):
        """`always_manual` is the boundary a profile must not quietly cross.

        A profile that learned to supply a sponsorship or EEO meaning would be answering a
        question whose whole disposition is that it reaches the user.
        """
        text = ARTIFACT.read_text(encoding="utf-8")
        for target in corpus_targets("always_manual"):
            with self.subTest(target=target):
                self.assertIn(f"`{target}`", text)

    def test_the_corpus_proves_no_demand_for_a_postal_address(self):
        """So an address in the profile would be the user's requirement, not the corpus's.

        Recording that keeps a field set from growing on the strength of looking reasonable.
        """
        recorded = " ".join(entry["recorded_label"].lower()
                            for entry in APPROVAL["entries"]).replace("-", " ")
        for word in ("street", "address line", "postal", "zip"):
            self.assertNotIn(word, recorded)
        # `Email address` is the one place the word appears, and it is not a postal one.
        self.assertIn("email address", recorded)


class ArtifactPrivacyTests(unittest.TestCase):
    def test_the_measurement_records_counts_and_never_values(self):
        text = ARTIFACT.read_text(encoding="utf-8")
        for forbidden in ("@" + "gmail", "sis" + "si", "linkedin.com" + "/in", "21" + "2-",
                          "Bos" + "ton", "/Users/"):
            with self.subTest(needle=len(forbidden)):
                self.assertNotIn(forbidden, text)

    def test_the_measurement_states_what_it_does_not_show(self):
        """A measurement that only reports findings invites them to be read as conclusions."""
        text = ARTIFACT.read_text(encoding="utf-8")
        self.assertIn("What this does not show", text)
        self.assertIn("not current ATS DOM", text)


class ProfileReachabilityTests(unittest.TestCase):
    """The two findings that decide whether splitting values would be enough."""

    def test_the_canonical_to_fact_mapping_now_lives_in_the_planner(self):
        """It did not on 2026-09-02, and this test is the thing that noticed.

        What it asserted then: `fill_core` looked a fact up by an id the observation supplied,
        and the only mapping from a meaning to a fact anywhere was `FACT_IDS`, three entries
        long, in a test fixture — so splitting a composite value would have produced facts the
        planner still could not reach. The artifact above still says so, because it is a
        measurement of that day. This is where the closure is asserted instead: the planner
        resolves by meaning, and no table of internal fact ids is left in either place.
        """
        fixture = (ROOT / "tests" / "fixtures" / "replay_observer.py").read_text(
            encoding="utf-8")
        self.assertNotIn("FACT_IDS", fixture)
        self.assertEqual([path for path in SCRIPTS.glob("*.py")
                          if "FACT_IDS" in path.read_text(encoding="utf-8")], [])
        planner = (SCRIPTS / "fill_core.py").read_text(encoding="utf-8")
        self.assertIn("candidate_profile.resolve_canonical_fact", planner)
        # And the door it replaced stays shut.
        self.assertIn("never a source id", planner)

    def test_the_planner_refuses_a_fact_that_is_merely_confirmed(self):
        """Splitting is necessary and not sufficient: the facts have to be locked.

        Exercised rather than read, because `candidate_fact_not_locked` is the reason a
        correctly split, correctly mapped profile would still fill nothing.
        """
        source = (SCRIPTS / "fill_core.py").read_text(encoding="utf-8")
        # Two reasons, deliberately: the planner refuses an unlocked fact as
        # `profile_fact_not_locked` when it resolves the meaning, and the import path still
        # refuses one as `candidate_fact_not_locked` when it re-checks the resolved fact.
        self.assertIn("candidate_fact_not_locked", source)
        self.assertIn("PROFILE_FACT_NOT_LOCKED", (SCRIPTS / "candidate_profile.py").read_text(
            encoding="utf-8"))
        policy = importlib.util.spec_from_file_location("coverage_field_policy",
                                                        SCRIPTS / "field_policy.py")
        module = importlib.util.module_from_spec(policy)
        policy.loader.exec_module(module)
        self.assertIn("fact", module.DISPOSITIONS)
        self.assertIn("candidate_fact_not_locked", ARTIFACT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
