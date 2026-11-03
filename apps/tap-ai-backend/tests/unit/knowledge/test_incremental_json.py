from __future__ import annotations

import json
import random

import pytest

from tap.modules.knowledge.application.incremental_json import IncrementalJsonField

DOCUMENT = '{"answer":"a\\nb \\u4e2d","claims":[]}'


def feed_all(field: IncrementalJsonField, chunks: list[str]) -> str:
    return "".join(field.feed(chunk) for chunk in chunks)


def test_character_by_character_decodes_escapes():
    assert feed_all(IncrementalJsonField("answer"), list(DOCUMENT)) == "a\nb 中"


def test_any_random_split_yields_the_decoded_value():
    rng = random.Random(20261103)
    for _ in range(50):
        cuts = sorted(rng.sample(range(1, len(DOCUMENT)), rng.randint(1, len(DOCUMENT) - 2)))
        chunks = [DOCUMENT[start:end] for start, end in zip([0, *cuts], [*cuts, len(DOCUMENT)])]
        assert feed_all(IncrementalJsonField("answer"), chunks) == "a\nb 中"


def test_field_need_not_be_the_first_property():
    document = '{"claims":[{"text":"x"}],"answer":"y"}'
    assert feed_all(IncrementalJsonField("answer"), list(document)) == "y"


def test_nested_keys_with_the_same_name_are_ignored():
    document = '{"claims":[{"answer":"nested"}],"meta":{"answer":"deep"},"answer":"top"}'
    assert feed_all(IncrementalJsonField("answer"), [document]) == "top"


def test_values_that_look_like_keys_are_ignored():
    document = '{"note":"answer","other":"\\"answer\\"","answer":"real"}'
    assert feed_all(IncrementalJsonField("answer"), list(document)) == "real"


@pytest.mark.parametrize("split", range(1, 30))
def test_surrogate_pair_split_across_chunks(split):
    document = '{"answer":"hi \\ud83d\\ude00!"}'
    field = IncrementalJsonField("answer")
    text = feed_all(field, [document[:split], document[split:]])
    assert text == json.loads(document)["answer"] == "hi 😀!"


def test_returns_empty_after_the_field_ends_and_for_non_string_values():
    field = IncrementalJsonField("answer")
    assert field.feed('{"answer":"done"') == "done"
    assert field.feed(',"other":"more"}') == ""
    assert feed_all(IncrementalJsonField("answer"), ['{"answer":null,"x":"y"}']) == ""


def test_whitespace_between_tokens_is_tolerated():
    document = '{ "claims" : [ ] ,\n  "answer" :  "spaced \\"quote\\"" }'
    assert feed_all(IncrementalJsonField("answer"), list(document)) == 'spaced "quote"'
