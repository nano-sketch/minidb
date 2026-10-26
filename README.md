# minidb

A small SQL database engine in pure Python (no `sqlite3`, no dependencies).

```python
from minidb import Database, Error

db = Database()              # in memory
db = Database("shop.mdb")    # kept in a file, loaded if it exists, saved after every change

db.execute("CREATE TABLE users (id INTEGER, name TEXT, score REAL)")
db.execute("INSERT INTO users (id, name, score) VALUES (1, 'Ann', 9.5), (2, 'Ben', NULL)")  # -> 2

db.execute("SELECT name FROM users WHERE score IS NOT NULL")   # [('Ann',)]
db.execute("SELECT COUNT(*), AVG(score) FROM users")           # [(2, 9.5)]
db.execute("UPDATE users SET score = 7 WHERE name = 'Ben'")    # -> 1
db.execute("DELETE FROM users WHERE id = 1")                   # -> 1

try:
    db.execute("SELECT * FROM missing")
except Error as e:
    print(e)
```

`execute` returns a list of tuples for `SELECT` and the number of changed rows for
everything else. Every problem raises `minidb.Error`.

## Supported SQL

- `CREATE TABLE t (col INTEGER|REAL|TEXT, ...)`
- `INSERT INTO t (col, ...) VALUES (...), (...)` (omitted columns are `NULL`)
- `SELECT * | expr, ... FROM t [JOIN u ON expr] [WHERE expr] [GROUP BY expr, ...]
  [ORDER BY expr [ASC|DESC], ...] [LIMIT n]`
- `UPDATE t SET col = expr, ... [WHERE expr]`, `DELETE FROM t [WHERE expr]`
- Expressions: literals, `NULL`, columns (`users.name`), `+ - * /`,
  `= != < <= > >=`, `AND OR NOT`, `IS [NOT] NULL`, `LIKE` (`%`, `_`, case-insensitive)
- Aggregates: `COUNT(*)`, `COUNT(x)`, `SUM`, `AVG`, `MIN`, `MAX`

```sql
SELECT users.name, SUM(orders.total)
FROM users JOIN orders ON users.id = orders.user_id
GROUP BY users.name
ORDER BY SUM(orders.total) DESC
LIMIT 3
```

NULL follows SQL's three-valued logic, as in SQLite.

## Tests

```
python -m unittest
```
