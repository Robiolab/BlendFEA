"""Fixed-radius neighbour search with numpy only (replaces scipy.spatial.KDTree).

Every lookup in the matcher asks "which points lie within `tolerance`?", so a uniform
grid with cells as large as the radius is enough: a point's neighbours are always in
its own cell or one of the 26 around it. Results equal KDTree's for those questions;
points farther than the radius are reported as no match (distance inf, index -1).
"""
from __future__ import annotations

import itertools

import numpy as np

_OFFSETS = np.array(list(itertools.product((-1, 0, 1), repeat=3)), dtype=np.int64)


class GridIndex:
    def __init__(self, points, radius: float):
        self.points = np.asarray(points, dtype=float).reshape(-1, 3)
        self.radius = float(radius)
        # A zero/negative radius still has to bucket points; matches then need equality.
        self._cell = self.radius if self.radius > 0 else 1e-12
        self._cells: dict[tuple, np.ndarray] = {}
        if len(self.points):
            keys = np.floor(self.points / self._cell).astype(np.int64)
            uniq, inverse = np.unique(keys, axis=0, return_inverse=True)
            order = np.argsort(inverse.ravel(), kind="stable")
            bounds = np.searchsorted(inverse.ravel()[order], np.arange(len(uniq) + 1))
            for c, key in enumerate(map(tuple, uniq)):
                self._cells[key] = order[bounds[c]:bounds[c + 1]]

    def _candidates(self, q):
        base = np.floor(q / self._cell).astype(np.int64)
        found = [self._cells.get(tuple(k)) for k in base + _OFFSETS]
        found = [f for f in found if f is not None]
        return np.concatenate(found) if found else None

    def nearest(self, queries):
        """Nearest point within the radius: (distance, index); (inf, -1) when none.
        A single (3,) query gives scalars, an (n, 3) array gives arrays."""
        q = np.asarray(queries, dtype=float)
        single = q.ndim == 1
        q = q.reshape(-1, 3)
        dist = np.full(len(q), np.inf)
        idx = np.full(len(q), -1, dtype=np.int64)
        for n, p in enumerate(q):
            cand = self._candidates(p)
            if cand is None:
                continue
            d = np.linalg.norm(self.points[cand] - p, axis=1)
            j = int(np.argmin(d))
            if d[j] <= self.radius:
                dist[n], idx[n] = d[j], cand[j]
        return (float(dist[0]), int(idx[0])) if single else (dist, idx)

    def within(self, queries):
        """For each query, the indices of all points within the radius (like
        KDTree.query_ball_point)."""
        q = np.asarray(queries, dtype=float).reshape(-1, 3)
        out = []
        for p in q:
            cand = self._candidates(p)
            if cand is None:
                out.append([])
                continue
            d = np.linalg.norm(self.points[cand] - p, axis=1)
            out.append(sorted(int(i) for i in cand[d <= self.radius]))
        return out
