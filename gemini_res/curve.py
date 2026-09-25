"""Concentration from optical density and calibrators — Gemini's own arithmetic.

Everything else is in the ``.res`` file; the quantitative results are not. The
IFU says the calculation is performed again when the plate is opened (p. 4-67).

**What is computed is in ``DataAnalysis.dll``** and was read out of the machine
code, not guessed. The class is ``CSigmoid``, and its methods say it all:

| method | RVA | what follows from it |
|---|---|---|
| ``CalculateY`` | 0x0ba360 | ``y = p3 + (p0 - p3) / (1 + (x/p2)**p1)`` — the **four-parameter logistic**, in the order a, b, c, d |
| ``InitialiseParameters`` | 0x0ba5c0 | ``a = y[0]``, ``b = 1``, ``c = (x[0] + x[n-1]) / 2``, ``d = y[n-1]`` |
| ``CalculatePartialDerivatives`` | 0x0ba410 | the derivatives **analytically**, not numerically |
| ``CheckParameters`` | 0x0ba740 | if ``c <= 0`` then ``c = c_previous / 2``; no other bound |
| ``Fit`` | 0x0b97a0 | ``mrqmin`` from *Numerical Recipes* — the helper is literally called ``mrqcof`` there, the solver is ``CMatrix::GaussJordan`` |
| ``CDataModel::Sort`` | 0x042280 | the points are sorted **before** the fit |

From ``Fit`` itself: initial λ = 0.001; on improvement λ times 0.1, otherwise
times 10; the diagonal is **multiplied** (``alpha[j][j] * (1 + λ)``); a **fixed
number of iterations** is computed, and every iteration counts — including one
that is rejected. The convergence test ("improvement < 0.001") only sets a flag
for the return value and **does not leave the loop**.

**The iteration count** is stored in ``CQuantitativeSettings`` (the constructor
defaults to 20, ``SetIterations`` overrides it). Every file examined carries 100,
and that is what reproduces the runs — see :data:`ITERATIONS`.

**How closely this matches** — against Gemini's own export of the same plate:

| run | assay | median deviation | parameters |
|---|---|---|---|
| …M27LBR | 3204 Cardiolipin IgM 27 °C | 0.007 ‰ | identical to four decimals |
| …M32LBR | 3204 Cardiolipin IgM 32 °C | 0.009 ‰ | identical to four decimals |
| …MRTLBR | 3204 Cardiolipin IgM V2 | 0.008 ‰ | |
| …G1RH | 3401 a-TPO | 0.006 ‰ | |
| …GM1RH | 3208 Inositol (first half) | 0.05 ‰ | |

Per mille, not per cent: the arithmetic is reproduced, not approximated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: Initial λ and its steps — the floating-point constants out of ``CSigmoid::Fit``
#: (0.001 at 0x100e7ee0, 10.0 at 0x100e9fd0, 0.1 at 0x100e7ef8).
LAMBDA_START = 0.001
LAMBDA_UP = 10.0
LAMBDA_DOWN = 0.1

#: Fixed number of iterations. ``CSigmoid::CSigmoid`` sets 20, the assays
#: examined carry 100 in ``CQuantitativeSettings`` — and only with 100 do the
#: runs match whose curve does not reach saturation. Where the fit settles
#: earlier, further iterations change nothing.
ITERATIONS = 100

#: The share of the upper asymptote the highest standard has to reach for the
#: curve to count as **statistically** determined (see
#: :attr:`Curve.well_determined`). It no longer bears on agreement with Gemini.
SATURATION_AT_LEAST = 0.35


class NoCurve(Exception):
    """No curve can be fitted through these calibrators."""


def four_pl(x: float, p) -> float:
    """``CSigmoid::CalculateY``: OD as a function of concentration.

        OD(x) = d + (a - d) / (1 + (x / c) ** b)
    """
    a, b, c, d = p
    if x <= 0:
        return a
    # **Do not rely on ``try``**: a negative ``c`` makes the base negative, and
    # Python then carries on in the *complex* domain instead of failing —
    # ``(-2.0) ** 0.7`` yields a complex number that only surfaces in
    # ``math.isfinite`` ("must be real number, not complex").
    if c <= 0:
        return float("nan")
    try:
        return d + (a - d) / (1.0 + (x / c) ** b)
    except (OverflowError, ValueError, ZeroDivisionError):
        return float("nan")


def _derivatives(x: float, p) -> list[float]:
    """``CSigmoid::CalculatePartialDerivatives`` — analytic, by a, b, c, d.

    With ``u = (x/c)**b`` and ``D = 1 + u``:
    ``dy/da = 1/D``, ``dy/dd = u/D``,
    ``dy/db = -(a-d) * u * ln(x/c) / D**2``, ``dy/dc = (a-d) * b * u / (c * D**2)``.
    At the zero standard ``u = 0``; there only ``a`` is tied to the curve.
    """
    a, b, c, d = p
    if x <= 0 or c <= 0:
        return [1.0, 0.0, 0.0, 0.0]
    try:
        u = (x / c) ** b
    except (OverflowError, ValueError):
        return [0.0, 0.0, 0.0, 0.0]
    denom = 1.0 + u
    if not math.isfinite(u) or denom == 0:
        return [0.0, 0.0, 0.0, 0.0]
    try:
        return [
            1.0 / denom,
            -(a - d) * u * math.log(x / c) / (denom * denom),
            (a - d) * b * u / (c * denom * denom),
            u / denom,
        ]
    except (OverflowError, ValueError, ZeroDivisionError):
        return [0.0, 0.0, 0.0, 0.0]


def _mrqcof(points, p):
    """``CSigmoid::mrqcof`` — normal equations and sum of squares, unweighted."""
    alpha = [[0.0] * 4 for _ in range(4)]
    beta = [0.0] * 4
    chi = 0.0
    for x, y in points:
        model = four_pl(x, p)
        if not math.isfinite(model):
            return None, None, float("inf")
        residual = y - model
        g = _derivatives(x, p)
        for j in range(4):
            for k in range(j, 4):
                alpha[j][k] += g[j] * g[k]
            beta[j] += residual * g[j]
        chi += residual * residual
    for j in range(4):
        for k in range(j):
            alpha[j][k] = alpha[k][j]
    return alpha, beta, chi


def _gauss_jordan(A, b):
    """``CMatrix::GaussJordan`` — four unknowns, with column pivoting."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for k in range(n):
        p = max(range(k, n), key=lambda i: abs(M[i][k]))
        if abs(M[p][k]) < 1e-300:
            raise NoCurve("the system of equations is singular")
        M[k], M[p] = M[p], M[k]
        for i in range(n):
            if i == k:
                continue
            f = M[i][k] / M[k][k]
            for j in range(k, n + 1):
                M[i][j] -= f * M[k][j]
    return [M[i][n] / M[i][i] for i in range(n)]


def _fit(points, iterations=ITERATIONS):
    """``CSigmoid::Fit`` — mrqmin with a fixed iteration count.

    The points come in sorted (``CDataModel::Sort``), because the initial values
    are taken from the first and the last one.
    """
    (x_first, y_first), (x_last, y_last) = points[0], points[-1]
    p = [y_first, 1.0, (x_first + x_last) / 2.0, y_last]
    lam = LAMBDA_START
    alpha, beta, chi = _mrqcof(points, p)
    if alpha is None:
        raise NoCurve("the initial values are unusable")
    for _ in range(iterations):
        covar = [row[:] for row in alpha]
        for j in range(4):
            # **Multiplicative**, as in the machine code (``(1 + λ) * alpha[j][j]``)
            # and as in Numerical Recipes — not additive.
            covar[j][j] = alpha[j][j] * (1.0 + lam)
        try:
            step = _gauss_jordan(covar, beta)
        except NoCurve:
            lam *= LAMBDA_UP
            continue
        candidate = [p[i] + step[i] for i in range(4)]
        if candidate[2] <= 0:  # CheckParameters
            candidate[2] = p[2] / 2.0
        alpha_new, beta_new, chi_new = _mrqcof(points, candidate)
        if chi_new < chi:
            lam *= LAMBDA_DOWN
            p, chi, alpha, beta = candidate, chi_new, alpha_new, beta_new
        else:
            lam *= LAMBDA_UP
    return p, chi


@dataclass
class Curve:
    """The calibration curve of one run."""

    parameters: tuple
    sum_of_squares: float
    #: (concentration, OD) of the calibrator wells it was fitted through
    points: list = field(default_factory=list)

    @property
    def bottom(self) -> float:
        return self.parameters[0]

    @property
    def top(self) -> float:
        return self.parameters[3]

    @property
    def well_determined(self) -> bool:
        """Is the curve **statistically** pinned down by the calibrators?

        If the highest standard does not reach saturation, the logistic describes
        little more than a nearly straight branch there: ``c`` and ``d`` can be
        pushed upwards together without the sum of squares rising appreciably.

        **This is not a reservation about the computation** — Gemini arrives at
        the same numbers, to the rounding. It remains a statement about the
        **run**: whoever judges the standard series should know that the curve is
        not uniquely determined there.
        """
        if not self.points or not math.isfinite(self.top) or self.top <= 0:
            return False
        return max(od for _, od in self.points) / self.top >= SATURATION_AT_LEAST

    def concentration(self, od: float):
        """The curve inverted (``CSigmoid::YToX``) — concentration for an OD.

        ``None`` when the OD lies outside the curve; Gemini writes ``*****`` there.
        """
        a, b, c, d = self.parameters
        if od is None or not math.isfinite(od):
            return None
        if od <= a:
            return 0.0
        if od >= d:
            return None
        try:
            v = (a - d) / (od - d) - 1.0
            if v <= 0:
                return None
            return c * v ** (1.0 / b)
        except (OverflowError, ValueError, ZeroDivisionError):
            return None


def curve_from(calibrators, iterations=ITERATIONS) -> Curve:
    """The curve through (concentration, OD) of the **single** calibrator wells.

    Duplicate determinations go in one by one, not as a mean — and sorted, the way
    ``CDataModel::Sort`` does it, because the initial values hang on the first and
    the last point.
    """
    points = sorted(
        (float(k), float(o))
        for k, o in calibrators
        if k is not None and o is not None and math.isfinite(o)
    )
    if len(points) < 4:
        raise NoCurve("fewer than four calibrator values")
    if not any(k > 0 for k, _ in points):
        raise NoCurve("nothing but the zero calibrator")
    p, chi = _fit(points, iterations)
    if not all(math.isfinite(v) for v in p) or p[2] <= 0 or p[1] == 0:
        raise NoCurve("no usable fit")
    return Curve(parameters=tuple(p), sum_of_squares=chi, points=points)
