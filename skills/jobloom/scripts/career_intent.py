"""Career-goal validation and deterministic intent matching."""

from __future__ import annotations

from typing import Any


LIST_KEYS = {"desired_roles", "desired_industries", "skills_to_build", "avoid_roles", "avoid_industries"}


def validate(goals: dict[str, Any] | None) -> dict[str, Any] | None:
    if goals is None:
        return None
    if not isinstance(goals, dict) or not LIST_KEYS <= set(goals):
        raise ValueError("career goals are incomplete")
    result = dict(goals)
    for key in LIST_KEYS:
        if not isinstance(goals[key], list) or any(not isinstance(x, str) or not x.strip() for x in goals[key]):
            raise ValueError(f"career goals {key} must be a string list")
        result[key] = [x.strip() for x in goals[key]]
    return result if any(result[key] for key in LIST_KEYS) else None


def score(function: dict[str, Any], goals: dict[str, Any] | None) -> tuple[int | None, bool]:
    if goals is None:
        return None, False
    space = {function["canonical_label"].casefold(), function["role_family"].casefold(),
             function["function_id"].casefold()}
    desired = [x.casefold() for x in goals["desired_roles"] + goals["desired_industries"]]
    avoided = [x.casefold() for x in goals["avoid_roles"] + goals["avoid_industries"]]
    excluded = any(x in space for x in avoided)
    if excluded:
        return 0, True
    if not desired:
        return None, False
    return round(100 * sum(x in space for x in desired) / len(desired)), False
