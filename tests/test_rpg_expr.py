"""The derivation evaluator (Phase 9, Tier 0), offline: the arithmetic it computes and —
the point of a sandbox — the reject-list of everything it refuses. All pure, no network.
"""
import unittest

from loom.rpg.expr import Expr, ExprError, compile_expr, evaluate, SAFE_FUNCS


class ArithmeticTests(unittest.TestCase):
    def test_precedence_and_operators(self):
        self.assertEqual(evaluate("2 + 3 * 4"), 14)
        self.assertEqual(evaluate("(2 + 3) * 4"), 20)
        self.assertEqual(evaluate("10 - 4 - 3"), 3)
        self.assertEqual(evaluate("17 % 5"), 2)
        self.assertEqual(evaluate("2 ** 10"), 1024)

    def test_division_forms(self):
        self.assertEqual(evaluate("7 / 2"), 3.5)      # true division → float
        self.assertEqual(evaluate("7 // 2"), 3)       # floor division → int
        self.assertEqual(evaluate("-2 // 2"), -1)     # floors toward -inf (5e mod maths)

    def test_unary(self):
        self.assertEqual(evaluate("-5"), -5)
        self.assertEqual(evaluate("+5"), 5)
        self.assertEqual(evaluate("- -5"), 5)
        self.assertTrue(evaluate("not 0"))
        self.assertFalse(evaluate("not 3"))

    def test_bool_and_literals(self):
        self.assertIs(evaluate("True"), True)
        self.assertIs(evaluate("False"), False)
        self.assertEqual(evaluate("True + 1"), 2)     # bool is a number here

    def test_names_from_namespace(self):
        self.assertEqual(evaluate("a + b", {"a": 3, "b": 4}), 7)
        self.assertEqual(evaluate("(con - 10) // 2", {"con": 14}), 2)
        self.assertEqual(evaluate("(con - 10) // 2", {"con": 8}), -1)

    def test_dnd_derivations(self):
        ns = {"hit_die": 8, "level": 3, "con_mod": 2}
        self.assertEqual(evaluate("10 + hit_die*level + con_mod*level", ns), 40)
        self.assertEqual(evaluate("2 + (level - 1) // 4", {"level": 9}), 4)  # prof at L9

    def test_functions(self):
        self.assertEqual(evaluate("min(3, 1, 2)"), 1)
        self.assertEqual(evaluate("max(3, 1, 2)"), 3)
        self.assertEqual(evaluate("floor(3.7)"), 3)
        self.assertEqual(evaluate("ceil(3.2)"), 4)
        self.assertEqual(evaluate("clamp(15, 8, 12)"), 12)
        self.assertEqual(evaluate("clamp(5, 8, 12)"), 8)
        self.assertEqual(evaluate("clamp(10, 8, 12)"), 10)

    def test_comparisons_and_conditional(self):
        self.assertTrue(evaluate("3 < 5"))
        self.assertFalse(evaluate("3 > 5"))
        self.assertTrue(evaluate("1 < x < 10", {"x": 5}))
        self.assertFalse(evaluate("1 < x < 10", {"x": 15}))
        self.assertEqual(evaluate("5 if m > 2 else 3", {"m": 4}), 5)
        self.assertEqual(evaluate("5 if m > 2 else 3", {"m": 1}), 3)

    def test_boolop_short_circuit(self):
        self.assertTrue(evaluate("a > 0 and b > 0", {"a": 1, "b": 1}))
        self.assertFalse(evaluate("a > 0 and b > 0", {"a": 1, "b": -1}))
        self.assertEqual(evaluate("a or b", {"a": 0, "b": 7}), 7)


class CompiledExprTests(unittest.TestCase):
    def test_free_names_collected(self):
        e = compile_expr("10 + hit_die*level + con_mod*level")
        self.assertEqual(e.names, frozenset({"hit_die", "level", "con_mod"}))

    def test_function_name_is_not_a_free_name(self):
        e = compile_expr("min(a, b)")
        self.assertEqual(e.names, frozenset({"a", "b"}))

    def test_reusable_across_namespaces(self):
        e = compile_expr("(con - 10) // 2")
        self.assertEqual(e.evaluate({"con": 20}), 5)
        self.assertEqual(e.evaluate({"con": 12}), 1)

    def test_repr_and_source(self):
        e = compile_expr("a + 1")
        self.assertEqual(e.source, "a + 1")
        self.assertIn("a + 1", repr(e))

    def test_safe_funcs_surface(self):
        self.assertEqual(set(SAFE_FUNCS), {"min", "max", "floor", "ceil", "clamp"})


class RejectListTests(unittest.TestCase):
    """Every forbidden construct is refused at compile time, before any value is computed."""

    FORBIDDEN = [
        "x.__class__",          # attribute access
        "().__class__",         # attribute access on a literal
        "x[0]",                 # subscription
        "lambda: 1",            # lambda
        "[i for i in x]",       # comprehension
        "{i for i in x}",       # set comprehension
        "{k: v for k in x}",    # dict comprehension
        "range(3)",             # call to a non-safe function
        "eval('1')",            # the obvious one
        "open('/etc/passwd')",  # I/O
        "__import__('os')",     # import
        "min",                  # a safe function used as a value, not called
        "min(*x)",              # argument unpacking
        "min(1, key=2)",        # keyword argument
        "'hello'",              # non-numeric literal
        "1 + 'a'",              # string inside arithmetic
        "b'x'",                 # bytes literal
        "None",                 # None literal
        "1j",                   # complex literal
        "f'{x}'",               # f-string
        "[1, 2, 3]",            # list display
        "(1, 2)",               # tuple display
        "{1: 2}",               # dict display
    ]

    def test_forbidden_constructs_rejected(self):
        for src in self.FORBIDDEN:
            with self.subTest(src=src):
                with self.assertRaises(ExprError):
                    compile_expr(src)

    def test_assignment_and_walrus_rejected(self):
        # A bare assignment is not an expression (SyntaxError → ExprError); the walrus is.
        for src in ("x = 5", "(x := 5)"):
            with self.subTest(src=src):
                with self.assertRaises(ExprError):
                    compile_expr(src)

    def test_arity_is_enforced_at_compile(self):
        for src in ("floor(1, 2)", "ceil()", "clamp(1, 2)", "clamp(1, 2, 3, 4)", "min()"):
            with self.subTest(src=src):
                with self.assertRaises(ExprError):
                    compile_expr(src)

    def test_syntax_error_wrapped(self):
        with self.assertRaises(ExprError):
            compile_expr("2 +")


class SizeGuardTests(unittest.TestCase):
    def test_exponent_capped(self):
        self.assertEqual(evaluate("2 ** 16"), 65536)   # within cap
        with self.assertRaises(ExprError):
            evaluate("2 ** 100")                        # exponent > max_pow

    def test_nested_pow_bit_length_capped(self):
        with self.assertRaises(ExprError):
            evaluate("(2 ** 64) ** 64")                 # ~2**4096, over the bit cap

    def test_non_finite_float_rejected(self):
        with self.assertRaises(ExprError):
            evaluate("1e308 * 1e308")                   # overflows to inf

    def test_division_by_zero(self):
        for src in ("1 / 0", "1 // 0", "5 % 0"):
            with self.subTest(src=src):
                with self.assertRaises(ExprError):
                    evaluate(src)

    def test_complexity_capped(self):
        with self.assertRaises(ExprError):
            compile_expr("1" + "+1" * 300)

    def test_source_length_capped(self):
        with self.assertRaises(ExprError):
            compile_expr("1 + " * 200 + "1")


class NamespaceTests(unittest.TestCase):
    def test_unknown_name_raises_at_eval(self):
        with self.assertRaises(ExprError):
            evaluate("x + 1")                           # nothing supplied
        with self.assertRaises(ExprError):
            evaluate("x + y", {"x": 1})                 # y missing

    def test_non_numeric_namespace_value_rejected(self):
        with self.assertRaises(ExprError):
            evaluate("x + 1", {"x": "oops"})

    def test_non_string_source_rejected(self):
        with self.assertRaises(ExprError):
            compile_expr(42)

    def test_determinism(self):
        e = compile_expr("a * b + c")
        ns = {"a": 3, "b": 4, "c": 5}
        self.assertEqual(e.evaluate(ns), e.evaluate(ns))


if __name__ == "__main__":
    unittest.main()
