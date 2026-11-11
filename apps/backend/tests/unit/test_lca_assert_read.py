import pytest

from tap_platform.lca.definition import validate_definition
from tap_platform.lca.generator import generate_playwright


def read_flow(**arguments):
    return dict(
        schema_version="lca-debug-v1",
        allowed_origins=["https://fixture.example"],
        targets=[dict(id="field", kind="test_id", value="field")],
        parameters=[],
        steps=[
            dict(
                id="read",
                operation="assert_read",
                target_id="field",
                arguments=arguments,
                timeout_ms=300,
            )
        ],
    )


def test_read_contract_and_deterministic_generation():
    definition = validate_definition(
        read_flow(
            attribute="text",
            name="quote",
            positive=True,
            nonempty=True,
            pattern=r"^USD \d+",
            contains="USD",
        )
    )
    generated = generate_playwright(definition)
    assert generated == generate_playwright(definition)
    assert "readAssertion" in generated.source
    assert '"quote"' in generated.source


@pytest.mark.parametrize(
    "args",
    [
        {"attribute": "text"},
        {"attribute": {"parameter": "x"}, "nonempty": True},
        {"attribute": "text", "positive": False},
        {"attribute": "text", "nonempty": "true"},
        {"attribute": "onclick", "nonempty": True},
        {"attribute": "text", "equals": {"parameter": "x"}},
        {"attribute": "text", "pattern": "(a|aa)+$"},
        {"attribute": "text", "pattern": "^a+a+$"},
        {"attribute": "text", "pattern": "a+$"},
        {"attribute": "text", "pattern": r"^(a+)\1$"},
        {"attribute": "text", "pattern": "^a{1000000}$"},
        {"attribute": "text", "pattern": "["},
        {"attribute": "text", "pattern": r"^a\-b$"},
        {"attribute": "text", "equals": "ok", "unsupported": True},
    ],
)
def test_invalid_constraints_fail_closed(args):
    with pytest.raises(ValueError):
        validate_definition(read_flow(**args))
