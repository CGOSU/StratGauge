"""Small allow-listed expression language; never executes Python source."""

from __future__ import annotations

import ast as python_ast
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from operator import add, eq, ge, gt, le, lt, mul, ne, sub, truediv


class StrategyExpressionError(ValueError):
    """The strategy expression is invalid or cannot be evaluated."""


type Features = Mapping[str, Decimal]
type ExpressionValue = Decimal | bool


class Expression:
    """Compiled expression with an explicit evaluation boundary."""

    def evaluate(self, features: Features) -> ExpressionValue:
        raise NotImplementedError


@dataclass(frozen=True)
class NumberLiteral(Expression):
    value: Decimal

    def evaluate(self, features: Features) -> Decimal:
        return self.value


@dataclass(frozen=True)
class FeatureReference(Expression):
    name: str

    def evaluate(self, features: Features) -> Decimal:
        try:
            value = features[self.name]
        except KeyError as exc:
            raise StrategyExpressionError(f"unknown or unavailable feature: {self.name}") from exc
        if not isinstance(value, Decimal):
            raise StrategyExpressionError(f"feature {self.name} must be Decimal")
        return value


@dataclass(frozen=True)
class UnaryExpression(Expression):
    operator: str
    operand: Expression

    def evaluate(self, features: Features) -> Decimal:
        value = _decimal_value(self.operand.evaluate(features))
        if self.operator == "+":
            return value
        if self.operator == "-":
            return -value
        raise StrategyExpressionError(f"unsupported unary operator: {self.operator}")


@dataclass(frozen=True)
class BinaryExpression(Expression):
    operator: str
    left: Expression
    right: Expression

    def evaluate(self, features: Features) -> Decimal:
        left = _decimal_value(self.left.evaluate(features))
        right = _decimal_value(self.right.evaluate(features))
        operations: dict[str, Callable[[Decimal, Decimal], Decimal]] = {
            "+": add,
            "-": sub,
            "*": mul,
            "/": truediv,
        }
        operation = operations.get(self.operator)
        if operation is None:
            raise StrategyExpressionError(f"unsupported binary operator: {self.operator}")
        if self.operator == "/" and right == 0:
            raise StrategyExpressionError("division by zero")
        try:
            return operation(left, right)
        except (InvalidOperation, ZeroDivisionError) as exc:
            raise StrategyExpressionError("invalid decimal operation") from exc


@dataclass(frozen=True)
class ComparisonExpression(Expression):
    operator: str
    left: Expression
    right: Expression

    def evaluate(self, features: Features) -> bool:
        left = _decimal_value(self.left.evaluate(features))
        right = _decimal_value(self.right.evaluate(features))
        operations: dict[str, Callable[[Decimal, Decimal], bool]] = {
            ">": gt,
            ">=": ge,
            "<": lt,
            "<=": le,
            "==": eq,
            "!=": ne,
        }
        operation = operations.get(self.operator)
        if operation is None:
            raise StrategyExpressionError(f"unsupported comparison operator: {self.operator}")
        return operation(left, right)


@dataclass(frozen=True)
class BooleanExpression(Expression):
    operator: str
    operands: tuple[Expression, ...]

    def evaluate(self, features: Features) -> bool:
        values = [bool(operand.evaluate(features)) for operand in self.operands]
        if self.operator == "and":
            return all(values)
        if self.operator == "or":
            return any(values)
        raise StrategyExpressionError(f"unsupported boolean operator: {self.operator}")


@dataclass(frozen=True)
class FunctionExpression(Expression):
    name: str
    arguments: tuple[Expression, ...]

    def evaluate(self, features: Features) -> Decimal:
        values = tuple(_decimal_value(argument.evaluate(features)) for argument in self.arguments)
        if self.name in {"min", "max"}:
            if not values:
                raise StrategyExpressionError(f"{self.name} requires at least one argument")
            return min(values) if self.name == "min" else max(values)
        if self.name == "abs" and len(values) == 1:
            return abs(values[0])
        raise StrategyExpressionError(f"unsupported function or arity: {self.name}")


def parse_expression(source: str) -> Expression:
    """Parse only the documented expression subset into our own AST."""

    try:
        tree = python_ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise StrategyExpressionError("invalid strategy expression syntax") from exc
    return _compile(tree.body)


def evaluate_expression(source: str, features: Features) -> ExpressionValue:
    return parse_expression(source).evaluate(features)


def _compile(node: python_ast.AST) -> Expression:
    if isinstance(node, python_ast.Constant) and isinstance(node.value, (int, float)):
        return NumberLiteral(_to_decimal(node.value))
    if isinstance(node, python_ast.Name):
        return FeatureReference(node.id)
    if isinstance(node, python_ast.UnaryOp) and isinstance(
        node.op, (python_ast.UAdd, python_ast.USub)
    ):
        return UnaryExpression(
            "+" if isinstance(node.op, python_ast.UAdd) else "-", _compile(node.operand)
        )
    if isinstance(node, python_ast.BinOp) and isinstance(
        node.op, (python_ast.Add, python_ast.Sub, python_ast.Mult, python_ast.Div)
    ):
        operator = {
            python_ast.Add: "+",
            python_ast.Sub: "-",
            python_ast.Mult: "*",
            python_ast.Div: "/",
        }[type(node.op)]
        return BinaryExpression(operator, _compile(node.left), _compile(node.right))
    if isinstance(node, python_ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
        comparison_operator = {
            python_ast.Gt: ">",
            python_ast.GtE: ">=",
            python_ast.Lt: "<",
            python_ast.LtE: "<=",
            python_ast.Eq: "==",
            python_ast.NotEq: "!=",
        }.get(type(node.ops[0]))
        if comparison_operator is not None:
            return ComparisonExpression(
                comparison_operator, _compile(node.left), _compile(node.comparators[0])
            )
    if isinstance(node, python_ast.BoolOp) and isinstance(node.op, (python_ast.And, python_ast.Or)):
        if not node.values:
            raise StrategyExpressionError("boolean expression requires operands")
        operator = "and" if isinstance(node.op, python_ast.And) else "or"
        return BooleanExpression(operator, tuple(_compile(value) for value in node.values))
    if isinstance(node, python_ast.Call) and isinstance(node.func, python_ast.Name):
        if node.keywords or node.func.id not in {"min", "max", "abs"}:
            raise StrategyExpressionError("only min, max and abs positional calls are allowed")
        return FunctionExpression(node.func.id, tuple(_compile(argument) for argument in node.args))
    raise StrategyExpressionError(f"unsupported syntax: {type(node).__name__}")


def _to_decimal(value: int | float) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise StrategyExpressionError("numeric literal is not finite") from exc
    if not result.is_finite():
        raise StrategyExpressionError("numeric literal is not finite")
    return result


def _decimal_value(value: ExpressionValue) -> Decimal:
    if isinstance(value, bool):
        raise StrategyExpressionError("boolean cannot be used as a numeric value")
    return value
