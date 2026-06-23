# -*- coding: utf-8 -*-
from sympy import Integral, latex, Function
from sympy import pretty as xpretty
from sympy.vector import CoordSys3D, Vector, express
from sympy.abc import a, b, c






























def upretty(expr):
v.append(N.j - (Integral(f(b)) - C.x**2)*N.k)
upretty_v_8 = u(
"""\
N_j + ⎛   2   ⌠        ⎞ N_k\n\
      ⎜C_x  - ⎮ f(b) db⎟    \n\
      ⎝       ⌡        ⎠    \
""")
pretty_v_8 = u(









def upretty(expr):
v.append((a**2 + b)*N.i + (Integral(f(b)))*N.k)
upretty_v_11 = u(
"""\
⎛ 2    ⎞ N_i + ⎛⌠        ⎞ N_k\n\
⎝a  + b⎠       ⎜⎮ f(b) db⎟    \n\
               ⎝⌡        ⎠    \
""")
pretty_v_11 = u(





















def upretty(expr):
# This is the pretty form for ((a**2 + b)*N.i + 3*(C.y - c)*N.k) | N.k
upretty_d_7 = u(
"""\
⎛ 2    ⎞ (N_i|N_k) + (3⋅C_y - 3⋅c) (N_k|N_k)\n\
⎝a  + b⎠                                    \
""")
pretty_d_7 = u(
"""\
















































def test_pretty_print_unicode():
    assert upretty(d[10]) == u'(cos(a)) (C_i|N_k) + (-sin(a)) (C_j|N_k)'


def test_latex_printing():
    assert latex(v[0]) == '\\mathbf{\\hat{0}}'
    assert latex(v[1]) == '\\mathbf{\\hat{i}_{N}}'
