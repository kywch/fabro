def print_poly(poly):
    """Return a compact LaTeX representation for a univariate polynomial."""
    terms = []
    for coeff, power in poly.terms():
        if power == 0:
            terms.append(str(coeff))
        elif power == 1:
            terms.append(f"{coeff} x")
        else:
            terms.append(f"{coeff} x^{power}")
    return " + ".join(terms)
