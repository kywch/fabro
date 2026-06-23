from sympy import KroneckerDelta, diff, Piecewise, And
from sympy import Sum

from sympy.core import S, symbols, Add, Mul
from sympy.functions import transpose, sin, cos, sqrt













































































def test_Identity():
    assert In.inverse() == In
    assert In.conjugate() == In

def test_Identity_doit():
    Inn = Identity(Add(n, n, evaluate=False))
    assert isinstance(Inn.rows, Add)
