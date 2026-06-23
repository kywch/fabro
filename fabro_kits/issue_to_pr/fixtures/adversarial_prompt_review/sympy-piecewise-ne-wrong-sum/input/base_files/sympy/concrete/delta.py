










from sympy.core.cache import cacheit
from sympy.core.compatibility import default_sort_key, range
from sympy.functions import KroneckerDelta, Piecewise, piecewise_fold
from sympy.sets import Interval


@cacheit
def _expand_delta(expr, index):
    """
