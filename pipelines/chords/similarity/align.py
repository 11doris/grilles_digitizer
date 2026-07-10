"""Stage C — local alignment with a music-aware substitution model
(tune_similarity_spec §6.3).

Smith–Waterman/Gotoh local alignment over §4.3 token sequences with affine
gaps. Two implementations that must agree exactly (unit-tested):

* `sw_score` — numpy, anti-diagonal vectorized; used to score every
  (query, candidate) retrieval pair inside the §6.3 performance budget.
* `sw_traceback` — pure Python with pointers; run only for the pairs that
  are kept, to produce the slot path the UIs highlight.

Scores are normalized by the query's self-alignment downstream, so the
absolute weights only matter relative to each other.
"""
from __future__ import annotations

import numpy as np

# --- substitution weights (spec §6.3 table) --------------------------------

MATCH = 2.0          # identical (degree, quality)
RELATED = 1.6        # same degree, related quality class (small cost)
SUBSTITUTE = 1.4     # tritone sub / relative major-minor (small cost)
SAME_QUALITY = -1.0  # same quality, unrelated degree (large)
MISMATCH = -1.5      # everything else (large)
NC_MATCH = 0.5       # N.C. against N.C.
NC_OTHER = -0.5      # N.C. against a sounding chord
GAP_OPEN = 2.0       # affine gap: opening a half-bar-slot gap (medium) ...
GAP_EXTEND = 0.6     # ... extending it is much cheaper (open > extend)

# Related quality classes on the *same* degree: functional near-equivalents.
# maj<->dom covers the blues I; dom<->sus the suspended dominant; min<->m7b5
# the two ii shapes; m7b5<->dim the diminished family.
_RELATED_QUALITIES = {
    frozenset(("maj", "dom")), frozenset(("dom", "sus")),
    frozenset(("min", "m7b5")), frozenset(("m7b5", "dim")),
}


def token_score(a: tuple, b: tuple) -> float:
    """Substitution score for two §4.3 tokens (degree, quality)."""
    (da, qa), (db, qb) = a, b
    if qa == "nc" or qb == "nc":
        return NC_MATCH if qa == qb else NC_OTHER
    if a == b:
        return MATCH
    if da == db and frozenset((qa, qb)) in _RELATED_QUALITIES:
        return RELATED
    if qa == "dom" and qb == "dom" and (da - db) % 12 == 6:
        return SUBSTITUTE  # tritone substitution
    # relative major/minor chord substitution (e.g. degree 0 maj <-> 9 min)
    if {qa, qb} == {"maj", "min"}:
        maj_d, min_d = (da, db) if qa == "maj" else (db, da)
        if (min_d - maj_d) % 12 == 9:
            return SUBSTITUTE
    if qa == qb:
        return SAME_QUALITY
    return MISMATCH


# --- token interning: alignment works on small int arrays ------------------

class TokenTable:
    """Interns tokens to ints and precomputes the substitution matrix."""

    def __init__(self):
        self._ids: dict[tuple, int] = {}
        self._tokens: list[tuple] = []
        self._matrix = np.zeros((0, 0), dtype=np.float32)

    def encode(self, seq) -> np.ndarray:
        out = np.empty(len(seq), dtype=np.int32)
        for i, tok in enumerate(seq):
            tid = self._ids.get(tok)
            if tid is None:
                tid = self._ids[tok] = len(self._tokens)
                self._tokens.append(tok)
            out[i] = tid
        if len(self._tokens) > self._matrix.shape[0]:
            self._rebuild()
        return out

    def _rebuild(self):
        n = len(self._tokens)
        m = np.empty((n, n), dtype=np.float32)
        for i, a in enumerate(self._tokens):
            for j, b in enumerate(self._tokens):
                m[i, j] = token_score(a, b)
        self._matrix = m

    @property
    def matrix(self) -> np.ndarray:
        return self._matrix

    def self_score(self, encoded: np.ndarray) -> float:
        """Self-alignment score of a sequence (the §6.3 normalizer)."""
        return float(self._matrix[encoded, encoded].sum())


NEG_INF = np.float32(-1e30)


def sw_score_batch(q: np.ndarray, cands: list[np.ndarray],
                   sub: np.ndarray) -> np.ndarray:
    """Raw local-alignment scores of one query against many candidates,
    anti-diagonal vectorized across the whole batch (no traceback).

    Gotoh recurrences, 1-indexed cells over W[i-1, j-1] = sub[q[i-1], c[j-1]]:
        E[i,j] = max(M[i,j-1] - open, E[i,j-1] - ext)      (gap in query)
        F[i,j] = max(M[i-1,j] - open, F[i-1,j] - ext)      (gap in candidate)
        M[i,j] = max(0, max(M,E,F)[i-1,j-1] + W[i-1,j-1])
    Cells on anti-diagonal k = i+j depend only on diagonals k-1 and k-2, so
    each diagonal is one set of numpy ops over a (batch, n+1) block. Shorter
    candidates are padded with -inf substitution columns: a padded cell's M
    never exceeds 0 and only ever feeds other padded cells, so no masking is
    needed anywhere.
    """
    n = len(q)
    if n == 0 or not cands:
        return np.zeros(len(cands))
    m_max = max((len(c) for c in cands), default=0)
    if m_max == 0:
        return np.zeros(len(cands))
    B = len(cands)
    W = np.full((B, n, m_max), NEG_INF, dtype=np.float32)
    for b, c in enumerate(cands):
        if len(c):
            W[b, :, :len(c)] = sub[q[:, None], c[None, :]]

    size = n + 1
    shape = (B, size)
    M1 = np.full(shape, NEG_INF); E1 = np.full(shape, NEG_INF); F1 = np.full(shape, NEG_INF)
    M2 = np.full(shape, NEG_INF); E2 = np.full(shape, NEG_INF); F2 = np.full(shape, NEG_INF)
    # diagonal k=0 is the single cell (0,0); k=1 holds (0,1) and (1,0): all
    # borders, M=0 for local alignment, E/F = -inf (nothing to extend).
    M2[:, 0] = 0.0                        # k-2 buffer starts as diagonal 0
    M1[:, 0] = 0.0
    M1[:, 1] = 0.0

    best = np.zeros(B)
    for k in range(2, n + m_max + 1):
        lo, hi = max(1, k - m_max), min(n, k - 1)   # i-range of interior cells
        Mk = np.full(shape, NEG_INF); Ek = np.full(shape, NEG_INF); Fk = np.full(shape, NEG_INF)
        i = np.arange(lo, hi + 1)
        # E: from (i, j-1) on diag k-1, same i.  F: from (i-1, j), index i-1.
        Ek[:, lo:hi + 1] = np.maximum(M1[:, lo:hi + 1] - GAP_OPEN,
                                      E1[:, lo:hi + 1] - GAP_EXTEND)
        Fk[:, lo:hi + 1] = np.maximum(M1[:, lo - 1:hi] - GAP_OPEN,
                                      F1[:, lo - 1:hi] - GAP_EXTEND)
        # M: from (i-1, j-1) on diag k-2, index i-1, plus W[i-1, j-1]
        prev = np.maximum(np.maximum(M2[:, lo - 1:hi], E2[:, lo - 1:hi]),
                          F2[:, lo - 1:hi])
        Mk[:, lo:hi + 1] = np.maximum(0.0, prev + W[:, i - 1, k - i - 1])
        # borders of this diagonal (i=0 or j=0) are M=0 local starts; a
        # border past a candidate's real length only feeds padded cells.
        if k <= m_max:
            Mk[:, 0] = 0.0
        if k <= n:
            Mk[:, k] = 0.0
        np.maximum(best, Mk[:, lo:hi + 1].max(axis=1), out=best)
        M2, E2, F2 = M1, E1, F1
        M1, E1, F1 = Mk, Ek, Fk
    return best


def sw_score(q: np.ndarray, c: np.ndarray, sub: np.ndarray) -> float:
    """Single-pair convenience wrapper over `sw_score_batch`."""
    return float(sw_score_batch(q, [c], sub)[0])


def sw_traceback(q: np.ndarray, c: np.ndarray, sub: np.ndarray
                 ) -> tuple[float, list[tuple[int, int]]]:
    """Full Gotoh local alignment with traceback (pure Python; run only on
    kept pairs). Returns (raw score, [(q_slot, c_slot), ...]) where slots are
    0-based positions of aligned (substituted) cells — gap steps are not part
    of the mapping the UIs highlight."""
    n, m = len(q), len(c)
    if n == 0 or m == 0:
        return 0.0, []
    W = sub[q[:, None], c[None, :]]
    neg = float("-inf")
    M = [[0.0] * (m + 1) for _ in range(n + 1)]
    E = [[neg] * (m + 1) for _ in range(n + 1)]
    F = [[neg] * (m + 1) for _ in range(n + 1)]
    best, bi, bj = 0.0, 0, 0
    for i in range(1, n + 1):
        Mi, Mi1, Ei, Fi, Fi1 = M[i], M[i - 1], E[i], F[i], F[i - 1]
        Wi = W[i - 1]
        for j in range(1, m + 1):
            Ei[j] = max(Mi[j - 1] - GAP_OPEN, Ei[j - 1] - GAP_EXTEND)
            Fi[j] = max(Mi1[j] - GAP_OPEN, Fi1[j] - GAP_EXTEND)
            h = max(Mi1[j - 1], E[i - 1][j - 1], F[i - 1][j - 1]) + float(Wi[j - 1])
            Mi[j] = h if h > 0.0 else 0.0
            if Mi[j] > best:
                best, bi, bj = Mi[j], i, j
    # traceback from the best M cell
    path: list[tuple[int, int]] = []
    i, j, state = bi, bj, "M"
    while i > 0 and j > 0:
        if state == "M":
            if M[i][j] == 0.0:
                break
            path.append((i - 1, j - 1))
            h = M[i][j] - float(W[i - 1, j - 1])
            prev_m, prev_e = M[i - 1][j - 1], E[i - 1][j - 1]
            state = "M" if abs(h - prev_m) < 1e-6 else (
                "E" if abs(h - prev_e) < 1e-6 else "F")
            i, j = i - 1, j - 1
        elif state == "E":
            state = "M" if abs(E[i][j] - (M[i][j - 1] - GAP_OPEN)) < 1e-6 else "E"
            j -= 1
        else:
            state = "M" if abs(F[i][j] - (M[i - 1][j] - GAP_OPEN)) < 1e-6 else "F"
            i -= 1
    path.reverse()
    return best, path
