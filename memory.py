"""
memory.py — Instant learning from every clinician-verified scan (no retraining needed).

How it works
  * Every scan's 1536-d embedding is stored in the DB.
  * When a clinician verifies/corrects a scan, that (embedding, label) pair enters this
    memory immediately.
  * For a new scan we find the most similar verified scans (cosine similarity). If they
    are genuinely close, their labels are blended into the network's prediction. The
    blend weight grows with similarity and with how many close neighbours exist, and is
    capped (default 50%) so the trained network always keeps a say.
  * The periodic retrain (continual_learning.py) later bakes these samples into the
    network weights; after a new champion is promoted all stored scans are re-embedded.

Pure numpy — cheap enough to run on every request for tens of thousands of scans.
"""
import threading
import numpy as np

SKIN_TYPES = ["dry", "oily", "normal", "combination"]


class ExperienceMemory:
    def __init__(self, k=7, sim_threshold=0.80, max_weight=0.5, temperature=0.05, min_items=3):
        self.k, self.sim_threshold, self.max_weight = k, sim_threshold, max_weight
        self.temperature, self.min_items = temperature, min_items
        self._lock = threading.Lock()
        self.clear()

    def clear(self):
        with self._lock:
            self.E = np.zeros((0, 0), np.float32)
            self.types = np.zeros(0, np.int64)        # -1 = no verified type
            self.sev = []                             # list of dict
            self.ids = []

    def __len__(self):
        return len(self.ids)

    @staticmethod
    def _norm(x):
        x = np.asarray(x, np.float32)
        return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-8)

    def add(self, scan_id, embedding, skin_type=None, severity=None):
        e = self._norm(embedding).reshape(1, -1)
        t = SKIN_TYPES.index(skin_type) if skin_type in SKIN_TYPES else -1
        with self._lock:
            if scan_id in self.ids:                   # re-verification replaces the old entry
                i = self.ids.index(scan_id)
                self.E[i], self.types[i], self.sev[i] = e[0], t, severity or {}
                return
            self.E = e if self.E.size == 0 else np.vstack([self.E, e])
            self.types = np.append(self.types, t)
            self.sev.append(severity or {})
            self.ids.append(scan_id)

    def load_from_db(self, db, embedding_version):
        self.clear()
        n = 0
        for s in db.verified_samples(embedding_version=embedding_version, consent_only=True):
            if s["embedding"] is not None:
                self.add(s["scan_id"], s["embedding"], s["verified_skin_type"], s["severity"])
                n += 1
        return n

    def query(self, embedding, exclude_id=None):
        with self._lock:
            if len(self.ids) < self.min_items:
                return None
            sims = self.E @ self._norm(embedding).reshape(-1)
            if exclude_id in self.ids:
                sims[self.ids.index(exclude_id)] = -1
            order = np.argsort(-sims)[: self.k]
            order = order[sims[order] >= self.sim_threshold]
            if len(order) == 0:
                return None
            return order, sims[order], self.types[order].copy(), [self.sev[i] for i in order]

    def adjust(self, embedding, type_probs, severity, exclude_id=None):
        """
        type_probs: np.array(4) from the network, severity: dict attr->0..5.
        Returns (new_probs, new_severity, info) — info explains what memory contributed.
        """
        q = self.query(embedding, exclude_id)
        info = {"memory_size": len(self), "neighbours_used": 0, "blend_weight": 0.0}
        if q is None:
            return type_probs, severity, info
        idx, sims, types, sevs = q
        w = np.exp((sims - sims.max()) / self.temperature)
        # confidence of memory: similarity above threshold x number of close neighbours
        strength = float(np.clip((sims.mean() - self.sim_threshold) / (1 - self.sim_threshold), 0, 1))
        strength *= min(1.0, len(idx) / 3.0)
        alpha = self.max_weight * strength
        new_probs = np.asarray(type_probs, np.float64).copy()
        has_t = types >= 0
        if has_t.any() and alpha > 0:
            mem_p = np.bincount(types[has_t], weights=w[has_t], minlength=len(SKIN_TYPES))
            mem_p = mem_p / mem_p.sum()
            new_probs = (1 - alpha) * new_probs + alpha * mem_p
        new_sev = dict(severity)
        for a in severity:
            vals = [(wi, s[a]) for wi, s in zip(w, sevs) if a in s]
            if vals and alpha > 0:
                ws = np.array([v[0] for v in vals]); vs = np.array([v[1] for v in vals], np.float64)
                a_eff = alpha * min(1.0, len(vals) / 3.0)
                new_sev[a] = float((1 - a_eff) * severity[a] + a_eff * (ws @ vs / ws.sum()))
        info.update({"neighbours_used": int(len(idx)), "blend_weight": round(alpha, 3),
                     "max_similarity": round(float(sims.max()), 3),
                     "neighbour_scan_ids": [self.ids[i] for i in idx]})
        return new_probs, new_sev, info
