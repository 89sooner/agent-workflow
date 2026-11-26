"""Compatibility entry points for the original Katakuri offline goal contract."""
from . import goals
from .core import WorkflowError
from .goal_policy import DIRECTIVE, HANDOFF, KATAKURI_SCOPE as SCOPE

# These public helpers use the same state machine and result schemas as before.
from .goals import (
    MAX_GOAL_INPUT_BYTES, MAX_INPUT_BYTES, PLAN_SCHEMA, STEP_SCHEMA, evidence_packet,
    model_budget, prompt, read_coverage, review_schema, token_usage, validate_plan, validate_step,
)


def supported(config):
    return goals.supported(config, task_kind="katakuri")


def create(store, ident, profile, objective, criteria, groups=None, token_limit=None, *,
           instrument=None, milestone=None, recommendation_id=None):
    return goals._create(store, ident, profile, objective, criteria, groups, token_limit, task_kind="katakuri",
                         instrument=instrument, milestone=milestone, recommendation_id=recommendation_id)


def contract(task):
    if goals.kind(task) != "katakuri":
        raise WorkflowError("katakuri-goal로 등록된 작업이 필요합니다.")
    return goals.contract(task)


def status(task):
    contract(task)
    return goals.status(task)


def run(store, ident, timeout):
    contract(store.get(ident))
    return goals.run(store, ident, timeout)


def supply(store, ident, criterion, path):
    contract(store.get(ident))
    return goals.supply(store, ident, criterion, path)


def supply_text(store, ident, criterion, content):
    contract(store.get(ident))
    return goals.supply_text(store, ident, criterion, content)


def budget(store, ident, calls, seconds, tokens, clear_token_limit=False):
    contract(store.get(ident))
    return goals.budget(store, ident, calls, seconds, tokens, clear_token_limit)
