"""Table storage, expression evaluation, query execution and JSON persistence."""

import json
import os
import re

from .errors import Error
from .parser import parse

_TYPES = ("INTEGER", "REAL", "TEXT")
_NUMBER = re.compile(r"\s*[-+]?(\d+\.?\d*([eE][-+]?\d+)?|\.\d+([eE][-+]?\d+)?)\s*$")
_NUMBER_PREFIX = re.compile(r"\s*[-+]?(\d+\.?\d*([eE][-+]?\d+)?|\.\d+([eE][-+]?\d+)?)")


def _parse_number(text):
    """Return the int or float a whole string spells, else None."""
    if not _NUMBER.match(text):
        return None
    try:
        return int(text)
    except ValueError:
        return float(text)


def _num(v):
    """A value as a number for arithmetic, as SQLite does for text (0 if none)."""
    if isinstance(v, str):
        m = _NUMBER_PREFIX.match(v)
        return _parse_number(m.group()) if m else 0
    return v


def _truth(v):
    return None if v is None else _num(v) != 0


def _key(v):
    """Sort and equality key: NULL < numbers < text."""
    if v is None:
        return (0, 0)
    if isinstance(v, str):
        return (2, v)
    return (1, v)


def _like(value, pattern):
    regex = "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in pattern)
    return re.fullmatch(regex, value, re.ASCII | re.IGNORECASE | re.DOTALL) is not None


def _to_text(v):
    return v if isinstance(v, str) else str(v)


def _coerce(value, type_):
    """Apply a column's type affinity to a value."""
    if value is None:
        return None
    if type_ == "TEXT":
        return _to_text(value)
    if isinstance(value, str):
        parsed = _parse_number(value)
        if parsed is None:
            return value
        value = parsed
    if type_ == "REAL":
        return float(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _arith(op, a, b):
    a, b = _num(a), _num(b)
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if b == 0:
        return None
    if isinstance(a, int) and isinstance(b, int):
        q = abs(a) // abs(b)
        return q if (a < 0) == (b < 0) else -q
    return a / b


_COMPARE = {
    "=": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


class _Scope:
    """Column layout of the rows a query reads: a list of (table, column)."""

    def __init__(self, columns):
        self.columns = columns
        self.qualified = {c: i for i, c in enumerate(columns)}
        self.by_name = {}
        for i, (_, name) in enumerate(columns):
            self.by_name.setdefault(name, []).append(i)

    def index(self, table, name):
        name = name.lower()
        if table is not None:
            found = self.qualified.get((table.lower(), name))
            if found is None:
                raise Error(f"no such column: {table}.{name}")
            return found
        found = self.by_name.get(name)
        if not found:
            raise Error(f"no such column: {name}")
        if len(found) > 1:
            raise Error(f"ambiguous column name: {name}")
        return found[0]


def _walk(e):
    yield e
    for part in e[1:]:
        if isinstance(part, tuple):
            yield from _walk(part)


def _has_agg(e):
    return any(n[0] == "agg" for n in _walk(e))


class Database:
    def __init__(self, path=None):
        self.path = path
        self.tables = {}
        if path is not None and os.path.exists(path):
            self._load()

    def execute(self, sql):
        stmt = parse(sql)
        kind = stmt[0]
        if kind == "select":
            return self._select(*stmt[1:])
        handler = {
            "create": self._create,
            "insert": self._insert,
            "update": self._update,
            "delete": self._delete,
        }.get(kind)
        if handler is None:
            raise Error(f"unsupported statement: {kind}")
        changed = handler(*stmt[1:])
        self._save()
        return changed

    # persistence

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.tables = {
                name: {"cols": [tuple(c) for c in t["cols"]], "rows": t["rows"]}
                for name, t in data["tables"].items()
            }
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise Error(f"cannot load database file {self.path}: {exc}") from exc

    def _save(self):
        if self.path is None:
            return
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"tables": self.tables}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError as exc:
            raise Error(f"cannot write database file {self.path}: {exc}") from exc

    # helpers

    def _table(self, name):
        table = self.tables.get(name.lower())
        if table is None:
            raise Error(f"no such table: {name}")
        return table

    @staticmethod
    def _table_scope(name, table):
        return _Scope([(name.lower(), c) for c, _ in table["cols"]])

    def _check(self, expr, scope, allow_agg=False):
        """Reject unknown columns and misplaced aggregates before any row is read."""
        for node in _walk(expr):
            if node[0] == "col":
                scope.index(node[1], node[2])
            elif node[0] == "agg" and not allow_agg:
                raise Error("misuse of aggregate function " + node[1])

    def _ev(self, e, scope, row, rows=None):
        """Evaluate e on one row; rows is the group an aggregate runs over."""
        kind = e[0]
        if kind == "lit":
            return e[1]
        if kind == "col":
            return None if row is None else row[scope.index(e[1], e[2])]
        if kind == "agg":
            if rows is None:
                raise Error("misuse of aggregate function " + e[1])
            return self._aggregate(e, scope, rows)
        if kind == "not":
            t = _truth(self._ev(e[1], scope, row, rows))
            return None if t is None else int(not t)
        if kind == "neg":
            v = self._ev(e[1], scope, row, rows)
            return None if v is None else -_num(v)
        if kind == "isnull":
            return int((self._ev(e[1], scope, row, rows) is None) != e[2])
        if kind == "like":
            value = self._ev(e[1], scope, row, rows)
            pattern = self._ev(e[2], scope, row, rows)
            if value is None or pattern is None:
                return None
            return int(_like(_to_text(value), _to_text(pattern)))
        if kind == "bin":
            return self._binary(e, scope, row, rows)
        raise Error(f"unsupported expression: {kind}")

    def _binary(self, e, scope, row, rows):
        op = e[1]
        a = self._ev(e[2], scope, row, rows)
        b = self._ev(e[3], scope, row, rows)
        if op in ("and", "or"):
            ta, tb = _truth(a), _truth(b)
            decisive = op == "or"
            if ta is decisive or tb is decisive:
                return int(decisive)
            return None if ta is None or tb is None else int(not decisive)
        if a is None or b is None:
            return None
        if op in _COMPARE:
            return int(_COMPARE[op](_key(a), _key(b)))
        if op in ("+", "-", "*", "/"):
            return _arith(op, a, b)
        raise Error(f"unsupported operator: {op}")

    def _aggregate(self, e, scope, rows):
        name, arg = e[1].lower(), e[2]
        if arg is None:
            return len(rows)
        values = [v for v in (self._ev(arg, scope, r) for r in rows) if v is not None]
        if name == "count":
            return len(values)
        if not values:
            return None
        if name in ("sum", "avg"):
            total = sum(_num(v) for v in values)
            return float(total) / len(values) if name == "avg" else total
        pick = min if name == "min" else max
        return pick(values, key=_key)

    # statements

    def _create(self, name, columns):
        key = name.lower()
        if key in self.tables:
            raise Error(f"table {name} already exists")
        cols = []
        for col, type_ in columns:
            type_ = type_.upper()
            if type_ not in _TYPES:
                raise Error(f"unknown type {type_} for column {col}")
            if any(c == col.lower() for c, _ in cols):
                raise Error(f"duplicate column name: {col}")
            cols.append((col.lower(), type_))
        self.tables[key] = {"cols": cols, "rows": []}
        return 0

    def _insert(self, name, columns, value_rows):
        table = self._table(name)
        names = [c for c, _ in table["cols"]]
        if columns is None:
            targets = list(range(len(names)))
        else:
            targets = []
            for col in columns:
                if col.lower() not in names:
                    raise Error(f"table {name} has no column named {col}")
                targets.append(names.index(col.lower()))
            if len(set(targets)) != len(targets):
                raise Error("duplicate column in INSERT")
        empty = _Scope([])
        new_rows = []
        for values in value_rows:
            if len(values) != len(targets):
                raise Error(f"{len(targets)} columns but {len(values)} values were supplied")
            row = [None] * len(names)
            for i, expr in zip(targets, values):
                self._check(expr, empty)
                row[i] = _coerce(self._ev(expr, empty, None), table["cols"][i][1])
            new_rows.append(row)
        table["rows"].extend(new_rows)
        return len(new_rows)

    def _update(self, name, assignments, where):
        table = self._table(name)
        scope = self._table_scope(name, table)
        names = [c for c, _ in table["cols"]]
        targets = []
        for col, expr in assignments:
            if col.lower() not in names:
                raise Error(f"no such column: {col}")
            self._check(expr, scope)
            targets.append(names.index(col.lower()))
        if where is not None:
            self._check(where, scope)
        changes = []
        for row in table["rows"]:
            if where is None or _truth(self._ev(where, scope, row)):
                new = list(row)
                for i, (_, expr) in zip(targets, assignments):
                    new[i] = _coerce(self._ev(expr, scope, row), table["cols"][i][1])
                changes.append((row, new))
        for row, new in changes:
            row[:] = new
        return len(changes)

    def _delete(self, name, where):
        table = self._table(name)
        scope = self._table_scope(name, table)
        if where is None:
            count = len(table["rows"])
            table["rows"] = []
            return count
        self._check(where, scope)
        kept = [r for r in table["rows"] if not _truth(self._ev(where, scope, r))]
        count = len(table["rows"]) - len(kept)
        table["rows"] = kept
        return count

    def _select(self, items, name, join, where, group, order, limit):
        base = self._table(name)
        columns = [(name.lower(), c) for c, _ in base["cols"]]
        rows = base["rows"]
        if join is not None:
            other_name, on = join
            other = self._table(other_name)
            columns += [(other_name.lower(), c) for c, _ in other["cols"]]
            scope = _Scope(columns)
            self._check(on, scope)
            rows = [
                left + right
                for left in rows
                for right in other["rows"]
                if _truth(self._ev(on, scope, left + right))
            ]
        scope = _Scope(columns)
        if items is None:
            items = [("col", t, c) for t, c in columns]
        exprs = items + list(group) + [e for e, _ in order]
        for e in items + [e for e, _ in order]:
            self._check(e, scope, allow_agg=True)
        for e in group:
            self._check(e, scope)
        if where is not None:
            self._check(where, scope)
            rows = [r for r in rows if _truth(self._ev(where, scope, r))]

        if group or any(_has_agg(e) for e in exprs):
            if group:
                buckets = {}
                for r in rows:
                    k = tuple(_key(self._ev(g, scope, r)) for g in group)
                    buckets.setdefault(k, []).append(r)
                units = list(buckets.values())
            else:
                units = [rows]
            evaluated = [
                (
                    [self._ev(e, scope, u[0] if u else None, u) for e in items],
                    [self._ev(e, scope, u[0] if u else None, u) for e, _ in order],
                )
                for u in units
            ]
        else:
            evaluated = [
                (
                    [self._ev(e, scope, r) for e in items],
                    [self._ev(e, scope, r) for e, _ in order],
                )
                for r in rows
            ]
        # stable sorts from the last key to the first give mixed ASC/DESC orders
        for i in reversed(range(len(order))):
            evaluated.sort(key=lambda pair: _key(pair[1][i]), reverse=order[i][1])
        result = [tuple(out) for out, _ in evaluated]
        return result if limit is None else result[: max(limit, 0)]
