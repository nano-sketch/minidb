import os
import tempfile
import unittest

from minidb import Database, Error


class DbCase(unittest.TestCase):
    def setUp(self):
        self.db = Database()

    def q(self, sql):
        return self.db.execute(sql)


class PeopleCase(DbCase):
    def setUp(self):
        super().setUp()
        self.q("CREATE TABLE people (id INTEGER, name TEXT, age INTEGER, city TEXT)")
        self.q(
            "INSERT INTO people (id, name, age, city) VALUES "
            "(1, 'Alice', 30, 'Paris'), (2, 'Bob', 25, 'Rome'), "
            "(3, 'Carol', 35, 'Paris'), (4, 'Dave', NULL, 'Rome'), "
            "(5, 'Eve', 25, NULL)"
        )


class TestBasics(DbCase):
    def test_create_insert_select(self):
        self.assertEqual(self.q("CREATE TABLE t (a INTEGER, b REAL, c TEXT)"), 0)
        self.assertEqual(self.q("INSERT INTO t (a, b, c) VALUES (1, 2.5, 'x')"), 1)
        self.assertEqual(self.q("SELECT * FROM t"), [(1, 2.5, "x")])

    def test_multi_row_insert_count(self):
        self.q("CREATE TABLE t (a INTEGER)")
        self.assertEqual(self.q("INSERT INTO t (a) VALUES (1), (2), (3)"), 3)
        self.assertEqual(self.q("SELECT a FROM t"), [(1,), (2,), (3,)])

    def test_missing_columns_are_null(self):
        self.q("CREATE TABLE t (a INTEGER, b TEXT)")
        self.q("INSERT INTO t (b) VALUES ('x')")
        self.assertEqual(self.q("SELECT * FROM t"), [(None, "x")])

    def test_insert_column_order(self):
        self.q("CREATE TABLE t (a INTEGER, b TEXT)")
        self.q("INSERT INTO t (b, a) VALUES ('x', 7)")
        self.assertEqual(self.q("SELECT * FROM t"), [(7, "x")])

    def test_escaped_quote(self):
        self.q("CREATE TABLE t (a TEXT)")
        self.q("INSERT INTO t (a) VALUES ('it''s')")
        self.assertEqual(self.q("SELECT a FROM t"), [("it's",)])

    def test_keywords_case_insensitive(self):
        self.q("create table t (a integer)")
        self.q("insert into t (a) values (1)")
        self.assertEqual(self.q("select a from t where a = 1"), [(1,)])

    def test_trailing_semicolon(self):
        self.q("CREATE TABLE t (a INTEGER);")
        self.assertEqual(self.q("SELECT a FROM t;"), [])

    def test_real_and_integer_types(self):
        self.q("CREATE TABLE t (a INTEGER, b REAL)")
        self.q("INSERT INTO t (a, b) VALUES (1, 2)")
        row = self.q("SELECT a, b FROM t")[0]
        self.assertEqual(row, (1, 2.0))
        self.assertIsInstance(row[0], int)
        self.assertIsInstance(row[1], float)


class TestExpressions(DbCase):
    def setUp(self):
        super().setUp()
        self.q("CREATE TABLE t (a INTEGER, b INTEGER, s TEXT)")
        self.q("INSERT INTO t (a, b, s) VALUES (7, 2, 'Hello')")

    def one(self, expr):
        return self.q(f"SELECT {expr} FROM t")[0][0]

    def test_arithmetic(self):
        self.assertEqual(self.one("a + b"), 9)
        self.assertEqual(self.one("a - b"), 5)
        self.assertEqual(self.one("a * b"), 14)
        self.assertEqual(self.one("a + b * 2"), 11)
        self.assertEqual(self.one("(a + b) * 2"), 18)
        self.assertEqual(self.one("-a"), -7)
        self.assertEqual(self.one("1.5 + 1"), 2.5)

    def test_integer_division_truncates(self):
        self.assertEqual(self.one("a / b"), 3)
        self.assertEqual(self.one("7.0 / 2"), 3.5)

    def test_division_by_zero_is_null(self):
        self.assertIsNone(self.one("a / 0"))

    def test_comparisons(self):
        for expr, want in [
            ("a = 7", 1), ("a != 7", 0), ("a < 8", 1), ("a <= 7", 1),
            ("a > 7", 0), ("a >= 8", 0), ("s = 'Hello'", 1), ("s < 'Z'", 1),
        ]:
            self.assertEqual(self.one(expr), want, expr)

    def test_where_filters(self):
        self.assertEqual(self.q("SELECT a FROM t WHERE a > 100"), [])
        self.assertEqual(self.q("SELECT a FROM t WHERE a > 1 AND b = 2"), [(7,)])
        self.assertEqual(self.q("SELECT a FROM t WHERE a > 100 OR b = 2"), [(7,)])
        self.assertEqual(self.q("SELECT a FROM t WHERE NOT a = 7"), [])


class TestNullLogic(DbCase):
    def setUp(self):
        super().setUp()
        self.q("CREATE TABLE t (n INTEGER)")
        self.q("INSERT INTO t (n) VALUES (NULL)")

    def one(self, expr):
        return self.q(f"SELECT {expr} FROM t")[0][0]

    def test_arithmetic_and_comparison_with_null(self):
        for expr in ["n + 1", "n - 1", "n * 2", "n / 2", "n = 1", "n = n",
                     "n != 1", "n < 1", "n <= 1", "n > 1", "n >= 1", "1 = NULL"]:
            self.assertIsNone(self.one(expr), expr)

    def test_not(self):
        self.assertIsNone(self.one("NOT NULL"))
        self.assertEqual(self.one("NOT 1"), 0)
        self.assertEqual(self.one("NOT 0"), 1)

    def test_and_truth_table(self):
        cases = [
            ("1 AND 1", 1), ("1 AND 0", 0), ("0 AND 1", 0), ("0 AND 0", 0),
            ("NULL AND 0", 0), ("0 AND NULL", 0),
            ("NULL AND 1", None), ("1 AND NULL", None), ("NULL AND NULL", None),
        ]
        for expr, want in cases:
            self.assertEqual(self.one(expr), want, expr)

    def test_or_truth_table(self):
        cases = [
            ("1 OR 1", 1), ("1 OR 0", 1), ("0 OR 1", 1), ("0 OR 0", 0),
            ("NULL OR 1", 1), ("1 OR NULL", 1),
            ("NULL OR 0", None), ("0 OR NULL", None), ("NULL OR NULL", None),
        ]
        for expr, want in cases:
            self.assertEqual(self.one(expr), want, expr)

    def test_is_null(self):
        self.assertEqual(self.one("n IS NULL"), 1)
        self.assertEqual(self.one("n IS NOT NULL"), 0)
        self.assertEqual(self.one("1 IS NULL"), 0)
        self.assertEqual(self.one("1 IS NOT NULL"), 1)

    def test_where_null_drops_row(self):
        self.assertEqual(self.q("SELECT n FROM t WHERE n = 1"), [])
        self.assertEqual(self.q("SELECT n FROM t WHERE n != 1"), [])
        self.assertEqual(self.q("SELECT n FROM t WHERE NOT (n = 1)"), [])
        self.assertEqual(self.q("SELECT n FROM t WHERE n IS NULL"), [(None,)])

    def test_null_comparison_does_not_match_in_update_delete(self):
        self.assertEqual(self.q("UPDATE t SET n = 5 WHERE n = 1"), 0)
        self.assertEqual(self.q("DELETE FROM t WHERE n != 1"), 0)
        self.assertEqual(self.q("DELETE FROM t WHERE n IS NULL"), 1)


class TestLike(DbCase):
    def setUp(self):
        super().setUp()
        self.q("CREATE TABLE w (s TEXT)")
        self.q("INSERT INTO w (s) VALUES ('apple'), ('Apricot'), ('banana'), ('cab'), (NULL)")

    def like(self, pattern):
        rows = self.q(f"SELECT s FROM w WHERE s LIKE '{pattern}'")
        return sorted(r[0] for r in rows)

    def test_percent(self):
        self.assertEqual(self.like("a%"), ["Apricot", "apple"])
        self.assertEqual(self.like("%an%"), ["banana"])
        self.assertEqual(self.like("%"), ["Apricot", "apple", "banana", "cab"])

    def test_underscore(self):
        self.assertEqual(self.like("ca_"), ["cab"])
        self.assertEqual(self.like("_____"), ["apple"])

    def test_case_insensitive(self):
        self.assertEqual(self.like("APPLE"), ["apple"])
        self.assertEqual(self.like("aPr%"), ["Apricot"])

    def test_must_match_whole_string(self):
        self.assertEqual(self.like("app"), [])
        self.assertEqual(self.like("ban"), [])

    def test_null_is_not_matched(self):
        self.assertIsNone(self.q("SELECT s LIKE 'a%' FROM w WHERE s IS NULL")[0][0])

    def test_regex_characters_are_literal(self):
        self.q("INSERT INTO w (s) VALUES ('a.c'), ('abc')")
        self.assertEqual(self.like("a.c"), ["a.c"])


class TestSelect(PeopleCase):
    def test_star(self):
        self.assertEqual(self.q("SELECT * FROM people")[0], (1, "Alice", 30, "Paris"))
        self.assertEqual(len(self.q("SELECT * FROM people")), 5)

    def test_expression_list(self):
        self.assertEqual(
            self.q("SELECT name, age + 1 FROM people WHERE id = 1"),
            [("Alice", 31)],
        )

    def test_where_with_null_column(self):
        self.assertEqual(self.q("SELECT name FROM people WHERE age > 20 ORDER BY id"),
                         [("Alice",), ("Bob",), ("Carol",), ("Eve",)])
        self.assertEqual(self.q("SELECT name FROM people WHERE age IS NULL"), [("Dave",)])

    def test_qualified_column(self):
        self.assertEqual(self.q("SELECT people.name FROM people WHERE people.id = 2"),
                         [("Bob",)])

    def test_order_by_asc_desc(self):
        self.assertEqual(self.q("SELECT id FROM people ORDER BY id DESC"),
                         [(5,), (4,), (3,), (2,), (1,)])
        self.assertEqual(self.q("SELECT id FROM people ORDER BY id ASC"),
                         [(1,), (2,), (3,), (4,), (5,)])

    def test_order_by_nulls_first_ascending(self):
        # SQLite sorts NULL before every other value.
        self.assertEqual(self.q("SELECT id FROM people ORDER BY age, id"),
                         [(4,), (2,), (5,), (1,), (3,)])
        self.assertEqual(self.q("SELECT id FROM people ORDER BY age DESC, id"),
                         [(3,), (1,), (2,), (5,), (4,)])

    def test_order_by_expression_and_multiple_keys(self):
        self.assertEqual(self.q("SELECT id FROM people ORDER BY city, name DESC"),
                         [(5,), (3,), (1,), (4,), (2,)])
        self.assertEqual(self.q("SELECT id FROM people WHERE age IS NOT NULL ORDER BY 0 - age, id"),
                         [(3,), (1,), (2,), (5,)])

    def test_limit(self):
        self.assertEqual(self.q("SELECT id FROM people ORDER BY id LIMIT 2"), [(1,), (2,)])
        self.assertEqual(self.q("SELECT id FROM people LIMIT 0"), [])
        self.assertEqual(len(self.q("SELECT id FROM people LIMIT 100")), 5)

    def test_select_from_empty_table(self):
        self.q("CREATE TABLE e (a INTEGER)")
        self.assertEqual(self.q("SELECT * FROM e"), [])


class TestJoin(DbCase):
    def setUp(self):
        super().setUp()
        self.q("CREATE TABLE users (id INTEGER, name TEXT)")
        self.q("CREATE TABLE orders (id INTEGER, user_id INTEGER, total REAL)")
        self.q("INSERT INTO users (id, name) VALUES (1, 'Ann'), (2, 'Ben'), (3, 'Cy')")
        self.q("INSERT INTO orders (id, user_id, total) VALUES "
               "(10, 1, 5.5), (11, 1, 4.5), (12, 2, 10.0), (13, 9, 1.0), (14, NULL, 2.0)")

    def test_inner_join(self):
        rows = self.q("SELECT users.name, orders.total FROM users "
                      "JOIN orders ON users.id = orders.user_id ORDER BY orders.id")
        self.assertEqual(rows, [("Ann", 5.5), ("Ann", 4.5), ("Ben", 10.0)])

    def test_join_where_and_same_column_names(self):
        rows = self.q("SELECT orders.id, users.id FROM orders "
                      "JOIN users ON orders.user_id = users.id WHERE users.name = 'Ben'")
        self.assertEqual(rows, [(12, 2)])

    def test_join_star_has_all_columns(self):
        rows = self.q("SELECT * FROM users JOIN orders ON users.id = orders.user_id "
                      "WHERE orders.id = 12")
        self.assertEqual(rows, [(2, "Ben", 12, 2, 10.0)])

    def test_join_with_group_by(self):
        rows = self.q("SELECT users.name, SUM(orders.total) FROM users "
                      "JOIN orders ON users.id = orders.user_id "
                      "GROUP BY users.name ORDER BY users.name")
        self.assertEqual(rows, [("Ann", 10.0), ("Ben", 10.0)])

    def test_join_order_by_limit(self):
        rows = self.q("SELECT orders.id FROM users JOIN orders ON users.id = orders.user_id "
                      "ORDER BY orders.total DESC LIMIT 1")
        self.assertEqual(rows, [(12,)])

    def test_ambiguous_column_is_error(self):
        with self.assertRaises(Error):
            self.q("SELECT id FROM users JOIN orders ON users.id = orders.user_id")


class TestAggregates(PeopleCase):
    def test_count_star_and_expr(self):
        self.assertEqual(self.q("SELECT COUNT(*) FROM people"), [(5,)])
        self.assertEqual(self.q("SELECT COUNT(age) FROM people"), [(4,)])
        self.assertEqual(self.q("SELECT COUNT(city) FROM people"), [(4,)])

    def test_sum_avg_min_max(self):
        self.assertEqual(self.q("SELECT SUM(age) FROM people"), [(115,)])
        self.assertEqual(self.q("SELECT AVG(age) FROM people"), [(28.75,)])
        self.assertEqual(self.q("SELECT MIN(age), MAX(age) FROM people"), [(25, 35)])
        self.assertEqual(self.q("SELECT MIN(name), MAX(name) FROM people"), [("Alice", "Eve")])

    def test_avg_is_real(self):
        self.q("CREATE TABLE n (v INTEGER)")
        self.q("INSERT INTO n (v) VALUES (2), (4)")
        r = self.q("SELECT AVG(v) FROM n")[0][0]
        self.assertEqual(r, 3.0)
        self.assertIsInstance(r, float)

    def test_aggregate_with_where(self):
        self.assertEqual(self.q("SELECT COUNT(*) FROM people WHERE city = 'Paris'"), [(2,)])

    def test_aggregate_expression(self):
        self.assertEqual(self.q("SELECT MAX(age) - MIN(age) FROM people"), [(10,)])
        self.assertEqual(self.q("SELECT SUM(age * 2) FROM people"), [(230,)])

    def test_empty_table(self):
        self.q("CREATE TABLE e (a INTEGER)")
        self.assertEqual(self.q("SELECT COUNT(*) FROM e"), [(0,)])
        self.assertEqual(self.q("SELECT COUNT(a) FROM e"), [(0,)])
        self.assertEqual(self.q("SELECT SUM(a), AVG(a), MIN(a), MAX(a) FROM e"),
                         [(None, None, None, None)])

    def test_no_rows_after_where(self):
        self.assertEqual(self.q("SELECT COUNT(*), SUM(age) FROM people WHERE id > 99"),
                         [(0, None)])

    def test_all_null_values(self):
        self.q("CREATE TABLE z (a INTEGER)")
        self.q("INSERT INTO z (a) VALUES (NULL), (NULL)")
        self.assertEqual(self.q("SELECT COUNT(*), COUNT(a), SUM(a), MIN(a) FROM z"),
                         [(2, 0, None, None)])

    def test_group_by(self):
        rows = self.q("SELECT city, COUNT(*) FROM people GROUP BY city ORDER BY city")
        self.assertEqual(rows, [(None, 1), ("Paris", 2), ("Rome", 2)])

    def test_group_by_aggregates(self):
        rows = self.q("SELECT city, SUM(age), MIN(age), MAX(age), COUNT(age) "
                      "FROM people WHERE city IS NOT NULL GROUP BY city ORDER BY city")
        self.assertEqual(rows, [("Paris", 65, 30, 35, 2), ("Rome", 25, 25, 25, 1)])

    def test_group_by_on_empty_table_has_no_rows(self):
        self.q("CREATE TABLE e (a INTEGER)")
        self.assertEqual(self.q("SELECT a, COUNT(*) FROM e GROUP BY a"), [])

    def test_group_by_expression(self):
        rows = self.q("SELECT age, COUNT(*) FROM people GROUP BY age ORDER BY age")
        self.assertEqual(rows, [(None, 1), (25, 2), (30, 1), (35, 1)])

    def test_group_by_two_columns(self):
        self.q("CREATE TABLE g (a INTEGER, b INTEGER)")
        self.q("INSERT INTO g (a, b) VALUES (1, 1), (1, 1), (1, 2), (2, 1)")
        rows = self.q("SELECT a, b, COUNT(*) FROM g GROUP BY a, b ORDER BY a, b")
        self.assertEqual(rows, [(1, 1, 2), (1, 2, 1), (2, 1, 1)])

    def test_order_by_aggregate(self):
        rows = self.q("SELECT city, COUNT(*) FROM people WHERE city IS NOT NULL "
                      "GROUP BY city ORDER BY COUNT(*) DESC, city")
        self.assertEqual(rows, [("Paris", 2), ("Rome", 2)])
        rows = self.q("SELECT city FROM people WHERE city IS NOT NULL "
                      "GROUP BY city ORDER BY SUM(age) DESC")
        self.assertEqual(rows, [("Paris",), ("Rome",)])
        rows = self.q("SELECT city, MAX(age) FROM people WHERE city IS NOT NULL "
                      "GROUP BY city ORDER BY MAX(age) ASC LIMIT 1")
        self.assertEqual(rows, [("Rome", 25)])


class TestUpdateDelete(PeopleCase):
    def test_update_with_where(self):
        self.assertEqual(self.q("UPDATE people SET age = 26 WHERE age = 25"), 2)
        self.assertEqual(self.q("SELECT COUNT(*) FROM people WHERE age = 26"), [(2,)])

    def test_update_all_rows(self):
        self.assertEqual(self.q("UPDATE people SET city = 'Oslo'"), 5)
        self.assertEqual(self.q("SELECT COUNT(*) FROM people WHERE city = 'Oslo'"), [(5,)])

    def test_update_multiple_columns_and_expressions(self):
        self.assertEqual(self.q("UPDATE people SET age = age + 1, name = 'Zed' WHERE id = 1"), 1)
        self.assertEqual(self.q("SELECT name, age FROM people WHERE id = 1"), [("Zed", 31)])

    def test_update_uses_old_values(self):
        self.q("CREATE TABLE s (a INTEGER, b INTEGER)")
        self.q("INSERT INTO s (a, b) VALUES (1, 2)")
        self.q("UPDATE s SET a = b, b = a")
        self.assertEqual(self.q("SELECT a, b FROM s"), [(2, 1)])

    def test_update_to_null_and_null_arithmetic(self):
        self.q("UPDATE people SET age = NULL WHERE id = 1")
        self.assertEqual(self.q("SELECT age FROM people WHERE id = 1"), [(None,)])
        self.assertEqual(self.q("UPDATE people SET age = age + 1 WHERE age IS NULL"), 2)
        self.assertEqual(self.q("SELECT COUNT(age) FROM people"), [(3,)])

    def test_update_no_match(self):
        self.assertEqual(self.q("UPDATE people SET age = 1 WHERE id = 99"), 0)

    def test_delete_with_where(self):
        self.assertEqual(self.q("DELETE FROM people WHERE city = 'Paris'"), 2)
        self.assertEqual(self.q("SELECT COUNT(*) FROM people"), [(3,)])

    def test_delete_all(self):
        self.assertEqual(self.q("DELETE FROM people"), 5)
        self.assertEqual(self.q("SELECT * FROM people"), [])

    def test_delete_no_match(self):
        self.assertEqual(self.q("DELETE FROM people WHERE id = 99"), 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM people"), [(5,)])


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "data.mdb")

    def test_reopen_loads_data(self):
        db = Database(self.path)
        db.execute("CREATE TABLE t (a INTEGER, b REAL, c TEXT)")
        db.execute("INSERT INTO t (a, b, c) VALUES (1, 1.5, 'it''s'), (2, NULL, NULL)")
        again = Database(self.path)
        self.assertEqual(again.execute("SELECT * FROM t ORDER BY a"),
                         [(1, 1.5, "it's"), (2, None, None)])

    def test_written_after_each_change_without_close(self):
        db = Database(self.path)
        db.execute("CREATE TABLE t (a INTEGER)")
        self.assertEqual(Database(self.path).execute("SELECT * FROM t"), [])
        db.execute("INSERT INTO t (a) VALUES (1), (2), (3)")
        self.assertEqual(Database(self.path).execute("SELECT COUNT(*) FROM t"), [(3,)])
        db.execute("UPDATE t SET a = a * 10 WHERE a > 1")
        self.assertEqual(Database(self.path).execute("SELECT a FROM t ORDER BY a"),
                         [(1,), (20,), (30,)])
        db.execute("DELETE FROM t WHERE a = 1")
        self.assertEqual(Database(self.path).execute("SELECT a FROM t ORDER BY a"),
                         [(20,), (30,)])

    def test_new_path_starts_empty(self):
        db = Database(self.path)
        with self.assertRaises(Error):
            db.execute("SELECT * FROM t")

    def test_two_tables_survive(self):
        db = Database(self.path)
        db.execute("CREATE TABLE a (x INTEGER)")
        db.execute("CREATE TABLE b (y TEXT)")
        db.execute("INSERT INTO b (y) VALUES ('hi')")
        again = Database(self.path)
        self.assertEqual(again.execute("SELECT x FROM a"), [])
        self.assertEqual(again.execute("SELECT y FROM b"), [("hi",)])

    def test_in_memory_database_writes_nothing(self):
        before = set(os.listdir(self.dir.name))
        db = Database()
        db.execute("CREATE TABLE t (a INTEGER)")
        self.assertEqual(set(os.listdir(self.dir.name)), before)


class TestErrors(PeopleCase):
    def assertFails(self, sql):
        with self.assertRaises(Error) as ctx:
            self.q(sql)
        self.assertTrue(str(ctx.exception))

    def test_error_is_exception(self):
        self.assertTrue(issubclass(Error, Exception))

    def test_unknown_table(self):
        self.assertFails("SELECT * FROM nope")
        self.assertFails("INSERT INTO nope (a) VALUES (1)")
        self.assertFails("UPDATE nope SET a = 1")
        self.assertFails("DELETE FROM nope")
        self.assertFails("SELECT * FROM people JOIN nope ON people.id = nope.id")

    def test_unknown_column(self):
        self.assertFails("SELECT nope FROM people")
        self.assertFails("SELECT * FROM people WHERE nope = 1")
        self.assertFails("SELECT people.nope FROM people")
        self.assertFails("INSERT INTO people (nope) VALUES (1)")
        self.assertFails("UPDATE people SET nope = 1")
        self.assertFails("SELECT * FROM people ORDER BY nope")

    def test_syntax_errors(self):
        self.assertFails("SELEC * FROM people")
        self.assertFails("SELECT FROM people")
        self.assertFails("SELECT * FROM")
        self.assertFails("SELECT * FROM people WHERE")
        self.assertFails("SELECT (1 + FROM people")
        self.assertFails("SELECT * FROM people LIMIT")
        self.assertFails("CREATE TABLE x (a)")
        self.assertFails("INSERT INTO people VALUES")
        self.assertFails("SELECT * FROM people extra garbage")
        self.assertFails("")

    def test_unterminated_string(self):
        self.assertFails("SELECT * FROM people WHERE name = 'Alice")
        self.assertFails("INSERT INTO people (name) VALUES ('oops)")

    def test_wrong_value_count(self):
        self.assertFails("INSERT INTO people (id, name) VALUES (1)")
        self.assertFails("INSERT INTO people (id) VALUES (1, 2)")
        self.assertFails("INSERT INTO people (id, name) VALUES (1, 'a'), (2)")

    def test_duplicate_table(self):
        self.assertFails("CREATE TABLE people (a INTEGER)")

    def test_unknown_type(self):
        self.assertFails("CREATE TABLE x (a BLOBBY)")

    def test_failed_statement_changes_nothing(self):
        self.assertFails("INSERT INTO people (id, name) VALUES (9, 'ok'), (10)")
        self.assertEqual(self.q("SELECT COUNT(*) FROM people"), [(5,)])


if __name__ == "__main__":
    unittest.main()
