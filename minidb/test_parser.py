import unittest

from minidb.errors import Error
from minidb.parser import parse
from minidb.tokenizer import tokenize


def col(name, table=None):
    return ("col", table, name)


class TokenizerTests(unittest.TestCase):
    def test_string_escape(self):
        self.assertEqual(tokenize("'it''s'")[0][:2], ("str", "it's"))

    def test_numbers(self):
        kinds = [t[:2] for t in tokenize("1 2.5")[:2]]
        self.assertEqual(kinds, [("num", 1), ("num", 2.5)])

    def test_not_equal_forms(self):
        self.assertEqual([t[1] for t in tokenize("<> !=")[:2]], ["!=", "!="])

    def test_unterminated_string(self):
        with self.assertRaises(Error):
            tokenize("'abc")

    def test_bad_character(self):
        with self.assertRaises(Error):
            tokenize("select #")


class StatementTests(unittest.TestCase):
    def test_create(self):
        self.assertEqual(
            parse("CREATE TABLE t (a integer, b TEXT, c Real);"),
            ("create", "t", [("a", "INTEGER"), ("b", "TEXT"), ("c", "REAL")]),
        )

    def test_create_bad_type(self):
        with self.assertRaises(Error):
            parse("CREATE TABLE t (a BLOB)")

    def test_insert_multi(self):
        self.assertEqual(
            parse("INSERT INTO t (a, b) VALUES (1, 'x'), (NULL, -2)"),
            ("insert", "t", ["a", "b"],
             [[("lit", 1), ("lit", "x")], [("lit", None), ("neg", ("lit", 2))]]),
        )

    def test_insert_without_columns(self):
        self.assertIsNone(parse("insert into t values (1)")[2])

    def test_select_star(self):
        self.assertEqual(
            parse("SELECT * FROM t"),
            ("select", None, "t", None, None, [], [], None),
        )

    def test_select_full(self):
        node = parse(
            "SELECT p.name, COUNT(*) FROM people JOIN pets ON people.id = pets.owner "
            "WHERE age > 3 GROUP BY p.name ORDER BY COUNT(*) DESC, name ASC, age LIMIT 5"
        )
        _, items, table, join, where, group_by, order_by, limit = node
        self.assertEqual(items, [col("name", "p"), ("agg", "count", None)])
        self.assertEqual(table, "people")
        self.assertEqual(join, ("pets", ("bin", "=", col("id", "people"), col("owner", "pets"))))
        self.assertEqual(where, ("bin", ">", col("age"), ("lit", 3)))
        self.assertEqual(group_by, [col("name", "p")])
        self.assertEqual(order_by, [(("agg", "count", None), True), (col("name"), False), (col("age"), False)])
        self.assertEqual(limit, 5)

    def test_aggregates(self):
        items = parse("SELECT SUM(a), avg(b), MIN(c), MAX(d), COUNT(e) FROM t")[1]
        self.assertEqual([i[1] for i in items], ["sum", "avg", "min", "max", "count"])
        self.assertEqual(items[4], ("agg", "count", col("e")))

    def test_sum_star_rejected(self):
        with self.assertRaises(Error):
            parse("SELECT SUM(*) FROM t")

    def test_update(self):
        self.assertEqual(
            parse("UPDATE t SET a = a + 1, b = 'z' WHERE a < 3"),
            ("update", "t",
             [("a", ("bin", "+", col("a"), ("lit", 1))), ("b", ("lit", "z"))],
             ("bin", "<", col("a"), ("lit", 3))),
        )

    def test_delete(self):
        self.assertEqual(parse("DELETE FROM t"), ("delete", "t", None))
        self.assertEqual(
            parse("DELETE FROM t WHERE a IS NULL"),
            ("delete", "t", ("isnull", col("a"), False)),
        )


def where(sql):
    return parse(f"SELECT * FROM t WHERE {sql}")[4]


class ExpressionTests(unittest.TestCase):
    def test_or_below_and(self):
        self.assertEqual(
            where("a OR b AND c"),
            ("bin", "or", col("a"), ("bin", "and", col("b"), col("c"))),
        )

    def test_not_below_and(self):
        self.assertEqual(
            where("NOT a AND b"),
            ("bin", "and", ("not", col("a")), col("b")),
        )

    def test_not_over_comparison(self):
        self.assertEqual(where("NOT a = 1"), ("not", ("bin", "=", col("a"), ("lit", 1))))

    def test_arithmetic_precedence(self):
        self.assertEqual(
            where("1 + 2 * 3 = 7"),
            ("bin", "=", ("bin", "+", ("lit", 1), ("bin", "*", ("lit", 2), ("lit", 3))), ("lit", 7)),
        )

    def test_left_associative(self):
        self.assertEqual(
            where("a - b - c = 0")[2],
            ("bin", "-", ("bin", "-", col("a"), col("b")), col("c")),
        )

    def test_unary_minus_binds_tighter_than_multiply(self):
        self.assertEqual(
            where("-a * b = 0")[2],
            ("bin", "*", ("neg", col("a")), col("b")),
        )

    def test_parentheses(self):
        self.assertEqual(
            where("(a OR b) AND c"),
            ("bin", "and", ("bin", "or", col("a"), col("b")), col("c")),
        )

    def test_is_null_forms(self):
        self.assertEqual(where("a IS NULL"), ("isnull", col("a"), False))
        self.assertEqual(where("a IS NOT NULL"), ("isnull", col("a"), True))

    def test_like(self):
        self.assertEqual(where("a LIKE 'x%'"), ("like", col("a"), ("lit", "x%")))
        self.assertEqual(where("a NOT LIKE 'x'"), ("not", ("like", col("a"), ("lit", "x"))))

    def test_null_literal_and_not_equal(self):
        self.assertEqual(where("a <> NULL"), ("bin", "!=", col("a"), ("lit", None)))


class ErrorTests(unittest.TestCase):
    def test_bad_statements(self):
        for sql in [
            "",
            "DROP TABLE t",
            "SELECT FROM t",
            "SELECT * t",
            "SELECT * FROM t WHERE",
            "SELECT * FROM t LIMIT 1.5",
            "SELECT * FROM t LIMIT x",
            "SELECT (1 FROM t",
            "SELECT * FROM t extra",
            "INSERT INTO t VALUES 1",
            "INSERT INTO t VALUES (1,)",
            "UPDATE t SET",
            "DELETE t",
            "CREATE TABLE t ()",
            "SELECT * FROM t WHERE a = 'oops",
        ]:
            with self.subTest(sql=sql), self.assertRaises(Error):
                parse(sql)

    def test_message_is_clear(self):
        with self.assertRaisesRegex(Error, "expected FROM"):
            parse("SELECT a b")


if __name__ == "__main__":
    unittest.main()
