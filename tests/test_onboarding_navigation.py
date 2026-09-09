"""Where the window puts you when you open it, which is a decision and not a detail.

A profile round and a resume stranded by a snapshot change are two separate pieces of work.
The page reached the second only through finishing the first, so growing the round from nine
fields to twenty put an unrelated wait in front of an application that was ready to go: every
endpoint answered correctly, `carryable` was 3, and the window still opened on "your name"
with no way through to the carry.

That could not be caught by reading the source — the condition it turned on read perfectly
sensibly — so these run the shipped `<script>` against a stub DOM and canned responses, and
assert the screen it actually chose. `onboarding_harness.mjs` does the loading.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
HARNESS = ROOT / "tests" / "fixtures" / "onboarding_harness.mjs"

STRANDED_BLOCKING_AN_APPLICATION = {
    "version_id": "resume-a", "direction": "research-clinical-data-v1",
    "migratable": True, "carried_by": None, "blocked_reason": None,
    "application_id": "app-1", "lock_lost": True, "in_progress": None,
}
ROUND = {
    "fields": [{"canonical_id": "contact.full_name", "value": "", "proposed": True},
               {"canonical_id": "contact.postal_code", "value": "", "proposed": False}],
    "resumed": True, "countries": ["United States"],
}


def state(has_profile=True, answered=False):
    asked = ["contact.full_name", "contact.postal_code"]
    return {"has_profile": has_profile, "round": "onboarding-v1",
            "screens": [{"name": "name", "fields": ["contact.full_name"]},
                        {"name": "address", "fields": ["contact.postal_code"]}],
            "fields_in_round": asked,
            # What the active profile can already answer. A round that grew after the profile
            # was registered leaves the rest of it blank, which is the case that broke.
            "resolvable": asked if answered else ["contact.full_name"],
            "unresolved": {}, "open_round": None if answered else "onboarding-v1"}


def open_window(profile_state, stranded):
    if not shutil.which("node"):
        raise unittest.SkipTest("node is required to run the onboarding page harness")
    plan = {"responses": {"/api/state": profile_state,
                          "/api/resume-migrations": {"stranded": stranded,
                                                     "carryable": len(stranded)},
                          "/api/round": ROUND}}
    finished = subprocess.run(["node", str(HARNESS), json.dumps(plan)],
                              capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    if finished.returncode != 0:
        raise AssertionError(f"onboarding harness failed: {finished.stderr[-2000:]}")
    return json.loads(finished.stdout)


class OnboardingNavigationTests(unittest.TestCase):

    def test_an_unfinished_round_does_not_hide_a_resume_an_application_waits_on(self):
        opened = open_window(state(answered=False), [STRANDED_BLOCKING_AN_APPLICATION])
        self.assertEqual(opened["screen"], "landing")
        self.assertEqual(opened["buttons"], ["Restore them", "Carry on"])
        # Named, not merely reachable: the reason one of them is urgent is on the screen.
        self.assertIn("Restore application materials (1)", opened["shown"])
        self.assertIn("holding up an application", opened["shown"])

    def test_the_carry_is_asked_about_whenever_a_profile_exists(self):
        """The old page asked only after the round was complete, so it never knew."""
        opened = open_window(state(answered=False), [STRANDED_BLOCKING_AN_APPLICATION])
        self.assertIn("/api/resume-migrations", [r["path"] for r in opened["requests"]])

    def test_an_answered_round_still_goes_straight_to_the_carry(self):
        opened = open_window(state(answered=True), [STRANDED_BLOCKING_AN_APPLICATION])
        self.assertEqual(opened["screen"], "migrations")

    def test_an_unfinished_round_with_nothing_stranded_is_the_round(self):
        opened = open_window(state(answered=False), [])
        self.assertEqual(opened["screen"], "welcome")
        self.assertIsNone(opened["entry"])

    def test_a_window_with_no_profile_yet_asks_nothing_about_resumes(self):
        """There is no snapshot to have stranded anything, so the question is meaningless."""
        opened = open_window(state(has_profile=False, answered=False), [])
        self.assertEqual(opened["screen"], "welcome")
        self.assertEqual([r["path"] for r in opened["requests"]],
                         ["/api/state", "/api/round"])


if __name__ == "__main__":
    unittest.main()
