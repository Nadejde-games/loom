"""The derivation evaluator — the one load-bearing mechanism of the RPG layer (Phase 9,
Tier 0). See docs/spikes/rpg-systems.md §B.

Every number the mechanics compute — a pool's maximum and regen, an XP curve, a to-hit or
damage formula — is authored as a **sandboxed arithmetic expression string** in world data
and evaluated here. One evaluator, reused everywhere, so the game declares its own maths as
data and the framework hardcodes no formula (invariant I1: mechanism in ``loom``, rules as
data). It is the ``simpleeval`` design pattern (the field's consensus for safe formula
evaluation) reduced to arithmetic-only: parse to an AST in ``eval`` mode, walk a **whitelist**
of node types, resolve names from a fixed caller-supplied namespace, and forbid everything
else. Trust level: a spreadsheet cell — never a code path, never the host language.

What is allowed:
- literals (int / float / bool only), and names bound in the supplied namespace;
- arithmetic ``+ - * / // % **`` and unary ``+ - not``;
- boolean ``and`` / ``or`` and comparisons ``== != < <= > >=`` (so a formula can branch);
- the ternary ``a if cond else b``;
- calls to a fixed set of safe numeric functions only: ``min max floor ceil clamp``.

What is forbidden — and rejected at *compile* time, before any value is computed:
attribute access (``x.__class__``), subscripting (``x[0]``), lambdas, comprehensions,
assignment/walrus, f-strings, starred/keyword call args, any call whose target is not one of
the named safe functions, and any bare reference to a safe-function name as a value. Names not
present in the namespace raise at evaluation. Three residual size guards bound the one way
arithmetic can still hurt you — resource exhaustion: the ``**`` exponent is capped, integer
results are capped by bit-length, and non-finite floats are rejected; the AST node count and
source length are capped so a deeply nested expression cannot exhaust the stack.

Pure and dependency-free (stdlib ``ast`` + ``math`` + ``operator``), deterministic, and
trivially offline-testable — including its reject-list, which is the point.
"""
from __future__ import annotations

import ast
import math
import operator
from typing import Any, Mapping


class ExprError(ValueError):
    """Raised for any expression that is malformed, uses a forbidden construct, references
    an unknown name, or would produce an out-of-bounds value. A single error type so callers
    (authoring validation, the loader) can treat "bad formula" uniformly."""


# --- limits (the residual resource-exhaustion guards) -----------------------

DEFAULT_MAX_POW = 64        # cap the ** exponent magnitude
DEFAULT_MAX_BITS = 1024     # cap the bit-length of any integer value that flows through
DEFAULT_MAX_NODES = 256     # cap AST size (bounds recursion depth and total work)
DEFAULT_MAX_LEN = 512       # cap source length before we even parse


# --- the safe surface: operators and functions ------------------------------

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    # ast.Pow is handled specially (exponent guard) in _eval.
}

_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Not: operator.not_,
}

_COMPARE = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def _clamp(x, lo, hi):
    """Constrain ``x`` to ``[lo, hi]`` — the derivation form of "current, capped"."""
    return max(lo, min(hi, x))


SAFE_FUNCS: dict[str, Any] = {
    "min": min,
    "max": max,
    "floor": lambda x: math.floor(x),
    "ceil": lambda x: math.ceil(x),
    "clamp": _clamp,
}

# (min_args, max_args); max_args None means variadic. Enforced at compile time so a wrong
# call arity fails to author, not at some later evaluation with unlucky data.
_FUNC_ARITY: dict[str, tuple[int, Any]] = {
    "min": (1, None),
    "max": (1, None),
    "floor": (1, 1),
    "ceil": (1, 1),
    "clamp": (3, 3),
}


class Expr:
    """A compiled, sandboxed arithmetic expression: validated once, evaluated many times
    against different namespaces (the pool max recomputed every load, every level-up, every
    equip). ``names`` is the frozenset of free variable names it references — the caller must
    supply each in the namespace at evaluation. Immutable and safe to cache/share."""

    __slots__ = ("source", "names", "_body", "_max_pow", "_max_bits")

    def __init__(self, source: str, *, max_pow: int = DEFAULT_MAX_POW,
                 max_bits: int = DEFAULT_MAX_BITS, max_nodes: int = DEFAULT_MAX_NODES,
                 max_len: int = DEFAULT_MAX_LEN):
        if not isinstance(source, str):
            raise ExprError(f"expression must be a string, got {type(source).__name__}")
        if len(source) > max_len:
            raise ExprError(f"expression too long ({len(source)} > {max_len} chars)")
        try:
            tree = ast.parse(source, mode="eval")
        except SyntaxError as e:
            raise ExprError(f"could not parse {source!r}: {e}") from e
        names: set[str] = set()
        self._validate(tree.body, names, [0], max_nodes)
        self.source = source
        self.names = frozenset(names)
        self._body = tree.body
        self._max_pow = max_pow
        self._max_bits = max_bits

    # --- compile-time whitelist walk ---------------------------------------

    @classmethod
    def _validate(cls, node: ast.AST, names: set[str], count: list[int],
                  max_nodes: int) -> None:
        """Recursively assert ``node`` is on the whitelist, collecting free variable names.
        Raises :class:`ExprError` on the first forbidden construct — so an unsafe expression
        never reaches :meth:`evaluate` at all. ``count`` is a one-cell mutable node counter."""
        count[0] += 1
        if count[0] > max_nodes:
            raise ExprError(f"expression too complex (> {max_nodes} nodes)")

        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or isinstance(node.value, (int, float)):
                return
            raise ExprError(f"only numeric literals are allowed, not {node.value!r}")

        if isinstance(node, ast.Name):
            if not isinstance(node.ctx, ast.Load):
                raise ExprError("names may only be read, not assigned")
            if node.id in SAFE_FUNCS:
                raise ExprError(f"{node.id!r} is a function and cannot be used as a value")
            names.add(node.id)
            return

        if isinstance(node, ast.BinOp):
            if type(node.op) not in _BINOPS and not isinstance(node.op, ast.Pow):
                raise ExprError(f"operator {type(node.op).__name__} is not allowed")
            cls._validate(node.left, names, count, max_nodes)
            cls._validate(node.right, names, count, max_nodes)
            return

        if isinstance(node, ast.UnaryOp):
            if type(node.op) not in _UNARYOPS:
                raise ExprError(f"unary {type(node.op).__name__} is not allowed")
            cls._validate(node.operand, names, count, max_nodes)
            return

        if isinstance(node, ast.BoolOp):
            for v in node.values:
                cls._validate(v, names, count, max_nodes)
            return

        if isinstance(node, ast.Compare):
            for op in node.ops:
                if type(op) not in _COMPARE:
                    raise ExprError(f"comparison {type(op).__name__} is not allowed")
            cls._validate(node.left, names, count, max_nodes)
            for c in node.comparators:
                cls._validate(c, names, count, max_nodes)
            return

        if isinstance(node, ast.IfExp):
            cls._validate(node.test, names, count, max_nodes)
            cls._validate(node.body, names, count, max_nodes)
            cls._validate(node.orelse, names, count, max_nodes)
            return

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in SAFE_FUNCS:
                raise ExprError("only min/max/floor/ceil/clamp may be called")
            if node.keywords:
                raise ExprError("keyword arguments are not allowed")
            lo, hi = _FUNC_ARITY[node.func.id]
            n = len(node.args)
            if any(isinstance(a, ast.Starred) for a in node.args):
                raise ExprError("argument unpacking is not allowed")
            if n < lo or (hi is not None and n > hi):
                want = f"{lo}" if lo == hi else (f">= {lo}" if hi is None else f"{lo}..{hi}")
                raise ExprError(f"{node.func.id}() takes {want} arguments, got {n}")
            for a in node.args:
                cls._validate(a, names, count, max_nodes)
            return

        raise ExprError(f"{type(node).__name__} is not allowed in an expression")

    # --- evaluation --------------------------------------------------------

    def evaluate(self, namespace: Mapping[str, Any] | None = None) -> Any:
        """Compute the value against ``namespace`` (name → number). Every free name in
        :attr:`names` must be present and numeric; a missing or non-numeric name raises
        :class:`ExprError`, as does any size-guard breach. Returns an int / float / bool."""
        ns = namespace or {}
        try:
            return self._eval(self._body, ns)
        except ExprError:
            raise
        except ZeroDivisionError as e:
            raise ExprError(f"division by zero in {self.source!r}") from e
        except (OverflowError, ValueError, TypeError) as e:
            raise ExprError(f"could not evaluate {self.source!r}: {e}") from e
        except RecursionError as e:  # defence in depth; node cap should preclude this
            raise ExprError(f"expression too deeply nested: {self.source!r}") from e

    def _guard(self, value: Any) -> Any:
        """Bound the one way pure arithmetic can still hurt: reject over-large integers
        (nested ``**`` growth), non-finite floats, and non-numeric results."""
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            if value.bit_length() > self._max_bits:
                raise ExprError("number too large")
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ExprError("result is not a finite number")
            return value
        raise ExprError(f"expression produced a non-numeric value: {value!r}")

    def _eval(self, node: ast.AST, ns: Mapping[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value

        if isinstance(node, ast.Name):
            if node.id not in ns:
                raise ExprError(f"unknown name {node.id!r}")
            val = ns[node.id]
            if isinstance(val, bool) or isinstance(val, (int, float)):
                return val
            raise ExprError(f"name {node.id!r} is not a number: {val!r}")

        if isinstance(node, ast.BinOp):
            left = self._eval(node.left, ns)
            right = self._eval(node.right, ns)
            if isinstance(node.op, ast.Pow):
                if abs(right) > self._max_pow:
                    raise ExprError(f"exponent too large ({right})")
                return self._guard(left ** right)
            return self._guard(_BINOPS[type(node.op)](left, right))

        if isinstance(node, ast.UnaryOp):
            return self._guard(_UNARYOPS[type(node.op)](self._eval(node.operand, ns)))

        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result: Any = True
                for v in node.values:
                    result = self._eval(v, ns)
                    if not result:
                        return result
                return result
            result = False
            for v in node.values:
                result = self._eval(v, ns)
                if result:
                    return result
            return result

        if isinstance(node, ast.Compare):
            left = self._eval(node.left, ns)
            for op, comparator in zip(node.ops, node.comparators):
                right = self._eval(comparator, ns)
                if not _COMPARE[type(op)](left, right):
                    return False
                left = right
            return True

        if isinstance(node, ast.IfExp):
            branch = node.body if self._eval(node.test, ns) else node.orelse
            return self._eval(branch, ns)

        if isinstance(node, ast.Call):
            args = [self._eval(a, ns) for a in node.args]
            return self._guard(SAFE_FUNCS[node.func.id](*args))

        # Unreachable: _validate rejected everything else at compile time.
        raise ExprError(f"{type(node).__name__} is not allowed in an expression")

    def __repr__(self) -> str:
        return f"Expr({self.source!r})"


def compile_expr(source: str, **kw: Any) -> Expr:
    """Compile a derivation string to a reusable :class:`Expr` (validate now, evaluate
    later). Raises :class:`ExprError` if the source is unsafe or malformed."""
    return Expr(source, **kw)


def evaluate(source: str, namespace: Mapping[str, Any] | None = None, **kw: Any) -> Any:
    """One-shot convenience: compile ``source`` and evaluate it against ``namespace``.
    Prefer :func:`compile_expr` when the same formula is evaluated repeatedly."""
    return Expr(source, **kw).evaluate(namespace)
