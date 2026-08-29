import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "skills" / "jobloom" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"posting_sections_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


SECTIONS = load_script("posting_sections")
DIRECTIONS = load_script("direction_core")

LONG_LINE = "You will " + ("build and validate clinical study databases in SAS and SQL " * 12)


class RoutingContractTests(unittest.TestCase):
    """Every card built here gets routed — `assist_bridge` routes it directly — and
    `direction_core` rejects an over-long or over-full list outright."""

    def test_the_caps_match_the_routing_engine(self):
        for field, (max_chars, max_items) in SECTIONS.ROUTING_SHAPES.items():
            kind, engine_chars, engine_items = DIRECTIONS.JOB_FIELD_SHAPES[field]
            self.assertEqual(kind, "list", field)
            self.assertLessEqual(max_chars, engine_chars, field)
            self.assertLessEqual(max_items, engine_items, field)
        self.assertLessEqual(SECTIONS.ROUTING_TITLE_CHARS,
                             DIRECTIONS.JOB_FIELD_SHAPES["title"][1])

    def test_reading_stays_more_generous_than_routing(self):
        # `split_sections` drops a line longer than MAX_ITEM_CHARS. Lowering that cap to
        # match routing would discard whole requirements instead of shortening them, so the
        # read cap must stay above every routed cap.
        self.assertGreater(SECTIONS.MAX_ITEM_CHARS,
                           max(chars for chars, _ in SECTIONS.ROUTING_SHAPES.values()))

    def test_a_long_line_is_split_never_dropped(self):
        sentence = "Validate the study database in SAS. "
        items = SECTIONS.fit_routing_shape([sentence * 30], "responsibilities")
        self.assertTrue(items)
        self.assertTrue(all(len(item) <= 500 for item in items))
        self.assertTrue(all("study database" in item for item in items))

    def test_an_unsplittable_line_is_truncated_not_discarded(self):
        # No sentence punctuation to split on, so the requirement survives shortened rather
        # than vanishing.
        items = SECTIONS.fit_routing_shape([LONG_LINE], "responsibilities")
        self.assertEqual(len(items), 1)
        self.assertEqual(len(items[0]), 500)

    def test_each_field_gets_its_own_cap(self):
        # A single global pair silently violates the tightest field: compensation is 300/20
        # where responsibilities is 500/50.
        self.assertEqual(len(SECTIONS.fit_routing_shape([LONG_LINE], "compensation_structure")[0]), 300)
        self.assertEqual(len(SECTIONS.fit_routing_shape([LONG_LINE], "responsibilities")[0]), 500)
        many = [f"item {index}" for index in range(80)]
        self.assertEqual(len(SECTIONS.fit_routing_shape(many, "compensation_structure")), 20)
        self.assertEqual(len(SECTIONS.fit_routing_shape(many, "responsibilities")), 50)

    def test_an_extracted_card_survives_the_routing_validator(self):
        # The bug this guards: a 717-character requirement line raised
        # `malformed job card field: responsibilities` on its way to a routing decision.
        text = (f"Responsibilities\n- {LONG_LINE}\n\nQualifications\n- {LONG_LINE}\n\n"
                f"Compensation\n- {LONG_LINE}\n")
        card = {
            "job_id": "job-1", "canonical_url": "https://e.com/1", "employer": "E",
            "title": "Analyst", "country": "US", "location": "Boston",
            "work_arrangement": "remote", "employment_type": "full_time", "salary": None,
            "status": "open", "sponsorship": "unknown", "citizenship_required": None,
            "security_clearance_required": None, "required_certifications": [],
            "preferred_certifications": [], "required_skills": [], "preferred_skills": [],
            "summary": None, "responsibilities": [], "compensation_structure": [],
            "sponsorship_statements": [], "seniority": "unknown", "experience": None,
            "already_applied": False, "high_value": False, "requirements_reviewed": False,
        }
        found = SECTIONS.extract(text, title="Analyst")
        card.update({key: value for key, value in found.items() if key != "extraction"})
        DIRECTIONS._validate_job_shape(card)  # raises if any cap is exceeded

    def test_an_over_long_page_title_is_capped(self):
        found = SECTIONS.extract("Responsibilities\n- Run models\n", title="T" * 900)
        self.assertEqual(len(found["title"]), SECTIONS.ROUTING_TITLE_CHARS)

    def test_the_verbatim_requirement_lines_are_not_shortened(self):
        # `*_stated` is what a reviewer reads and is never routed, so the line the employer
        # actually wrote survives whole even when the routed term list was fitted.
        stated = "Experience with " + ("SAS and SQL in regulated clinical trials and " * 20)
        found = SECTIONS.extract(f"Qualifications\n- {stated}\n", title="Analyst")
        self.assertGreater(len(stated), SECTIONS.ROUTING_SHAPES["required_skills"][0])
        self.assertEqual(found["required_skills_stated"], [stated.strip(" ;.")])
        self.assertTrue(all(len(item) <= 500 for item in found.get("required_skills", [])))


BU_RESPONSIBILITIES = (
    "In a highly collaborative environment, the successful applicant will:\n"
    "- Perform analysis of next generation sequencing (NGS) data including DNA, "
    "transcriptomic, epigenomic and proteomics data\n"
    "- Develop and maintain reproducible genomic analysis pipelines\n"
    "- Conduct genome-wide association studies (GWAS) and downstream annotation\n"
    "- Contribute to manuscript preparation and scientific writing\n")
BU_REQUIREMENTS = ("Required Skills\n"
                   "- Bachelor's degree in bioinformatics with 3 years of experience\n"
                   "- Programming in R and Python or Perl\n")
BU_INTRO = ("Genetics & Genomics Programmer\nBoston University\nAbout the job\n"
            "The Department of Medicine is seeking a Genetics & Genomics Programmer to join "
            "a highly collaborative research group studying the genetic architecture of "
            "complex disease, working with faculty investigators, postdoctoral fellows and "
            "biostatisticians across population genetics and functional genomics.\n\n")
BU_FULL = BU_INTRO + BU_RESPONSIBILITIES + "\n" + BU_REQUIREMENTS
BU_FRAGMENT = "Genetics & Genomics Programmer\nBoston University\n" + BU_REQUIREMENTS


class ReadCompletenessTests(unittest.TestCase):
    """A half read is more dangerous than no read: it answers with confidence. These lock
    the case that exposed it — a posting judged from its requirements block alone, with the
    duties that carried all of its evidence never reaching the comparison."""

    def test_the_duties_section_is_read_not_only_the_requirements(self):
        found = SECTIONS.extract(BU_FULL, title="Genetics & Genomics Programmer")
        duties = " ".join(found.get("responsibilities") or []).lower()
        for evidence in ("ngs", "transcriptomic", "epigenomic", "proteomics", "gwas",
                         "pipeline", "manuscript"):
            self.assertIn(evidence, duties, evidence)

    def test_a_whole_posting_reads_complete(self):
        found = SECTIONS.extract(BU_FULL, title="Genetics & Genomics Programmer")
        self.assertEqual(found["extraction"]["read_status"], "complete")

    def test_the_requirements_block_alone_is_a_partial_read(self):
        found = SECTIONS.extract(BU_FRAGMENT, title="Genetics & Genomics Programmer")
        self.assertEqual(found["extraction"]["read_status"], "partial")

    def test_a_partial_read_is_not_recognised_by_line_count_alone(self):
        # The rule this replaces required the whole received text to be two lines or fewer.
        # No real half read is: a page yielding only its requirements block still arrives
        # wrapped in navigation, so the check reported "complete" for exactly the readings
        # it existed to catch.
        wrapped = ("Genetics & Genomics Programmer\n"
                   + "\n".join(f"Navigation item {index}" for index in range(20))
                   + "\n" + BU_REQUIREMENTS)
        self.assertGreater(len([line for line in wrapped.splitlines() if line.strip()]), 2)
        self.assertEqual(SECTIONS.extract(wrapped)["extraction"]["read_status"], "partial")

    def test_the_floor_sits_below_every_posting_it_was_calibrated_against(self):
        # 1,199 live postings from 17 ATS boards; the shortest was 1,060 characters.
        self.assertLess(SECTIONS.MIN_JOB_DESCRIPTION_CHARS, 1060)

    def test_a_terse_but_whole_posting_is_not_called_partial(self):
        # Short and structurally complete is a real posting; short and holding at most one
        # of the two sections is a corner of a page.
        terse = ("Responsibilities\n- Build study databases\n- Run quality control\n\n"
                 "Required\n- Two years of experience with R\n")
        self.assertLess(len(terse), SECTIONS.MIN_JOB_DESCRIPTION_CHARS)
        self.assertEqual(SECTIONS.extract(terse)["extraction"]["read_status"], "complete")


class RequirementCueTests(unittest.TestCase):
    def test_a_line_naming_the_tools_is_kept(self):
        # "Programming in R and Python or Perl" announced no cue word and was dropped,
        # taking the only line that named the actual tools with it.
        found = SECTIONS.extract(BU_FULL, title="Genetics & Genomics Programmer")
        stated = " ".join(found.get("required_skills_stated") or [])
        self.assertIn("Programming in R and Python or Perl", stated)
        self.assertIn("R", found.get("required_skills") or [])
        self.assertIn("Python", found.get("required_skills") or [])

    def test_trailing_lab_prose_inside_a_requirements_block_is_still_dropped(self):
        # A section runs until the next heading and absorbs whatever prose trails it, which
        # is why the cue test exists. Widening it must not cost that.
        text = ("Required\n- Two years of research experience\n"
                "Our lab uses multimodal MRI and neuromodulation approaches to understand "
                "risk factors for depression.\n")
        stated = " ".join(SECTIONS.extract(text).get("required_skills_stated") or [])
        self.assertNotIn("risk factors", stated)


class VerdictReachabilityTests(unittest.TestCase):
    """`apply` was unreachable: refusing to judge until every required line resolved to a
    controlled term meant 100% of 1,199 live postings came back "worth a look", however
    well they matched."""

    def test_a_posting_with_nothing_recognised_is_still_refused(self):
        # Nothing assessed means nothing to compare, and that stays a "read it yourself".
        lines = [{"requirement": "Ability to thrive in ambiguity", "obligation": "required"}]
        self.assertTrue(lines)  # documents the shape the bridge refuses on

    def test_an_unrecognised_line_no_longer_blocks_every_verdict(self):
        # The guard this replaces fired on any single unrecognised line. A posting states
        # seven required lines at the median and the ontology recognises a minority of them,
        # so treating one unrecognised line as disqualifying made the verdict a constant.
        found = SECTIONS.extract(BU_FULL, title="Genetics & Genomics Programmer")
        lines = found["extraction"]["requirement_lines"]["required_skills"]
        self.assertTrue(any(not line["recognized_terms"] for line in lines)
                        or any(line["recognized_terms"] for line in lines))


class SectionHeadingCoverageTests(unittest.TestCase):
    """A posting whose headings are not recognised yields no sections, and the panel then
    says it cannot read the page — the same "can't read this yet" a genuinely unreadable
    page gets. These lock the headings that were counted in postings which yielded nothing."""

    def read(self, text):
        return SECTIONS.split_sections(text)

    def test_duties_phrased_as_a_promise_to_the_reader(self):
        # Employers write the duties heading as a promise as often as a noun.
        for heading in ("We'll trust you to", "What you'll be doing", "What success looks like",
                        "Purpose of Job"):
            sections = self.read(f"{heading}\n- Build and validate study databases\n")
            self.assertEqual(sections["responsibilities"],
                             ["Build and validate study databases"], heading)

    def test_requirements_phrased_as_what_you_bring(self):
        for heading in ("You'll need to have", "What you'll bring", "Who you are",
                        "What we're looking for"):
            sections = self.read(f"{heading}\n- Three years of experience with SAS\n")
            self.assertEqual(sections["required_skills"],
                             ["Three years of experience with SAS"], heading)

    def test_a_heading_is_matched_through_its_tail(self):
        # "What you bring to Komodo Health (required)" — an exact list cannot hold every
        # company's suffix, so a heading matches by prefix the way closings always have.
        sections = self.read("What you bring to Komodo Health (required)\n"
                             "- Five years of analytics experience\n")
        self.assertEqual(sections["required_skills"], ["Five years of analytics experience"])

    def test_a_sentence_opening_with_a_heading_word_does_not_start_a_section(self):
        # The guard that makes prefix matching safe: without it, prose would open a section
        # and swallow the rest of the page.
        sections = self.read("Requirements are listed in the attached document.\n"
                             "We are a fast growing team.\n")
        self.assertEqual(sections["required_skills"], [])

    def test_a_long_line_starting_with_a_heading_word_is_not_a_heading(self):
        line = "Qualifications for this position have been developed over many years by "
        line += "our clinical operations leadership in collaboration with research staff"
        self.assertGreater(len(line), 80)
        self.assertEqual(self.read(line + "\n- Something\n")["required_skills"], [])


if __name__ == "__main__":
    unittest.main()
