import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.interpolate import interpolate


class TestInterpolate:
    def test_bare_dollar_var_substitutes(self) -> None:
        document, warnings = interpolate({"k": "$FOO"}, {"FOO": "bar"})
        assert document == {"k": "bar"}
        assert warnings == []

    def test_braced_var_substitutes(self) -> None:
        document, warnings = interpolate({"k": "${FOO}"}, {"FOO": "bar"})
        assert document == {"k": "bar"}
        assert warnings == []

    def test_unset_var_defaults_to_blank_and_warns(self) -> None:
        document, warnings = interpolate({"k": "${FOO}"}, {})
        assert document == {"k": ""}
        assert "FOO" in warnings[0]
        assert "not set" in warnings[0]

    def test_dollar_dollar_is_literal_dollar(self) -> None:
        document, warnings = interpolate({"k": "$$FOO"}, {"FOO": "bar"})
        assert document == {"k": "$FOO"}
        assert warnings == []

    def test_colon_dash_uses_default_when_unset(self) -> None:
        document, _ = interpolate({"k": "${FOO:-fallback}"}, {})
        assert document == {"k": "fallback"}

    def test_colon_dash_uses_default_when_empty(self) -> None:
        document, _ = interpolate({"k": "${FOO:-fallback}"}, {"FOO": ""})
        assert document == {"k": "fallback"}

    def test_colon_dash_uses_value_when_set_and_nonempty(self) -> None:
        document, _ = interpolate({"k": "${FOO:-fallback}"}, {"FOO": "bar"})
        assert document == {"k": "bar"}

    def test_dash_uses_default_only_when_unset(self) -> None:
        document, _ = interpolate({"k": "${FOO-fallback}"}, {})
        assert document == {"k": "fallback"}

    def test_dash_keeps_empty_value_when_set_empty(self) -> None:
        document, _ = interpolate({"k": "${FOO-fallback}"}, {"FOO": ""})
        assert document == {"k": ""}

    def test_colon_question_raises_when_unset(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="FOO is required"):
            interpolate({"k": "${FOO:?FOO is required}"}, {})

    def test_colon_question_raises_when_empty(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="FOO is required"):
            interpolate({"k": "${FOO:?FOO is required}"}, {"FOO": ""})

    def test_colon_question_passes_when_set_and_nonempty(self) -> None:
        document, _ = interpolate({"k": "${FOO:?FOO is required}"}, {"FOO": "bar"})
        assert document == {"k": "bar"}

    def test_question_raises_only_when_unset(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="FOO is required"):
            interpolate({"k": "${FOO?FOO is required}"}, {})

    def test_question_passes_when_set_empty(self) -> None:
        document, _ = interpolate({"k": "${FOO?FOO is required}"}, {"FOO": ""})
        assert document == {"k": ""}

    def test_colon_plus_substitutes_alt_when_set_and_nonempty(self) -> None:
        document, _ = interpolate({"k": "${FOO:+alt}"}, {"FOO": "bar"})
        assert document == {"k": "alt"}

    def test_colon_plus_blank_when_unset_or_empty(self) -> None:
        assert interpolate({"k": "${FOO:+alt}"}, {})[0] == {"k": ""}
        assert interpolate({"k": "${FOO:+alt}"}, {"FOO": ""})[0] == {"k": ""}

    def test_plus_substitutes_alt_when_set_even_empty(self) -> None:
        document, _ = interpolate({"k": "${FOO+alt}"}, {"FOO": ""})
        assert document == {"k": "alt"}

    def test_plus_blank_when_unset(self) -> None:
        document, _ = interpolate({"k": "${FOO+alt}"}, {})
        assert document == {"k": ""}

    def test_recurses_through_nested_dict_and_list(self) -> None:
        document, _ = interpolate(
            {"services": {"app": {"environment": ["A=$FOO", "B=${BAR}"]}}},
            {"FOO": "1", "BAR": "2"},
        )
        assert document == {"services": {"app": {"environment": ["A=1", "B=2"]}}}

    def test_keys_are_left_untouched(self) -> None:
        document, _ = interpolate({"$FOO": "literal"}, {"FOO": "bar"})
        assert document == {"$FOO": "literal"}

    def test_non_string_leaves_pass_through(self) -> None:
        document, _ = interpolate({"k": 1, "n": None, "b": True}, {})
        assert document == {"k": 1, "n": None, "b": True}

    def test_multiple_variables_in_one_string(self) -> None:
        document, _ = interpolate({"k": "$FOO-${BAR}"}, {"FOO": "a", "BAR": "b"})
        assert document == {"k": "a-b"}
