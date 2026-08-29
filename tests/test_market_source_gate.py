import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"market_gate_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


MARKET = load_script("market_profile")
ATS = load_script("ats_sources")


def registry_file(basis):
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"schema_version": "0.1.0", "sources": [{
        "source_id": "workday_tenant_feeds",
        "name": "Workday tenant feeds",
        "authorization_basis": basis,
        "terms_url": None,
        "recorded_at": "2026-08-29",
        "notes": "read on the operator's own compliance judgement",
    }]}, handle)
    handle.close()
    return Path(handle.name)


class MarketSourceGateTests(unittest.TestCase):
    """A market profile decides which career directions get proposed and how their market
    and accessibility axes score. A source read on the operator's own compliance judgement
    must not reach that, and the refusal has to be active: leaving it unlisted is defence by
    omission, and the next person who wants scraped postings to be more useful cannot tell a
    deliberate exclusion from an oversight."""

    def test_a_self_asserted_source_listed_in_the_registry_is_still_refused(self):
        # The whole point: it *is* listed, and it is still refused. A test that merely
        # omitted it would pass just as happily after someone added it back.
        path = registry_file(ATS.SELF_ASSERTED)
        self.addCleanup(path.unlink)
        with self.assertRaises(ValueError) as caught:
            MARKET.load_sources(path)
        self.assertIn(ATS.SELF_ASSERTED, str(caught.exception))
        self.assertIn("market profile", str(caught.exception))

    def test_the_refusal_names_what_it_protects(self):
        # The message has to say why, or the next person deletes the check and learns
        # nothing from the diff.
        path = registry_file(ATS.SELF_ASSERTED)
        self.addCleanup(path.unlink)
        with self.assertRaises(ValueError) as caught:
            MARKET.load_sources(path)
        self.assertIn("directions", str(caught.exception))

    def test_a_platform_permitted_basis_still_loads(self):
        path = registry_file("user_supplied")
        self.addCleanup(path.unlink)
        self.assertIn("workday_tenant_feeds", MARKET.load_sources(path))

    def test_every_refused_basis_is_a_real_authorization_the_system_knows(self):
        # Refusing a basis nothing can produce would be a check that never fires.
        for basis in MARKET.REFUSED_AUTHORIZATION_BASES:
            self.assertIn(basis, ATS.AUTHORIZATIONS)
            self.assertFalse(ATS.AUTHORIZATIONS[basis]["platform_permitted"])

    def test_no_refused_basis_is_also_an_accepted_one(self):
        self.assertEqual(MARKET.REFUSED_AUTHORIZATION_BASES & MARKET.AUTHORIZATION_BASES, set())


if __name__ == "__main__":
    unittest.main()
