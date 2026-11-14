"""Radiation model for journey-to-work OD estimation from a zone-to-zone impedance matrix.

This is a vectorised version for many zones (e.g. a few thousand H3 cells). It
differs from ``transport_flow_model.RadiationModel`` in three ways:

- origin mass, destination mass and trip production are separate inputs
  (employed residents produce the trips, jobs attract them);
- the intervening opportunities ``s_ij`` are the opportunities closer to ``i``
  than ``j`` is (Simini et al. 2012), not all opportunities within a fixed
  threshold;
- it takes a precomputed impedance matrix (e.g. free-flow road time), so the
  network shortest paths are computed once, outside this module.

    P_ij = m_i * n_j / ((m_i + s_ij) * (m_i + n_j + s_ij))
    T_ij = T_i * P_ij   (then optionally renormalised per origin)

where ``m_i`` is the origin mass, ``n_j`` the destination mass and ``s_ij`` the
total destination mass of zones ``k`` (not ``i`` or ``j``) with
``d_ik <= d_ij``, in the same units as ``m``.
"""

from __future__ import annotations

import numpy as np

NORMALISATIONS = ("finite", "row", "none")


def radiation_probabilities(
    impedance: np.ndarray,
    origin_mass: np.ndarray,
    destination_mass: np.ndarray,
    *,
    max_impedance: float = np.inf,
    normalisation: str = "row",
) -> np.ndarray:
    """Return a dense Z x Z matrix of trip probabilities P_ij.

    Parameters
    ----------
    impedance
        Z x Z matrix of travel impedance (time or distance) from zone i to zone j.
        ``inf`` (or NaN) means unreachable. The diagonal is ignored.
    origin_mass
        Length-Z array ``m_i``, in the same units as ``destination_mass``.
    destination_mass
        Length-Z array ``n_j`` (opportunities, e.g. jobs).
    max_impedance
        Destinations beyond this are given zero probability. They still count
        as intervening opportunities for further destinations, but those are
        also beyond the threshold, so this does not matter.
    normalisation
        ``"finite"``: multiply by 1 / (1 - m_i / M) (Simini's finite-size correction).
        ``"row"``: rescale each row to sum to 1 over the destinations it can reach.
        ``"none"``: the raw formula.
    """
    if normalisation not in NORMALISATIONS:
        raise ValueError(f"normalisation must be one of {NORMALISATIONS}, got {normalisation!r}")
    d = np.array(impedance, dtype=float)
    m = np.asarray(origin_mass, dtype=float)
    n = np.asarray(destination_mass, dtype=float)
    z = d.shape[0]
    if d.shape != (z, z) or m.shape != (z,) or n.shape != (z,):
        raise ValueError("impedance must be Z x Z and masses length Z")
    if (m < 0).any() or (n < 0).any():
        raise ValueError("masses must be non-negative")

    d[np.isnan(d)] = np.inf
    np.fill_diagonal(d, np.inf)  # j == i is never a destination
    probabilities = np.zeros((z, z))

    for i in range(z):
        row = d[i]
        order = np.argsort(row, kind="stable")
        sorted_d = row[order]
        cumulative = np.cumsum(n[order])
        # Destination mass of every zone k with d_ik <= d_ij (ties count as
        # intervening, as they lie on the circle), minus j itself.
        # The origin is on the diagonal (inf) so it is excluded automatically.
        within = cumulative[np.searchsorted(sorted_d, sorted_d, side="right") - 1]
        s = within - n[order]

        reachable = np.isfinite(sorted_d) & (sorted_d <= max_impedance)
        numerator = m[i] * n[order]
        denominator = (m[i] + s) * (m[i] + n[order] + s)
        with np.errstate(divide="ignore", invalid="ignore"):
            p = np.where(reachable & (denominator > 0), numerator / denominator, 0.0)
        probabilities[i, order] = p

    if normalisation == "finite":
        total = n.sum()
        with np.errstate(divide="ignore", invalid="ignore"):
            factor = np.where(total - m > 0, total / (total - m), 0.0)
        probabilities *= factor[:, None]
    elif normalisation == "row":
        sums = probabilities.sum(axis=1, keepdims=True)
        with np.errstate(divide="ignore", invalid="ignore"):
            probabilities = np.where(sums > 0, probabilities / sums, 0.0)
    return probabilities


def radiation_trips(
    impedance: np.ndarray,
    trip_production: np.ndarray,
    origin_mass: np.ndarray,
    destination_mass: np.ndarray,
    **kwargs,
) -> np.ndarray:
    """Return a Z x Z matrix of trips T_ij = T_i * P_ij (see ``radiation_probabilities``)."""
    p = radiation_probabilities(impedance, origin_mass, destination_mass, **kwargs)
    return np.asarray(trip_production, dtype=float)[:, None] * p


def prune_small_flows(trips: np.ndarray, min_trips: float) -> np.ndarray:
    """Zero out OD cells below ``min_trips`` and spread each origin's dropped
    trips back over its remaining cells, so that origin totals are kept."""
    trips = np.asarray(trips, dtype=float)
    totals = trips.sum(axis=1, keepdims=True)
    kept = np.where(trips >= min_trips, trips, 0.0)
    kept_totals = kept.sum(axis=1, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.where(kept_totals > 0, totals / kept_totals, 0.0)
    return kept * scale
