"""Split a SQL string into tokens.

A token is a tuple (kind, value, position). Kinds:
  'kw'    keyword, value is upper-case ('SELECT')
  'id'    identifier, value is lower-case (SQL names are case-insensitive)
  'num'   number, value is an int or a float
  'str'   string literal, value is the text with '' unescaped
  'op'    + - * / = != < <= > >=   ('<>' is returned as '!=')
  'punct' ( ) , . ;
  'eof'   end of input
"""

from .errors import Error

KEYWORDS = frozenset(
    "CREATE TABLE INSERT INTO VALUES SELECT FROM JOIN INNER ON WHERE GROUP BY "
    "ORDER ASC DESC LIMIT UPDATE SET DELETE AND OR NOT IS NULL LIKE".split()
)
_TWO_CHAR_OPS = {"!=": "!=", "<>": "!=", "<=": "<=", ">=": ">="}
_ONE_CHAR_OPS = frozenset("+-*/=<>")
_PUNCT = frozenset("(),.;")


def _is_word_start(c: str) -> bool:
    return c == "_" or (c.isascii() and c.isalpha())


def _is_word_char(c: str) -> bool:
    return c == "_" or (c.isascii() and c.isalnum())


def tokenize(sql: str) -> list[tuple]:
    tokens = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c.isspace():
            i += 1
        elif _is_word_start(c):
            j = i + 1
            while j < n and _is_word_char(sql[j]):
                j += 1
            word = sql[i:j]
            if word.upper() in KEYWORDS:
                tokens.append(("kw", word.upper(), i))
            else:
                tokens.append(("id", word.lower(), i))
            i = j
        elif c.isascii() and c.isdigit():
            j = i
            while j < n and sql[j].isascii() and sql[j].isdigit():
                j += 1
            is_real = j + 1 < n and sql[j] == "." and sql[j + 1].isascii() and sql[j + 1].isdigit()
            if is_real:
                j += 1
                while j < n and sql[j].isascii() and sql[j].isdigit():
                    j += 1
            if j < n and _is_word_char(sql[j]):
                raise Error(f"malformed number at position {i}")
            text = sql[i:j]
            tokens.append(("num", float(text) if is_real else int(text), i))
            i = j
        elif c == "'":
            start = i
            parts = []
            j = i + 1
            while True:
                end = sql.find("'", j)
                if end < 0:
                    raise Error(f"unterminated string starting at position {i}")
                parts.append(sql[j:end])
                if sql[end + 1:end + 2] == "'":
                    parts.append("'")
                    j = end + 2
                else:
                    i = end + 1
                    break
            tokens.append(("str", "".join(parts), start))
        elif sql[i:i + 2] in _TWO_CHAR_OPS:
            tokens.append(("op", _TWO_CHAR_OPS[sql[i:i + 2]], i))
            i += 2
        elif c in _ONE_CHAR_OPS:
            tokens.append(("op", c, i))
            i += 1
        elif c in _PUNCT:
            tokens.append(("punct", c, i))
            i += 1
        else:
            raise Error(f"unexpected character {c!r} at position {i}")
    tokens.append(("eof", None, n))
    return tokens
