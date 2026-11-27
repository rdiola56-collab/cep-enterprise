"""Controlled formula language: tokenizer and recursive-descent parser.

User formulas are never passed to ``eval``/``exec`` or Python's ``ast``. They
are tokenized against a fixed grammar and parsed into a small immutable AST:

    expr    := term (('+' | '-') term)*
    term    := unary (('*' | '/') unary)*
    unary   := ('-' | '+') unary | power
    power   := atom ('^' number)?
    atom    := NUMBER | IDENT | IDENT '(' args ')' | '(' expr ')'

Only whitelisted functions are callable. Exponents must be integer literals so
that dimensional analysis stays exact. Size and depth limits guard against
pathological input.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

MAX_FORMULA_LENGTH = 2000
MAX_NODES = 500
MAX_DEPTH = 40
MAX_EXPONENT = 6

ALLOWED_FUNCTIONS: dict[str, tuple[int, int]] = {
    # name: (min args, max args)
    "ceil": (1, 1),
    "floor": (1, 1),
    "round": (1, 2),
    "abs": (1, 1),
    "min": (2, 20),
    "max": (2, 20),
    "sqrt": (1, 1),
}

# Estimators write formulas with typographic operators; normalise them.
_OPERATOR_ALIASES = {"×": "*", "·": "*", "÷": "/", "−": "-", "–": "-"}


class FormulaSyntaxError(ValueError):
    def __init__(self, message: str, position: int | None = None):
        super().__init__(message if position is None else f"{message} (at position {position})")
        self.position = position


# ---------------------------------------------------------------- AST


@dataclass(frozen=True, slots=True)
class Num:
    value: Decimal


@dataclass(frozen=True, slots=True)
class Var:
    name: str


@dataclass(frozen=True, slots=True)
class UnaryOp:
    op: str
    operand: "Node"


@dataclass(frozen=True, slots=True)
class BinOp:
    op: str
    left: "Node"
    right: "Node"


@dataclass(frozen=True, slots=True)
class Pow:
    base: "Node"
    exponent: int


@dataclass(frozen=True, slots=True)
class Call:
    func: str
    args: tuple["Node", ...]


Node = Num | Var | UnaryOp | BinOp | Pow | Call


# ---------------------------------------------------------------- tokenizer


@dataclass(frozen=True, slots=True)
class Token:
    kind: str  # NUM, IDENT, OP, LPAREN, RPAREN, COMMA, END
    text: str
    pos: int


def tokenize(source: str) -> list[Token]:
    if len(source) > MAX_FORMULA_LENGTH:
        raise FormulaSyntaxError(f"formula longer than {MAX_FORMULA_LENGTH} characters")
    for alias, op in _OPERATOR_ALIASES.items():
        source = source.replace(alias, op)
    tokens: list[Token] = []
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        if c.isspace():
            i += 1
        elif c.isdigit() or (c == "." and i + 1 < n and source[i + 1].isdigit()):
            start = i
            while i < n and (source[i].isdigit() or source[i] == "."):
                i += 1
            text = source[start:i]
            if text.count(".") > 1:
                raise FormulaSyntaxError(f"malformed number '{text}'", start)
            tokens.append(Token("NUM", text, start))
        elif c.isalpha() or c == "_":
            start = i
            while i < n and (source[i].isalnum() or source[i] == "_"):
                i += 1
            tokens.append(Token("IDENT", source[start:i], start))
        elif c in "+-*/^":
            tokens.append(Token("OP", c, i))
            i += 1
        elif c == "(":
            tokens.append(Token("LPAREN", c, i))
            i += 1
        elif c == ")":
            tokens.append(Token("RPAREN", c, i))
            i += 1
        elif c == ",":
            tokens.append(Token("COMMA", c, i))
            i += 1
        else:
            raise FormulaSyntaxError(f"unexpected character '{c}'", i)
    tokens.append(Token("END", "", n))
    return tokens


# ---------------------------------------------------------------- parser


class _Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.i = 0
        self.nodes = 0
        self.depth = 0

    def peek(self) -> Token:
        return self.tokens[self.i]

    def take(self) -> Token:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def expect(self, kind: str) -> Token:
        tok = self.take()
        if tok.kind != kind:
            raise FormulaSyntaxError(f"expected {kind.lower()}, found '{tok.text or 'end of formula'}'", tok.pos)
        return tok

    def node(self, n: Node) -> Node:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise FormulaSyntaxError(f"formula has more than {MAX_NODES} elements")
        return n

    def enter(self, pos: int) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise FormulaSyntaxError(f"formula nested deeper than {MAX_DEPTH} levels", pos)

    def parse(self) -> Node:
        if self.peek().kind == "END":
            raise FormulaSyntaxError("formula is empty")
        result = self.expr()
        tok = self.peek()
        if tok.kind != "END":
            raise FormulaSyntaxError(f"unexpected '{tok.text}'", tok.pos)
        return result

    def expr(self) -> Node:
        left = self.term()
        while self.peek().kind == "OP" and self.peek().text in "+-":
            op = self.take().text
            left = self.node(BinOp(op, left, self.term()))
        return left

    def term(self) -> Node:
        left = self.unary()
        while self.peek().kind == "OP" and self.peek().text in "*/":
            op = self.take().text
            left = self.node(BinOp(op, left, self.unary()))
        return left

    def unary(self) -> Node:
        tok = self.peek()
        if tok.kind == "OP" and tok.text in "+-":
            self.take()
            self.enter(tok.pos)
            operand = self.unary()
            self.depth -= 1
            return operand if tok.text == "+" else self.node(UnaryOp("-", operand))
        return self.power()

    def power(self) -> Node:
        base = self.atom()
        if self.peek().kind == "OP" and self.peek().text == "^":
            caret = self.take()
            neg = False
            if self.peek().kind == "OP" and self.peek().text == "-":
                self.take()
                neg = True
            tok = self.expect("NUM")
            if "." in tok.text:
                raise FormulaSyntaxError("exponent must be a whole number", tok.pos)
            exp = int(tok.text)
            if exp > MAX_EXPONENT:
                raise FormulaSyntaxError(f"exponent larger than {MAX_EXPONENT}", caret.pos)
            return self.node(Pow(base, -exp if neg else exp))
        return base

    def atom(self) -> Node:
        tok = self.take()
        if tok.kind == "NUM":
            return self.node(Num(Decimal(tok.text)))
        if tok.kind == "IDENT":
            if self.peek().kind == "LPAREN":
                name = tok.text.lower()
                if name not in ALLOWED_FUNCTIONS:
                    raise FormulaSyntaxError(f"function '{tok.text}' is not allowed", tok.pos)
                self.take()
                self.enter(tok.pos)
                args = [self.expr()]
                while self.peek().kind == "COMMA":
                    self.take()
                    args.append(self.expr())
                self.expect("RPAREN")
                self.depth -= 1
                lo, hi = ALLOWED_FUNCTIONS[name]
                if not lo <= len(args) <= hi:
                    raise FormulaSyntaxError(f"{name}() takes {lo}–{hi} arguments, got {len(args)}", tok.pos)
                return self.node(Call(name, tuple(args)))
            return self.node(Var(tok.text))
        if tok.kind == "LPAREN":
            self.enter(tok.pos)
            inner = self.expr()
            self.expect("RPAREN")
            self.depth -= 1
            return inner
        raise FormulaSyntaxError(f"unexpected '{tok.text or 'end of formula'}'", tok.pos)


def parse(source: str) -> Node:
    return _Parser(tokenize(source)).parse()


def variables(node: Node) -> set[str]:
    match node:
        case Var(name):
            return {name}
        case Num():
            return set()
        case UnaryOp(_, operand):
            return variables(operand)
        case BinOp(_, left, right):
            return variables(left) | variables(right)
        case Pow(base, _):
            return variables(base)
        case Call(_, args):
            return set().union(*(variables(a) for a in args))
    raise TypeError(node)


def render(node: Node) -> str:
    """Canonical text form of a parsed formula (fully parenthesised where needed)."""
    match node:
        case Num(v):
            return format(v, "f")
        case Var(name):
            return name
        case UnaryOp(_, operand):
            return f"-{_wrap(operand)}"
        case BinOp(op, left, right):
            return f"{_wrap(left)} {op} {_wrap(right)}"
        case Pow(base, exp):
            return f"{_wrap(base)}^{exp}"
        case Call(func, args):
            return f"{func}({', '.join(render(a) for a in args)})"
    raise TypeError(node)


def _wrap(node: Node) -> str:
    return f"({render(node)})" if isinstance(node, (BinOp, UnaryOp)) else render(node)
