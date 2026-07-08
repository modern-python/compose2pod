import os
import subprocess

from compose2pod.shell import referenced_variables, to_shell


def _run_fragment(fragment: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    # Execute the emitted fragment under the same `set -eu` the generated
    # script uses, proving refs expand (or fail) at run time, not generation.
    return subprocess.run(  # noqa: S603 - fixed argv, test-controlled fragment
        ["sh", "-euc", f"printf %s {fragment}"],  # noqa: S607 - sh from PATH is intentional
        env={"PATH": os.environ["PATH"], **env},
        capture_output=True,
        text=True,
        check=False,
    )


class TestToShell:
    def test_plain_literal_is_double_quoted(self) -> None:
        assert to_shell("python:3.12") == '"python:3.12"'

    def test_named_ref_becomes_dash_default(self) -> None:
        assert to_shell("$FOO") == '"${FOO-}"'

    def test_braced_ref_becomes_dash_default(self) -> None:
        assert to_shell("${FOO}") == '"${FOO-}"'

    def test_operator_forms_pass_through(self) -> None:
        assert to_shell("${FOO:-d}") == '"${FOO:-d}"'
        assert to_shell("${FOO-d}") == '"${FOO-d}"'
        assert to_shell("${FOO:?m}") == '"${FOO:?m}"'
        assert to_shell("${FOO?m}") == '"${FOO?m}"'
        assert to_shell("${FOO:+a}") == '"${FOO:+a}"'
        assert to_shell("${FOO+a}") == '"${FOO+a}"'

    def test_dollar_dollar_is_literal_dollar(self) -> None:
        assert to_shell("$$") == '"\\$"'

    def test_specials_in_literal_are_escaped(self) -> None:
        assert to_shell('a"b`c\\d') == '"a\\"b\\`c\\\\d"'

    def test_bare_dollar_not_a_ref_is_escaped(self) -> None:
        assert to_shell("cost $5") == '"cost \\$5"'

    def test_default_text_is_escaped(self) -> None:
        assert to_shell("${FOO:-a`b}") == '"${FOO:-a\\`b}"'

    def test_multiple_refs_and_literals(self) -> None:
        assert to_shell("$A-${B}") == '"${A-}-${B-}"'

    def test_named_ref_expands_at_runtime(self) -> None:
        frag = to_shell("MODEL=${LLM_MODEL}")
        assert _run_fragment(frag, {}).stdout == "MODEL="
        assert _run_fragment(frag, {"LLM_MODEL": "gpt-4"}).stdout == "MODEL=gpt-4"

    def test_required_form_fails_under_shell_when_unset(self) -> None:
        result = _run_fragment(to_shell("${VAR:?must be set}"), {})
        assert result.returncode != 0
        assert "must be set" in result.stderr

    def test_command_substitution_is_inert(self) -> None:
        result = _run_fragment(to_shell("a$(echo pwned)b"), {})
        assert result.returncode == 0
        assert result.stdout == "a$(echo pwned)b"


class TestReferencedVariables:
    def test_collects_sorted_unique_names(self) -> None:
        document = {"services": {"app": {"environment": ["A=$FOO", "B=${BAR}", "C=$FOO"]}}}
        assert referenced_variables(document) == ["BAR", "FOO"]

    def test_excludes_escaped_and_non_string_leaves(self) -> None:
        document = {"k": "$$literal", "n": 1, "b": True, "x": "${GO}"}
        assert referenced_variables(document) == ["GO"]

    def test_empty_when_no_refs(self) -> None:
        assert referenced_variables({"services": {"app": {"image": "x"}}}) == []
