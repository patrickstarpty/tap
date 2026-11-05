from __future__ import annotations

from tap.modules.graph.application.alias_index import AliasIndex
from tap.modules.graph.domain.project import Alias


def test_alias_longest_match_prefers_longer_alias():
    index = AliasIndex.build(
        [Alias("健康告知", "n-short", "LABEL"), Alias("健康告知书", "n-long", "LABEL")]
    )
    assert [m.node_id for m in index.match("健康告知书需要什么？")] == ["n-long"]
    assert len(index) == 2


def test_alias_match_masks_used_spans_and_dedupes_nodes():
    index = AliasIndex.build(
        [
            Alias("核保流程", "n-underwriting", "LABEL"),
            Alias("理赔流程", "n-claims", "LABEL"),
        ]
    )
    matches = index.match("核保流程之后是理赔流程，核保流程由谁负责")
    assert [m.node_id for m in matches] == ["n-underwriting", "n-claims"]


def test_alias_match_ignores_width_case_and_punctuation():
    index = AliasIndex.build([Alias("healthdisclosure", "n-health", "LABEL")])
    matches = index.match("Ｈealth-Disclosure")
    assert [m.node_id for m in matches] == ["n-health"]
    assert matches[0].alias_norm == "healthdisclosure"
