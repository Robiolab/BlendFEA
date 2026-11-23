"""GridIndex (numpy) must answer exactly like a brute-force search for fixed-radius
questions: it replaced scipy.spatial.KDTree in the matcher."""
import numpy as np
import pytest

from matching_library.spatial import GridIndex


def _brute_nearest(P, q, r):
    d = np.linalg.norm(P - q, axis=1)
    j = int(np.argmin(d))
    return (float(d[j]), j) if d[j] <= r else (np.inf, -1)


@pytest.mark.parametrize("radius", [1e-6, 0.03, 0.2, 5.0])
def test_matches_brute_force(radius):
    rng = np.random.default_rng(7)
    P = rng.uniform(-1, 1, (2000, 3))
    Q = np.vstack([rng.uniform(-1.2, 1.2, (300, 3)), P[:50] + 1e-9])   # include exact hits
    g = GridIndex(P, radius)

    dist, idx = g.nearest(Q)
    for n, q in enumerate(Q):
        bd, bi = _brute_nearest(P, q, radius)
        assert dist[n] == pytest.approx(bd) if np.isfinite(bd) else not np.isfinite(dist[n])
        if np.isfinite(bd):
            assert np.linalg.norm(P[idx[n]] - q) == pytest.approx(bd)

    balls = g.within(Q[:100])
    for q, got in zip(Q[:100], balls):
        want = sorted(np.nonzero(np.linalg.norm(P - q, axis=1) <= radius)[0].tolist())
        assert got == want


def test_single_query_and_empty():
    g = GridIndex([[0, 0, 0], [1, 0, 0]], 0.5)
    assert g.nearest([0.9, 0, 0]) == (pytest.approx(0.1), 1)
    assert g.nearest([5, 5, 5]) == (np.inf, -1)
    assert GridIndex(np.zeros((0, 3)), 1.0).within([[0, 0, 0]]) == [[]]


def test_flat_and_negative_coordinates():
    """CAD faces are often flat; points straddling cell boundaries at 0 must match."""
    xs = np.linspace(-1, 1, 41)
    P = np.array([[x, y, 0.0] for x in xs for y in xs])
    g = GridIndex(P, 0.05)
    d, i = g.nearest([[-0.0001, 0.0001, 0.0], [0.5249, -0.5249, 0.0]])
    assert d[0] == pytest.approx(np.hypot(1e-4, 1e-4)) and i[0] >= 0
    assert d[1] < 0.05 and i[1] >= 0
