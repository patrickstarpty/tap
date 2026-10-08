from tap.modules.graph.domain.vocabulary import NODE_TYPES, RELATION_TYPES, normalize_key


def test_vocabulary_matches_spec():
    assert RELATION_TYPES == frozenset(
        {
            "REQUIRES",
            "APPLIES_TO",
            "PART_OF",
            "EXCEPTION_OF",
            "SUPERSEDES",
            "TRIGGERS",
            "PRECEDES",
            "VALIDATED_BY",
            "RESPONSIBLE_FOR",
            "USES",
            "DEFINES",
            "CONFLICTS_WITH",
            "RELATED_TO",
        }
    )
    assert "PROCESS" in NODE_TYPES and len(NODE_TYPES) == 6


def test_normalize_key_folds_width_case_space_and_punctuation():
    assert normalize_key("核保 流程。") == "核保流程"
    assert normalize_key("Ｈealth-Disclosure Policy") == "healthdisclosurepolicy"
