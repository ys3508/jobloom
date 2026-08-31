#!/usr/bin/env python3
"""Bind every approved claim to where a machine actually reads it, one decision at a time.

The audit proposes; nothing it produces is a binding. This turns those proposals into a
`MachineClaimBindingSet` in two phases, because a half-finished review must never be able
to pass for a finished one:

  start / decide   an in-progress session. Resumable, never carries `human_confirmed`.
  finalize         one explicit confirmation against the exact summary hash, written to a
                   sidecar created exclusively.

Three rules the shape enforces:

* The set covers all 38 claims, not only the 14 that needed a person. Leaving the exact
  matches out would strand them as proposals forever; relabelling them `human_confirmed`
  would claim a review that never happened. They carry `deterministic_exact_unique`.
* Nothing is auto-accepted, including the 11 claims with exactly one candidate. A single
  candidate makes the decision fast, not automatic.
* The session binds the BindingSetKey, never the aggregate report key. Writing an identity
  contract later changes the report and must not invalidate a binding already confirmed
  against bytes that did not move.

It reads the packet and writes only its own session and sidecar. It never touches the
registry, the database, a resume, a manifest or the audit's own output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SESSION_SCHEMA_VERSION = "binding-review-session-v1"
BINDING_SET_SCHEMA_VERSION = "machine-claim-binding-set-v1"

DETERMINISTIC = "deterministic_exact_unique"
CONFIRMED_ALTERNATE = "human_confirmed_alternate_render"
CONFIRMED_OCCURRENCES = "human_confirmed_occurrences"
UNRESOLVED = "unresolved"
NEEDS_CONTEXT = "needs_context"
OPEN_STATES = {"pending"}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_private(path: Path, content: str) -> None:
    """Create exclusively at 0600. A session and a binding set are both records."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def replace_private(path: Path, content: str) -> None:
    """Rewrite an in-progress session in place, keeping it private."""
    temporary = path.with_suffix(".tmp")
    if temporary.exists():
        temporary.unlink()
    write_private(temporary, content)
    temporary.replace(path)
    path.chmod(0o600)


# --------------------------------------------------------------------------- packet

def load_report(packet_dir: Path, report_key: str | None = None) -> dict:
    """Read one sealed report out of a review packet, refusing a packet that moved.

    Every hash the packet recorded is rechecked here rather than trusted: the candidate
    spans and offsets a person is about to decide on live in `audit-result.json`, and a
    session that bound a report it never verified would be confirming whatever the file
    happened to say at the time.
    """
    index = json.loads((packet_dir / "audit-packet.json").read_text(encoding="utf-8"))
    references = index["reports"]
    if report_key:
        references = [r for r in references if r["report_key"] == report_key]
    if len(references) != 1:
        raise ValueError(f"expected exactly one report, found {len(references)}; pass --report-key")
    reference = references[0]

    if "audit_result_sha256" not in reference:
        # A packet written before the report was sealed carries no hash to check against,
        # so nothing here can tell whether its result file still says what it said. Its
        # observations remain valid and are not discarded; a fresh packet supersedes it.
        raise ValueError("packet predates report sealing: rerun prepare-review into a new "
                         "directory, and keep this one")
    path = packet_dir / reference["report_path"]
    if sha256_bytes(path.read_bytes()) != reference["audit_result_sha256"]:
        raise ValueError("packet is stale: audit-result.json does not match its sealed hash")
    result = json.loads(path.read_text(encoding="utf-8"))
    for name, entry in result["observation_files"].items():
        if sha256_bytes((packet_dir / entry["path"]).read_bytes()) != entry["sha256"]:
            raise ValueError(f"packet is stale: {name} machine view does not match its hash")
    return {"index": index, "reference": reference, "result": result}


# --------------------------------------------------------------------------- session

def start_session(packet_dir: Path, report_key: str | None, at: str | None = None) -> dict:
    loaded = load_report(packet_dir, report_key)
    result, reference, execution = loaded["result"], loaded["reference"], loaded["index"]["execution"]

    decisions = {}
    for binding in result["bindings"]:
        repeated = binding["occurrence_count"] > 1
        decisions[binding["claim_id"]] = {
            "claim_id": binding["claim_id"],
            "evidence_strength": binding.get("evidence_strength"),
            "kind": "multiple_occurrences" if repeated else "exact_unique",
            # An exact unique match is decided by the bytes, not by a person, and is
            # recorded as such rather than being dressed up as a confirmation.
            "state": "pending" if repeated else "decided",
            "binding_basis": None if repeated else DETERMINISTIC,
            "occurrences": binding["occurrences"],
            "accepted_occurrence_indexes": None if repeated else list(range(len(binding["occurrences"]))),
            "rejected_candidate_block_indexes": [],
        }
    for candidate in result["unresolved_candidates"]:
        decisions[candidate["claim_id"]] = {
            "claim_id": candidate["claim_id"],
            "evidence_strength": candidate.get("evidence_strength"),
            "kind": "alternate_render",
            "state": "pending",
            "binding_basis": None,
            "candidate_block_indexes": candidate["candidate_block_indexes"],
            "accepted_block_index": None,
            "rejected_candidate_block_indexes": [],
        }

    return {
        "schema_version": SESSION_SCHEMA_VERSION,
        "status": "in_progress",
        "started_at": at or now_utc_iso(),
        "packet_dir": str(packet_dir),
        # The BindingSetKey, never the aggregate report key: an identity contract written
        # later changes the report and must leave these bindings standing.
        "binding_set_key": result["binding_set_key"],
        "machine_view_key": result["machine_view_key"],
        "locked_inputs": {
            "pdf_sha256": result["artifact_sha256"],
            "claims_manifest_sha256": result["claims_manifest_sha256"],
            "audit_result_sha256": reference["audit_result_sha256"],
            "raw_view_sha256": result["machine_view"]["raw_sha256"],
            "canonical_view_sha256": result["machine_view"]["canonical_sha256"],
            "extractor_policy_id": execution["extractor_policy_id"],
            "canonicalizer_version": execution["canonicalizer_version"],
            "audit_version": execution["audit_version"],
            "binding_schema_version": result["binding_schema_version"],
        },
        "decisions": decisions,
    }


def verify_session(session: dict, packet_dir: Path) -> None:
    """Recheck the packet on every resume. A session that outlived its evidence is stale."""
    loaded = load_report(packet_dir, None)
    if loaded["reference"]["audit_result_sha256"] != session["locked_inputs"]["audit_result_sha256"]:
        raise ValueError("packet is stale: the audit result changed since this session started")
    if loaded["result"]["binding_set_key"] != session["binding_set_key"]:
        raise ValueError("packet is stale: the binding set key changed since this session started")


def decide(session: dict, claim_id: str, *, accept_block: int | None = None,
           accept_occurrences: list[int] | None = None, reject: bool = False,
           needs_context: bool = False) -> dict:
    decision = session["decisions"].get(claim_id)
    if decision is None:
        raise ValueError(f"no such claim in this session: {claim_id}")
    if decision["kind"] == "exact_unique":
        raise ValueError("an exact unique match is decided by the bytes and takes no decision")

    if needs_context:
        decision.update(state="decided", binding_basis=NEEDS_CONTEXT)
    elif reject:
        # Rejecting leaves the claim unresolved on purpose. It is not a binding, and the
        # artifact stays blocked until the claim is bound or the manifest changes.
        if decision["kind"] == "alternate_render":
            decision["rejected_candidate_block_indexes"] = list(decision["candidate_block_indexes"])
        decision.update(state="decided", binding_basis=UNRESOLVED)
    elif decision["kind"] == "alternate_render":
        if accept_block is None:
            raise ValueError("accepting an alternate render needs a candidate block index")
        if accept_block not in decision["candidate_block_indexes"]:
            raise ValueError(f"block {accept_block} is not a candidate for {claim_id}")
        decision["rejected_candidate_block_indexes"] = [
            b for b in decision["candidate_block_indexes"] if b != accept_block]
        decision.update(state="decided", binding_basis=CONFIRMED_ALTERNATE,
                        accepted_block_index=accept_block)
    else:
        if not accept_occurrences:
            raise ValueError("accepting occurrences needs at least one occurrence index")
        available = range(len(decision["occurrences"]))
        if any(index not in available for index in accept_occurrences):
            raise ValueError(f"occurrence index out of range for {claim_id}")
        decision.update(state="decided", binding_basis=CONFIRMED_OCCURRENCES,
                        accepted_occurrence_indexes=sorted(set(accept_occurrences)))
    return decision


def progress(session: dict) -> dict:
    decisions = session["decisions"].values()
    return {"total": len(session["decisions"]),
            "decided": sum(1 for d in decisions if d["state"] not in OPEN_STATES),
            "pending": sum(1 for d in decisions if d["state"] in OPEN_STATES)}


# --------------------------------------------------------------------------- finalize

def summary(session: dict) -> dict:
    """A body-free account of every claim's outcome, and the thing the user confirms."""
    counts: dict[str, int] = {}
    for decision in session["decisions"].values():
        counts[decision["binding_basis"] or "pending"] = \
            counts.get(decision["binding_basis"] or "pending", 0) + 1
    return {
        "schema_version": BINDING_SET_SCHEMA_VERSION,
        "binding_set_key": session["binding_set_key"],
        "machine_view_key": session["machine_view_key"],
        "locked_inputs": session["locked_inputs"],
        "claims_total": len(session["decisions"]),
        "by_binding_basis": dict(sorted(counts.items())),
        "still_unresolved_claim_ids": sorted(
            d["claim_id"] for d in session["decisions"].values()
            if d["binding_basis"] in {UNRESOLVED, NEEDS_CONTEXT} or d["state"] in OPEN_STATES),
    }


def summary_sha256(session: dict) -> str:
    return sha256_text(json.dumps(summary(session), sort_keys=True, separators=(",", ":")))


def finalize(session: dict, actor: str, expected_summary_sha256: str,
             at: str | None = None) -> dict:
    if actor != "user":
        raise ValueError("only the user may confirm a machine claim binding set")
    if progress(session)["pending"]:
        raise ValueError("every claim must be decided before the set can be confirmed")
    actual = summary_sha256(session)
    if actual != expected_summary_sha256:
        raise ValueError(f"summary hash mismatch: confirmed {expected_summary_sha256}, "
                         f"current {actual}")
    return {**summary(session), "actor": actor, "confirmed_at": at or now_utc_iso(),
            "summary_sha256": actual,
            "bindings": [
                {k: v for k, v in decision.items() if k != "state"}
                for decision in sorted(session["decisions"].values(),
                                       key=lambda d: d["claim_id"])],
            }


# --------------------------------------------------------------------------- cli

def read_session(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)

    start = sub.add_parser("start", help="open an in-progress review session over a packet")
    start.add_argument("--packet", required=True, type=Path)
    start.add_argument("--report-key")
    start.add_argument("--session", required=True, type=Path)

    status = sub.add_parser("status", help="what is decided and what is left")
    status.add_argument("--session", required=True, type=Path)

    choose = sub.add_parser("decide", help="record one decision")
    choose.add_argument("--session", required=True, type=Path)
    choose.add_argument("--claim-id", required=True)
    group = choose.add_mutually_exclusive_group(required=True)
    group.add_argument("--accept-block", type=int)
    group.add_argument("--accept-occurrences", type=int, nargs="+")
    group.add_argument("--reject", action="store_true")
    group.add_argument("--needs-context", action="store_true")

    show = sub.add_parser("summary", help="the body-free summary and its hash")
    show.add_argument("--session", required=True, type=Path)

    done = sub.add_parser("finalize", help="confirm the set against an exact summary hash")
    done.add_argument("--session", required=True, type=Path)
    done.add_argument("--actor", required=True, help="must be `user`")
    done.add_argument("--expected-summary-sha256", required=True)
    done.add_argument("--output", required=True, type=Path)

    args = parser.parse_args()

    if args.mode == "start":
        session = start_session(args.packet, args.report_key)
        write_private(args.session, json.dumps(session, indent=2, ensure_ascii=False))
        print(f"session {args.session} (0600)   binding_set_key={session['binding_set_key'][:16]}")
        print(json.dumps(progress(session), indent=2))
        return

    session = read_session(args.session)

    if args.mode == "status":
        verify_session(session, Path(session["packet_dir"]))
        print(json.dumps(progress(session), indent=2))
        for decision in sorted(session["decisions"].values(), key=lambda d: d["claim_id"]):
            marker = "." if decision["state"] not in OPEN_STATES else "?"
            candidates = decision.get("candidate_block_indexes")
            print(f"  {marker} {decision['claim_id']:22} {decision['kind']:20} "
                  f"{decision['binding_basis'] or 'pending':34} "
                  f"{'candidates=' + str(candidates) if candidates else ''}")
        return

    if args.mode == "decide":
        verify_session(session, Path(session["packet_dir"]))
        decision = decide(session, args.claim_id, accept_block=args.accept_block,
                          accept_occurrences=args.accept_occurrences, reject=args.reject,
                          needs_context=args.needs_context)
        replace_private(args.session, json.dumps(session, indent=2, ensure_ascii=False))
        print(f"{decision['claim_id']}: {decision['binding_basis']}")
        print(json.dumps(progress(session), indent=2))
        return

    if args.mode == "summary":
        print(json.dumps(summary(session), indent=2, ensure_ascii=False))
        print(f"\nsummary_sha256 {summary_sha256(session)}")
        return

    verify_session(session, Path(session["packet_dir"]))
    record = finalize(session, args.actor, args.expected_summary_sha256)
    write_private(args.output, json.dumps(record, indent=2, ensure_ascii=False))
    session["status"] = "confirmed"
    replace_private(args.session, json.dumps(session, indent=2, ensure_ascii=False))
    print(f"confirmed {args.output} (0600)   summary_sha256={record['summary_sha256'][:16]}")


if __name__ == "__main__":
    main()
