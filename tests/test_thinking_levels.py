"""Thinking levels: the vocabulary and the request shaping, without a database.

A connection carries a map of level → JSON fragment; an agent picks a level per
routed activation; routing deep-merges the picked fragment over the
connection's extra body. ``default`` sends nothing.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from core.llm.thinking import ThinkingConfigError, deep_merge, effective_extra_body, unoffered
from core.models.settings import AIConnectionCreate, AIConnectionUpdate
from core.models.thinking import THINKING_CHOICES, THINKING_LEVELS, parse_thinking_levels

_ZAI = {"off": {"thinking": {"type": "disabled"}}, "high": {"thinking": {"type": "enabled"}}}


# ─── parse_thinking_levels ───


def test_the_vocabulary_is_default_plus_five_levels() -> None:
    assert THINKING_LEVELS == ("off", "low", "medium", "high", "xhigh")
    assert THINKING_CHOICES == ("default", *THINKING_LEVELS)


@pytest.mark.parametrize("value", [_ZAI, json.dumps(_ZAI)])
def test_a_map_or_its_json_text_parses_to_the_same_map(value: object) -> None:
    assert parse_thinking_levels(value) == _ZAI


@pytest.mark.parametrize("value", [None, {}, "{}"])
def test_none_and_an_empty_map_mean_no_levels(value: object) -> None:
    assert parse_thinking_levels(value) is None


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("not json", "not valid JSON"),
        ([], "must be an object"),
        ("[1]", "must be an object"),
        ({"none": {"a": 1}}, "unknown level 'none'"),
        ({"default": {"a": 1}}, "unknown level 'default'"),
        ({"high": {}}, "non-empty JSON object"),
        ({"high": "fast"}, "non-empty JSON object"),
        ({"high": [1]}, "non-empty JSON object"),
    ],
)
def test_a_malformed_map_is_refused(value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_thinking_levels(value)


def test_the_connection_models_reject_a_malformed_map_as_a_validation_error() -> None:
    with pytest.raises(ValidationError, match="unknown level"):
        AIConnectionCreate(name="x", api_base_url="http://h/v1", thinking_levels={"max": {"a": 1}})
    with pytest.raises(ValidationError, match="unknown level"):
        AIConnectionUpdate(thinking_levels={"max": {"a": 1}})


def test_an_update_keeps_an_empty_map_so_it_can_clear_the_column() -> None:
    assert AIConnectionUpdate(thinking_levels={}).model_dump(exclude_none=True) == {"thinking_levels": {}}
    assert AIConnectionUpdate().model_dump(exclude_none=True) == {}
    assert AIConnectionCreate(name="x", api_base_url="http://h/v1", thinking_levels={}).thinking_levels is None


# ─── deep_merge ───


def test_deep_merge_merges_nested_objects_and_the_fragment_wins_leaves() -> None:
    base = {"stream": False, "thinking": {"type": "enabled", "budget": 10}, "keep": [1]}
    fragment = {"thinking": {"type": "disabled"}, "keep": [2], "new": True}
    assert deep_merge(base, fragment) == {
        "stream": False,
        "thinking": {"type": "disabled", "budget": 10},
        "keep": [2],
        "new": True,
    }


def test_deep_merge_mutates_neither_input() -> None:
    base = {"thinking": {"type": "enabled"}}
    fragment = {"thinking": {"type": "disabled"}}
    merged = deep_merge(base, fragment)
    merged["thinking"]["type"] = "changed"
    assert base == {"thinking": {"type": "enabled"}}
    assert fragment == {"thinking": {"type": "disabled"}}


def test_a_fragment_replaces_a_non_object_with_an_object() -> None:
    assert deep_merge({"thinking": "on"}, {"thinking": {"type": "x"}}) == {"thinking": {"type": "x"}}


# ─── effective_extra_body ───


@pytest.mark.parametrize("extra_body", [None, '{"stream": false}', "not even json"])
def test_default_passes_the_extra_body_through_unchanged(extra_body: str | None) -> None:
    assert effective_extra_body(extra_body, _ZAI, "default") == extra_body


def test_a_level_merges_over_the_extra_body() -> None:
    body = effective_extra_body('{"stream": false}', _ZAI, "off")
    assert json.loads(body) == {"stream": False, "thinking": {"type": "disabled"}}


def test_a_level_over_no_extra_body_is_the_fragment_alone() -> None:
    assert json.loads(effective_extra_body(None, _ZAI, "high")) == {"thinking": {"type": "enabled"}}


def test_an_unoffered_level_raises() -> None:
    with pytest.raises(ThinkingConfigError, match="'low' is not offered"):
        effective_extra_body(None, _ZAI, "low")
    with pytest.raises(ThinkingConfigError, match="'off' is not offered"):
        effective_extra_body(None, None, "off")


@pytest.mark.parametrize("extra_body", ["not json", "[1, 2]", '"text"'])
def test_an_extra_body_that_is_not_a_json_object_raises(extra_body: str) -> None:
    with pytest.raises(ThinkingConfigError, match="extra body"):
        effective_extra_body(extra_body, _ZAI, "high")


# ─── unoffered ───


def test_unoffered_lists_each_non_default_choice_the_map_lacks() -> None:
    choices = {"thinking_social": "low", "thinking_work": "high"}
    assert unoffered(_ZAI, choices) == ["thinking_social: low"]
    assert unoffered(None, choices) == ["thinking_social: low", "thinking_work: high"]
    assert unoffered(None, {"thinking_social": "default", "thinking_work": "default"}) == []
