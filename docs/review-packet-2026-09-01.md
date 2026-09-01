# Review packet — overnight work, 2026-09-01

Branch `feat/record-manual-applications`. Working tree clean. Nothing pushed, no PR, no commit
amended. No real site touched, no submission, no message sent, no dependency installed.

## Commits, oldest first

| Commit | Purpose |
| --- | --- |
| `4307912` | `fix: require pdf application materials` |
| `9b923f1` | `docs: accept staged fill-only browser worker` |
| `65ce079` | `feat: define first-form answer boundaries` |
| `5ebc878` | `fix: close nondisclosure policy lifecycle gaps` |
| `0bea8d0` | `fix: require explicit nondisclosure handling evidence` |
| `b98ece1` | `feat: define fill worker protocol and form coverage chain` |
| `372a1bc` | `fix: preserve independent protocol observations` |
| `6616322` | `test: add attributed ats semantic replays` |

## Tests

912 at the start of the session, 979 now. `python3 -m unittest discover -s tests` — OK.
`git diff --check` — clean. Focused runs named in each task's plan entry all pass.

## Schema and dependency changes

`fill_pages` gained `final_page`, `submit_control_seen`, `predecessor_checkpoint_sha256`, each
via idempotent ALTER guarded by a `PRAGMA table_info` check, matching the existing pattern at
`application_core.py:202`. New tables `nondisclosure_policies` and `nondisclosure_handling`.
The production fill tables are empty, so no data migration was required. **No new runtime
dependency**; the replay server is `http.server` from the standard library.

## Vendored third-party material

Nine files, MIT, Jeremy Watt, pinned commit `081a5d9d793da29111e2d5331767021718f1d8b5`, under
`tests/fixtures/ats-semantic/upstream/`. Attribution in `NOTICE.md`, digests in
`SHA256SUMS.json`, both verified by test, including against upstream's own `provenance.json`.

## Where to look hardest

1. **Inference from absence.** Three defects of this class have already been found and fixed
   (`not_present` from zero rows, `user_handled` from a vanished control, `not_present` from
   incomplete coverage). Assume a fourth exists. `worker_protocol.chain_issue` and
   `field_policy.handling_summary` are the likeliest hosts.
2. **Two chains in one session.** `chain_issue` sorts by index and checks consecutiveness and
   predecessor hashes; I have not proved that a session cannot hold pages forming two valid
   sub-chains, or that a page cannot be re-observed after its successor.
3. **Page text influencing a disposition in the permissive direction.** `field_policy.classify`
   is meant to add caution only. `locale` and `options` now come from the observation.
4. **`semantic_replay.KIND_DISPOSITIONS`.** 37 kinds mapped by hand from label text. The
   mapping of `location.us_resident` to `current_country_of_residence` and
   `employment.prior_affiliate` to a separate canonical family are judgement calls worth
   a second reading.
5. **`ERROR_CODES` completeness.** A closed vocabulary refuses leakage but also refuses real
   failures; a worker forced into `unknown_error` too often would lose diagnostic value.

## Known limits, stated rather than discovered

- No worker exists. `fill_core` still writes an action package nothing reads. The liability
  entry remains open and narrows only at rollout stage 7.
- The replay proves field-combination handling, not that any live ATS DOM can be filled.
- `final_action_activations` is a test oracle on a page we generate. The live equivalent is a
  scoped guard and is not built.
- Task 4 (the browser worker) was deliberately not started. It is the highest-risk item in
  the plan and should not land unreviewed.
