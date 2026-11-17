import pytest
from kicad_tog.s_expression import tokenize, parse, child, children


# ---- tokenize: characters -> tokens ----

def test_tokenize_simple():
    assert list(tokenize("(a b)")) == ["(", "a", "b", ")"]

def test_tokenize_ignores_whitespace_and_newlines():
    assert list(tokenize("(a\n\t  b )")) == ["(", "a", "b", ")"]

def test_tokenize_keeps_paren_inside_quotes():
    # the real net name from pic_programmer
    assert list(tokenize('(net "Net-(D1-K)")')) == ["(", "net", '"Net-(D1-K)"', ")"]
    # design choice: tokens keep their quotes, so a net literally named "(" can't be
    # confused with a real "(" token. parse() strips them.

def test_tokenize_keeps_escaped_quotes_inside_string():
    text = r'(a "say \"hi\"")'

    assert list(tokenize(text)) == [
        "(", "a", '"say \\"hi\\""', ")"
    ]

def test_tokenize_handles_escaped_backslash():
    text = r'(a "C:\\folder")'

    assert list(tokenize(text)) == [
        "(", "a", '"C:\\\\folder"', ")"
    ]


# ---- parse: tokens -> nested lists ----

@pytest.mark.parametrize("text, expected", [
    ("(a)",                    ["a"]),
    ("(width 0.8)",            ["width", "0.8"]),          # values stay strings
    ("(a (b c) d)",            ["a", ["b", "c"], "d"]),
    ('(a (b "x(y)") 1)',       ["a", ["b", "x(y)"], "1"]),  # quotes stripped here
    ('(net 0 "")',             ["net", "0", ""]),          # empty string, KiCad 9's net 0
    ('(a "say \\"hi\\"")',     ["a", 'say "hi"']),         # escaped quotes
])
def test_parse(text, expected):
    assert parse(text) == expected

def test_parse_real_segment():
    text = """
    (segment
        (start 110.49 124.155)
        (end 110.49 119.38)
        (width 0.8)
        (layer "F.Cu")
        (net "VCC"))
    """
    assert parse(text) == ["segment",
                           ["start", "110.49", "124.155"],
                           ["end", "110.49", "119.38"],
                           ["width", "0.8"],
                           ["layer", "F.Cu"],
                           ["net", "VCC"]]

@pytest.mark.parametrize("bad", ["(a (b)", "(a))", ""])
def test_parse_rejects_unbalanced(bad):
    with pytest.raises(ValueError):
        parse(bad)


# ---- query helpers ----

SEG = ["segment", ["start", "1", "2"], ["width", "0.8"], ["layer", "F.Cu"], ["width", "9"]]

def test_child_returns_first_match():
    assert child(SEG, "width") == ["width", "0.8"]

def test_child_missing_is_none():
    assert child(SEG, "net") is None

def test_children_returns_all_matches_in_order():
    assert children(SEG, "width") == [["width", "0.8"], ["width", "9"]]

def test_helpers_skip_atoms():
    # "segment" is an atom, not a child node: must not crash or match
    assert children(["segment", "segment"], "segment") == []

def test_helpers_skip_empty_lists():
    node = ["segment", [], ["width", "0.8"]]

    assert child(node, "width") == ["width", "0.8"]
    assert children(node, "width") == [["width", "0.8"]]