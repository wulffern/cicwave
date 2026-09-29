"""Smith chart geometry and impedance maths; no cicwave or Qt needed."""

import numpy as np

#: Normalised resistances and reactances drawn as grid lines.
GRID_R = (0.0, 0.2, 0.5, 1.0, 2.0, 5.0)
GRID_X = (0.2, 0.5, 1.0, 2.0, 5.0)


def gamma_from_z(z, z0=50.0):
    """Reflection coefficient of impedance *z* (ohm) in a *z0* system."""
    z = np.asarray(z, dtype=complex)
    return (z - z0) / (z + z0)


def z_from_gamma(gamma, z0=50.0):
    """Impedance (ohm) whose reflection coefficient is *gamma*."""
    gamma = np.asarray(gamma, dtype=complex)
    return z0 * (1 + gamma) / (1 - gamma)


def grid_lines(n=400):
    """``[(label, gamma_points)]`` for the constant-r and constant-x lines.

    Every grid line is a straight line in the impedance plane, mapped
    through ``gamma = (z - 1) / (z + 1)``: constant r sweeps x, constant x
    sweeps r. Sweeping ``tan`` of an angle covers the infinite range with
    points spread evenly along the circle.
    """
    t = np.tan(np.linspace(-np.pi / 2, np.pi / 2, n)[1:-1])
    lines = []
    for r in GRID_R:
        lines.append(("r=%g" % r, gamma_from_z(r + 1j * t, 1.0)))
    rr = np.tan(np.linspace(0, np.pi / 2, n)[:-1])
    for x in GRID_X:
        for s in (1, -1):
            lines.append(("x=%g" % (s * x), gamma_from_z(rr + 1j * s * x, 1.0)))
    lines.append(("real axis", np.array([-1 + 0j, 1 + 0j])))
    return lines


def match_summary(freq, gamma, z0=50.0):
    """Figures at the best-matched frequency (smallest |gamma|)."""
    gamma = np.asarray(gamma, dtype=complex)
    i = int(np.argmin(np.abs(gamma)))
    g = gamma[i]
    mag = abs(g)
    return {
        "index": i,
        "frequency": float(freq[i]),
        "gamma": g,
        "z": complex(z_from_gamma(g, z0)),
        "return_loss_db": float(-20 * np.log10(mag)) if mag > 0 else np.inf,
        "vswr": float((1 + mag) / (1 - mag)) if mag < 1 else np.inf,
    }
