from sympy import I, S, exp, sin, symbols, trigsimp

x = symbols("x")


def test_trigsimp_basic():
    assert trigsimp(sin(x)) == sin(x)
