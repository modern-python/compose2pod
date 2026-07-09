import os
import subprocess

import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.shell import to_shell, variable_names


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

    def test_all_operator_forms_escape_arg_text(self) -> None:
        assert to_shell("${A:-x`y}") == '"${A:-x\\`y}"'
        assert to_shell("${A-x`y}") == '"${A-x\\`y}"'
        assert to_shell("${A:?x`y}") == '"${A:?x\\`y}"'
        assert to_shell("${A?x`y}") == '"${A?x\\`y}"'
        assert to_shell("${A:+x`y}") == '"${A:+x\\`y}"'
        assert to_shell("${A+x`y}") == '"${A+x\\`y}"'

    def test_malformed_braced_reference_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"malformed variable reference: \$\{FOO!bar\}"):
            to_shell("${FOO!bar}")

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


class TestVariableNames:
    def test_collects_names_excluding_escaped(self) -> None:
        assert variable_names("A=$FOO ${BAR} $$X ${BAZ:-d}") == {"FOO", "BAR", "BAZ"}

    def test_empty_when_no_refs(self) -> None:
        assert variable_names("plain") == set()
