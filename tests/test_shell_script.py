"""The script parser: the supported subset of sh, quoting, adjacency, and every refusal."""

from __future__ import annotations

import pytest

from core.bm_cli.shell_lexer import (
    STEER_BACKGROUND,
    STEER_BRACES,
    STEER_DUP,
    STEER_FD,
    STEER_GROUP,
    STEER_HEREDOC,
    STEER_PROCESS_SUB,
    STEER_SUBSHELL,
    STEER_SUBSTITUTION,
    STEER_VARIABLE,
)
from core.bm_cli.shell_script import (
    Redirect,
    ShellQuoteError,
    ShellSyntaxError,
    SimpleCommand,
    Word,
    is_compound,
    parse_shell_script,
    segment_raw,
)


def _argvs(text: str) -> list[list[str]]:
    return [[word.text for word in command.argv] for command in parse_shell_script(text).commands()]


def _connectors(text: str) -> list[str | None]:
    return [connector for _pipeline, connector in parse_shell_script(text).items]


def test_pipes_join_commands_into_one_pipeline() -> None:
    script = parse_shell_script("git ls-files | grep py | wc -l")

    assert len(script.items) == 1
    (pipeline, connector), = script.items
    assert connector is None
    assert [[w.text for w in c.argv] for c in pipeline.commands] == [
        ["git", "ls-files"], ["grep", "py"], ["wc", "-l"],
    ]


def test_connectors_are_kept_in_order() -> None:
    assert _connectors("a && b || c ; d") == ["&&", "||", ";", None]
    assert _argvs("a && b || c ; d") == [["a"], ["b"], ["c"], ["d"]]


def test_newlines_separate_like_semicolons_and_trailing_separators_are_dropped() -> None:
    assert _connectors("a\nb\n") == [";", None]
    assert _connectors("a;") == [None]
    assert _connectors("a &&\n b") == ["&&", None]


def test_adjacent_2_and_1_is_a_dup_but_spaced_is_refused() -> None:
    (command,) = parse_shell_script("make 2>&1").commands()
    assert command.redirects == (Redirect(2, "dup_out", None),)
    assert [w.text for w in command.argv] == ["make"]

    with pytest.raises(ShellSyntaxError) as spaced:
        parse_shell_script("make 2 >&1")
    assert spaced.value.steer == STEER_DUP


def test_a_spaced_fd_digit_is_an_argument() -> None:
    (command,) = parse_shell_script("seq 2 > out.txt").commands()
    assert [w.text for w in command.argv] == ["seq", "2"]
    assert command.redirects == (Redirect(1, "write", Word("out.txt", False, "out.txt")),)


@pytest.mark.parametrize(
    ("text", "redirect"),
    [
        ("cmd > f", Redirect(1, "write", Word("f", False, "f"))),
        ("cmd 1>> f", Redirect(1, "append", Word("f", False, "f"))),
        ("cmd 2> f", Redirect(2, "write", Word("f", False, "f"))),
        ("cmd < f", Redirect(0, "read", Word("f", False, "f"))),
        ("cmd &> f", Redirect("both", "write", Word("f", False, "f"))),
        ("cmd >f", Redirect(1, "write", Word("f", False, "f"))),
    ],
)
def test_redirect_forms(text: str, redirect: Redirect) -> None:
    (command,) = parse_shell_script(text).commands()
    assert command.redirects == (redirect,)
    assert [w.text for w in command.argv] == ["cmd"]


def test_escaped_semicolon_in_find_exec_is_a_word() -> None:
    assert _argvs("find . -name '*.pyc' -exec rm {} \\;") == [
        ["find", ".", "-name", "*.pyc", "-exec", "rm", "{}", ";"],
    ]
    assert not is_compound("find . -name '*.pyc' -exec rm {} \\;")


def test_quoted_operators_are_literals() -> None:
    assert _argvs("grep 'a|b' notes.txt") == [["grep", "a|b", "notes.txt"]]
    assert _argvs('grep "x && y; z > w" f') == [["grep", "x && y; z > w", "f"]]
    assert not is_compound("grep 'a|b' notes.txt")


def test_only_unquoted_glob_characters_glob() -> None:
    (command,) = parse_shell_script("ls '*.py' *.py \\*.md \"a?\"b*").commands()
    flags = [(word.text, word.glob) for word in command.argv]
    assert flags == [("ls", False), ("*.py", False), ("*.py", True), ("*.md", False), ("a?b*", True)]
    # The quoted ``?`` is escaped in the pattern; the unquoted ``*`` is not.
    assert command.argv[-1].pattern == "a[?]b*"


def test_leading_assignments_are_assignments_and_later_ones_are_words() -> None:
    (command,) = parse_shell_script("FOO=1 BAR='x y' make CC=gcc").commands()
    assert command.assignments == (("FOO", "1"), ("BAR", "x y"))
    assert [w.text for w in command.argv] == ["make", "CC=gcc"]


def test_a_quoted_name_is_not_an_assignment() -> None:
    (command,) = parse_shell_script("'FOO=1' ls").commands()
    assert command.assignments == ()
    assert [w.text for w in command.argv] == ["FOO=1", "ls"]


def test_dollar_without_a_name_and_single_quoted_variables_are_literals() -> None:
    assert _argvs("awk '{print $1}' f") == [["awk", "{print $1}", "f"]]
    assert _argvs('printf "costs $" 5$') == [["printf", "costs $", "5$"]]


@pytest.mark.parametrize(
    ("text", "steer"),
    [
        ("echo $(id)", STEER_SUBSTITUTION),
        ('echo "$(id)"', STEER_SUBSTITUTION),
        ("echo `id`", STEER_SUBSTITUTION),
        ('echo "`id`"', STEER_SUBSTITUTION),
        ("echo $HOME", STEER_VARIABLE),
        ("echo ${HOME}", STEER_VARIABLE),
        ('echo "$HOME"', STEER_VARIABLE),
        ("echo $?", STEER_VARIABLE),
        ("sleep 9 &", STEER_BACKGROUND),
        ("sleep 9 & ls", STEER_BACKGROUND),
        ("(cd x && ls)", STEER_SUBSHELL),
        ("{ ls; }", STEER_GROUP),
        ("cat <<EOF", STEER_HEREDOC),
        ("diff <(ls a) b", STEER_PROCESS_SUB),
        ("tee >(wc)", STEER_PROCESS_SUB),
        ("ls 3> f", STEER_FD),
        ("ls 1>&2", STEER_DUP),
        ("ls >&2", STEER_DUP),
        ("mkdir src/{a,b}", STEER_BRACES),
        ("echo {1..3}", STEER_BRACES),
    ],
)
def test_unsupported_constructs_are_refused_with_their_steer(text: str, steer: str) -> None:
    with pytest.raises(ShellSyntaxError) as refused:
        parse_shell_script(text)
    assert refused.value.steer == steer
    assert str(refused.value) == steer
    assert is_compound(text), "a refused construct must reach the script path for its steer"


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("", "empty"),
        ("a &&", "nothing follows &&"),
        ("a ||", "nothing follows ||"),
        ("a |", "nothing follows |"),
        ("| a", "a command is missing before '|'"),
        ("&& a", "a command is missing before '&&'"),
        ("a ; ; b", "a command is missing before ';'"),
        ("a ;; b", ";; is not supported"),
        ("ls >", "> needs a file name"),
        ("ls > && b", "> needs a file name"),
        ("FOO=1", "a command is missing"),
        ("ls > *.txt", "one plain name"),
        ("ls >| f", ">| is not supported"),
        ("ls |& wc", "|& is not supported"),
        ("ls &>> f", "&>> is not supported"),
        ("cat 0< f", "only < file"),
    ],
)
def test_empty_and_dangling_forms_are_refused(text: str, fragment: str) -> None:
    with pytest.raises(ShellSyntaxError) as refused:
        parse_shell_script(text)
    assert fragment in refused.value.steer


@pytest.mark.parametrize("text", ["echo 'open", 'echo "open', "echo trailing\\"])
def test_unterminated_quotes_are_quote_errors_and_keep_the_single_path(text: str) -> None:
    with pytest.raises(ShellQuoteError):
        parse_shell_script(text)
    assert not is_compound(text)


@pytest.mark.parametrize(
    "text",
    ["ls -la", "git commit -m 'fix: a | b'", "pytest -q tests/test_x.py", "ls\n", "  cat notes.md  ", "", "rr notes.md 1:20"],
)
def test_plain_commands_are_not_compound(text: str) -> None:
    assert not is_compound(text)


@pytest.mark.parametrize(
    "text",
    ["ls | wc -l", "a && b", "a || b", "a; b", "a;", "echo hi > f", "cat < f", "make 2>&1",
     "FOO=1 make", "ls *.py", "a\nb"],
)
def test_compound_commands_are_compound(text: str) -> None:
    assert is_compound(text)


def test_segment_raw_is_the_shell_quoted_argv() -> None:
    (command,) = parse_shell_script("FOO=1 grep 'a b' f > out").commands()
    assert segment_raw(command) == "grep 'a b' f"


def test_with_commands_keeps_pipelines_and_connectors() -> None:
    script = parse_shell_script("a | b && c")
    replaced = script.with_commands([
        SimpleCommand((), (Word(name, False, name),), ()) for name in ("x", "y", "z")
    ])
    assert [[w.text for w in c.argv] for c in replaced.commands()] == [["x"], ["y"], ["z"]]
    assert [len(p.commands) for p, _c in replaced.items] == [2, 1]
    assert [c for _p, c in replaced.items] == ["&&", None]
    with pytest.raises(ValueError):
        script.with_commands([])
