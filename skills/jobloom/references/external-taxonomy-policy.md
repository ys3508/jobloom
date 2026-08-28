# External occupation taxonomies: O*NET and ESCO

Decision recorded 2026-08-28. Revisit under the trigger below rather than ad hoc.

## Decision

**Reference them; do not import them.**

Function nodes stay curated by Jobloom. Each may carry `source_refs` pointing at an
external occupation code, but no external taxonomy is bulk-imported, and no text,
skill list, or task statement is copied from either database.

## Why not import

**The cost is inverted.** The ontology currently holds three function nodes covering
healthcare, clinical research, and life-sciences analytics. Importing a taxonomy of
several hundred occupations to seed three nodes is more maintenance surface than it
removes.

**The granularity does not line up.** O*NET groups at the SOC level, which is coarser
than a Jobloom function node: one statistics occupation spans what this ontology
separates into biostatistical analysis and statistical programming. An import would
either flatten distinctions the engine depends on, or need a hand-written crosswalk —
which is the curation work the import was supposed to avoid.

**Attribution is a real obligation for no current benefit.** Both sources require
attribution when their data is redistributed. Carrying that obligation is reasonable
once their data is doing work; it is not reasonable while it is doing none.

**What the import was supposed to protect against is already covered.** The risk is
that an agent invents a function node with no basis. That is held down by the golden
sample gate, the requirement that every signature capability carry a matching pattern,
and the calibration rule that a skill never hit by a real resume is a dead node.
An external taxonomy would not add to any of those.

## What is adopted

- `source_refs` stays in the FunctionNode schema and accepts `onet` and `esco` entries.
- An entry is only added when its code has been checked against the published release.
  **Codes are never written from memory.** A reference that cannot be verified is left
  out; an unverifiable identifier in a shipped asset is worse than no identifier.
- If any external field is ever copied rather than referenced, attribution must be
  added to the ontology asset and to any published output that carries it:
  - O*NET: CC BY 4.0, attribution required — <https://www.onetcenter.org/license_db.html>
  - ESCO: Commission Decision 2011/833/EU reuse terms, attribution required —
    <https://esco.ec.europa.eu/en/about-esco/faq?page=1>

## When to revisit

Any one of these makes the import worth its cost:

1. Function nodes exceed roughly 20, at which point hand-curation stops being cheaper.
2. Scope expands past healthcare, research, and life-sciences analytics, where curated
   coverage no longer exists.
3. Non-English markets are targeted, where ESCO's multilingual labels solve a problem
   nothing else does.
4. An external party needs to audit the taxonomy against a recognised standard.

Until then the curated ontology is the source of truth, and its defence is the
calibration data, not an appeal to authority.
