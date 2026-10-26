"""Recursive-descent SQL parser: parse(sql) -> statement node.

All names (tables, columns) are lower-case; operators in 'bin' are lower-case
words for 'and' / 'or' and the symbols + - * / = != < <= > >= otherwise.

Expression nodes (tuples):
  ('lit', value)                  int, float, str or None (NULL)
  ('col', table_or_None, name)
  ('bin', op, left, right)
  ('not', expr)
  ('neg', expr)
  ('isnull', expr, negated)       negated is True for IS NOT NULL
  ('like', expr, pattern_expr)    NOT LIKE is ('not', ('like', ...))
  ('agg', name, arg_or_None)      name in count sum avg min max; None means COUNT(*)

Statement nodes (tuples):
  ('create', table, [(col, type)])            type is 'INTEGER', 'REAL' or 'TEXT'
  ('insert', table, cols_or_None, [[expr]])
  ('select', items_or_None, table, join, where, group_by, order_by, limit)
      items_or_None: list of expressions, or None for *
      join: None or (table, on_expr)
      group_by: list of expressions; order_by: list of (expr, desc)
      limit: int or None
  ('update', table, [(col, expr)], where)
  ('delete', table, where)
where is an expression or None.
"""

from .errors import Error
from .tokenizer import tokenize

_TYPES = ("INTEGER", "REAL", "TEXT")
_AGGREGATES = frozenset(("count", "sum", "avg", "min", "max"))
_COMPARISONS = frozenset(("=", "!=", "<", "<=", ">", ">="))


class _Parser:
    def __init__(self, sql: str):
        self.tokens = tokenize(sql)
        self.pos = 0

    # --- token helpers ---
    def peek(self) -> tuple:
        return self.tokens[self.pos]

    def next(self) -> tuple:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def at(self, kind: str, value=None) -> bool:
        token = self.peek()
        return token[0] == kind and (value is None or token[1] == value)

    def accept(self, kind: str, value=None) -> bool:
        if self.at(kind, value):
            self.pos += 1
            return True
        return False

    def fail(self, expected: str):
        kind, value, pos = self.peek()
        found = "end of statement" if kind == "eof" else repr(value)
        raise Error(f"syntax error at position {pos}: expected {expected}, found {found}")

    def expect(self, kind: str, value=None):
        if not self.at(kind, value):
            self.fail(value if value is not None else kind)
        return self.next()[1]

    def name(self) -> str:
        return self.expect("id")

    # --- statements ---
    def statement(self) -> tuple:
        if self.accept("kw", "CREATE"):
            result = self.create()
        elif self.accept("kw", "INSERT"):
            result = self.insert()
        elif self.accept("kw", "SELECT"):
            result = self.select()
        elif self.accept("kw", "UPDATE"):
            result = self.update()
        elif self.accept("kw", "DELETE"):
            result = self.delete()
        else:
            self.fail("CREATE, INSERT, SELECT, UPDATE or DELETE")
        self.accept("punct", ";")
        if not self.at("eof"):
            self.fail("end of statement")
        return result

    def comma_list(self, parse_item) -> list:
        items = [parse_item()]
        while self.accept("punct", ","):
            items.append(parse_item())
        return items

    def create(self) -> tuple:
        self.expect("kw", "TABLE")
        table = self.name()
        self.expect("punct", "(")
        columns = self.comma_list(self.column_def)
        self.expect("punct", ")")
        return ("create", table, columns)

    def column_def(self) -> tuple:
        col = self.name()
        type_name = self.peek()[1].upper() if self.at("id") else None
        if type_name not in _TYPES:
            self.fail("a column type (INTEGER, REAL or TEXT)")
        self.next()
        return (col, type_name)

    def insert(self) -> tuple:
        self.expect("kw", "INTO")
        table = self.name()
        columns = None
        if self.accept("punct", "("):
            columns = self.comma_list(self.name)
            self.expect("punct", ")")
        self.expect("kw", "VALUES")
        rows = self.comma_list(self.value_row)
        return ("insert", table, columns, rows)

    def value_row(self) -> list:
        self.expect("punct", "(")
        row = self.comma_list(self.expr)
        self.expect("punct", ")")
        return row

    def select(self) -> tuple:
        items = None
        if not self.accept("op", "*"):
            items = self.comma_list(self.expr)
        self.expect("kw", "FROM")
        table = self.name()
        join = None
        if self.at("kw", "INNER") or self.at("kw", "JOIN"):
            self.accept("kw", "INNER")
            self.expect("kw", "JOIN")
            join_table = self.name()
            self.expect("kw", "ON")
            join = (join_table, self.expr())
        where = self.where()
        group_by = []
        if self.accept("kw", "GROUP"):
            self.expect("kw", "BY")
            group_by = self.comma_list(self.expr)
        order_by = []
        if self.accept("kw", "ORDER"):
            self.expect("kw", "BY")
            order_by = self.comma_list(self.order_item)
        limit = None
        if self.accept("kw", "LIMIT"):
            limit = self.expect("num")
            if isinstance(limit, float):
                raise Error("LIMIT must be an integer")
        return ("select", items, table, join, where, group_by, order_by, limit)

    def order_item(self) -> tuple:
        expr = self.expr()
        desc = self.accept("kw", "DESC")
        if not desc:
            self.accept("kw", "ASC")
        return (expr, desc)

    def where(self):
        return self.expr() if self.accept("kw", "WHERE") else None

    def update(self) -> tuple:
        table = self.name()
        self.expect("kw", "SET")
        assignments = self.comma_list(self.assignment)
        return ("update", table, assignments, self.where())

    def assignment(self) -> tuple:
        col = self.name()
        self.expect("op", "=")
        return (col, self.expr())

    def delete(self) -> tuple:
        self.expect("kw", "FROM")
        table = self.name()
        return ("delete", table, self.where())

    # --- expressions, lowest precedence first ---
    def expr(self) -> tuple:
        left = self.and_expr()
        while self.accept("kw", "OR"):
            left = ("bin", "or", left, self.and_expr())
        return left

    def and_expr(self) -> tuple:
        left = self.not_expr()
        while self.accept("kw", "AND"):
            left = ("bin", "and", left, self.not_expr())
        return left

    def not_expr(self) -> tuple:
        if self.accept("kw", "NOT"):
            return ("not", self.not_expr())
        return self.comparison()

    def comparison(self) -> tuple:
        left = self.additive()
        while True:
            kind, value, _ = self.peek()
            if kind == "op" and value in _COMPARISONS:
                self.next()
                left = ("bin", value, left, self.additive())
            elif self.accept("kw", "IS"):
                negated = self.accept("kw", "NOT")
                self.expect("kw", "NULL")
                left = ("isnull", left, negated)
            elif self.at("kw", "LIKE"):
                self.next()
                left = ("like", left, self.additive())
            elif self.at("kw", "NOT") and self.tokens[self.pos + 1][:2] == ("kw", "LIKE"):
                self.pos += 2
                left = ("not", ("like", left, self.additive()))
            else:
                return left

    def additive(self) -> tuple:
        left = self.term()
        while self.at("op", "+") or self.at("op", "-"):
            op = self.next()[1]
            left = ("bin", op, left, self.term())
        return left

    def term(self) -> tuple:
        left = self.unary()
        while self.at("op", "*") or self.at("op", "/"):
            op = self.next()[1]
            left = ("bin", op, left, self.unary())
        return left

    def unary(self) -> tuple:
        if self.accept("op", "-"):
            return ("neg", self.unary())
        if self.accept("op", "+"):
            return self.unary()
        return self.primary()

    def primary(self) -> tuple:
        kind, value, _ = self.peek()
        if kind in ("num", "str"):
            self.next()
            return ("lit", value)
        if self.accept("kw", "NULL"):
            return ("lit", None)
        if self.accept("punct", "("):
            inner = self.expr()
            self.expect("punct", ")")
            return inner
        if kind == "id":
            self.next()
            if value in _AGGREGATES and self.at("punct", "("):
                return self.aggregate(value)
            if self.accept("punct", "."):
                return ("col", value, self.name())
            return ("col", None, value)
        self.fail("an expression")

    def aggregate(self, name: str) -> tuple:
        self.expect("punct", "(")
        if self.accept("op", "*"):
            if name != "count":
                raise Error(f"{name.upper()}(*) is not allowed; only COUNT(*)")
            arg = None
        else:
            arg = self.expr()
        self.expect("punct", ")")
        return ("agg", name, arg)


def parse(sql: str) -> tuple:
    """Parse one SQL statement into an AST node; raise Error on bad syntax."""
    return _Parser(sql).statement()
