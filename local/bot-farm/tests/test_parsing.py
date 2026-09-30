import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.main_handlers import _TOKEN_RE, _parse_line  # noqa: E402

VALID_TOKEN = "123456789:AAExampleTokenTokenTokenToken1"


def test_parse_line_token_only():
    assert _parse_line(VALID_TOKEN) == (VALID_TOKEN, "")


def test_parse_line_with_pipe_text():
    line = f"{VALID_TOKEN} | Привет!"
    assert _parse_line(line) == (VALID_TOKEN, "Привет!")


def test_parse_line_with_space_text():
    line = f"{VALID_TOKEN} Привет мир"
    assert _parse_line(line) == (VALID_TOKEN, "Привет мир")


def test_parse_line_skips_blank_and_comments():
    assert _parse_line("") is None
    assert _parse_line("   ") is None
    assert _parse_line("# comment") is None


def test_token_regex_matches_valid():
    assert _TOKEN_RE.match(VALID_TOKEN)


def test_token_regex_rejects_garbage():
    assert not _TOKEN_RE.match("not-a-token")
    assert not _TOKEN_RE.match("123:short")
