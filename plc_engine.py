# plc_engine.py
"""
Engine logika bergaya PLC untuk CT Monitor (tanpa Tk / cv2, aman dipakai main.py).

Program = daftar Node, dievaluasi berurutan tiap scan.
Referensi ke node di ATAS membaca nilai scan ini; ke node di BAWAH membaca nilai scan lalu.
Ref input: "id", "!id" (negasi), "TRUE"/"FALSE".

Jalankan uji mandiri:  python plc_engine.py
"""
from __future__ import annotations

from dataclasses import dataclass, field

ARITY = {  # tipe -> (min_input, max_input); None = tak terbatas
    "IN": (0, 0), "NOT": (1, 1),
    "AND": (1, None), "OR": (1, None), "XOR": (1, None),
    "NAND": (1, None), "NOR": (1, None),
    "TON": (1, 1), "TOF": (1, 1), "TP": (1, 1),
    "R_TRIG": (1, 1), "F_TRIG": (1, 1),
    "SR": (2, 2), "RS": (2, 2), "CTU": (2, 2),
}
TIMED = ("TON", "TOF", "TP")
CONSTS = {"TRUE": True, "1": True, "FALSE": False, "0": False}
EPS = 1e-9


@dataclass
class Node:
    id: str
    type: str
    inputs: list = field(default_factory=list)
    params: dict = field(default_factory=dict)   # IN: name | TON/TOF/TP: pt (detik) | CTU: pv


def _base(ref: str) -> str:
    b = ref[1:] if ref.startswith("!") else ref
    return b[:-1] if b.endswith("@") else b

def validate(nodes: list) -> tuple:
    """Return (errors, warnings)."""
    errors, warnings = [], []
    ids = [n.id for n in nodes]
    index = {nid: i for i, nid in enumerate(ids)}
    for nid in {i for i in ids if ids.count(i) > 1}:
        errors.append(f"ID dobel: {nid}")
    for i, n in enumerate(nodes):
        if not n.id or n.id in CONSTS:
            errors.append(f"ID tidak valid: '{n.id}'")
        if n.type not in ARITY:
            errors.append(f"{n.id}: tipe '{n.type}' tidak dikenal")
            continue
        lo, hi = ARITY[n.type]
        cnt = len(n.inputs)
        if cnt < lo or (hi is not None and cnt > hi):
            errors.append(f"{n.id}: {n.type} butuh {lo}..{hi if hi is not None else 'n'} input, ada {cnt}")
        if n.type in TIMED or n.type == "CTU":
            key = "pt" if n.type in TIMED else "pv"
            try:
                if float(n.params.get(key, 0)) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{n.id}: parameter '{key}' harus angka >= 0")
        for r in n.inputs:
            b = _base(r)
            if b in CONSTS:
                continue
            if b not in index:
                errors.append(f"{n.id}: referensi '{r}' tidak ditemukan")
            elif index[b] >= i and not r.endswith("@"):
                warnings.append(f"{n.id}: membaca '{b}' dari scan sebelumnya (tertunda 1 scan)")
    return errors, warnings


class Engine:
    def __init__(self, nodes: list, back_tol: float = 0.05):
        errors, self.warnings = validate(nodes)
        if errors:
            raise ValueError("\n".join(errors))
        self.nodes = list(nodes)
        self.back_tol = back_tol
        self.reset()

    def reset(self):
        self.q = {n.id: False for n in self.nodes}
        self._prev = dict(self.q)
        self.state = {n.id: {} for n in self.nodes}
        self._t = None
        self.resets = getattr(self, "resets", 0) + 1

    def _ref(self, r: str) -> bool:
        if r in CONSTS:
            return CONSTS[r]
        neg = r.startswith("!")
        b = r[1:] if neg else r
        if b.endswith("@"):
            v = self._prev.get(b[:-1], False)
        else:
            v = self.q.get(b, False)
        return (not v) if neg else v

    def scan(self, ext: dict, t: float) -> dict:
        """ext: {nama_input: bool}; t: detik (waktu video). Return {id: bool}."""
        if self._t is not None and t < self._t - self.back_tol:
            self.reset()
        self._t = t
        self._prev = dict(self.q)
        for n in self.nodes:
            a = [self._ref(r) for r in n.inputs]
            st = self.state[n.id]
            ty = n.type
            if ty == "IN":
                v = bool(ext.get(n.params.get("name", n.id), False))
            elif ty == "NOT":
                v = not a[0]
            elif ty == "AND":
                v = all(a)
            elif ty == "OR":
                v = any(a)
            elif ty == "XOR":
                v = sum(a) % 2 == 1
            elif ty == "NAND":
                v = not all(a)
            elif ty == "NOR":
                v = not any(a)
            elif ty == "TON":
                pt = float(n.params.get("pt", 0))
                if a[0]:
                    if st.get("t0") is None:
                        st["t0"] = t
                    st["et"] = t - st["t0"]
                    v = st["et"] >= pt - EPS
                else:
                    st["t0"], st["et"], v = None, 0.0, False
            elif ty == "TOF":
                pt = float(n.params.get("pt", 0))
                if a[0]:
                    st["last"] = t
                    v = True
                else:
                    last = st.get("last")
                    v = last is not None and (t - last) < pt - EPS
            elif ty == "TP":
                pt = float(n.params.get("pt", 0))
                until = st.get("until")
                active = until is not None and t < until - EPS
                if a[0] and not st.get("prev", False) and not active:
                    st["until"] = until = t + pt
                    active = pt > 0
                st["prev"] = a[0]
                v = active
            elif ty == "R_TRIG":
                v = a[0] and not st.get("prev", False)
                st["prev"] = a[0]
            elif ty == "F_TRIG":
                v = (not a[0]) and st.get("prev", False)
                st["prev"] = a[0]
            elif ty == "SR":      # set dominan
                v = st["q"] = a[0] or (st.get("q", False) and not a[1])
            elif ty == "RS":      # reset dominan
                v = st["q"] = (a[0] or st.get("q", False)) and not a[1]
            else:                 # CTU
                if a[1]:
                    st["cnt"] = 0
                elif a[0] and not st.get("prev", False):
                    st["cnt"] = st.get("cnt", 0) + 1
                st["prev"] = a[0]
                v = st.get("cnt", 0) >= float(n.params.get("pv", 1))
            self.q[n.id] = bool(v)
        return dict(self.q)

    def debug(self) -> dict:
        """Untuk panel debug: {id: (nilai, info)} info = elapsed TON / hitungan CTU."""
        out = {}
        for n in self.nodes:
            st = self.state[n.id]
            info = st.get("et") if n.type == "TON" else st.get("cnt") if n.type == "CTU" else None
            out[n.id] = (self.q[n.id], info)
        return out

# ── Lapisan input: deteksi YOLO -> bit per ROI ─────────────────────────────

@dataclass
class HitRule:
    name: str                       # nama bit / ID ROI
    rect: tuple                     # (x1, y1, x2, y2) koordinat frame
    classes: set = field(default_factory=set)   # kosong = semua class
    conf: float = 0.25
    mode: str = "center"            # center | fraction | iou
    min_overlap: float = 0.4        # untuk fraction / iou
    priority: int = 0
    group: str = ""                 # "" = tidak eksklusif


def _inter(a: tuple, b: tuple) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _area(r: tuple) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def overlap_score(rule: HitRule, box: tuple):
    """Skor 0..1 bila box dianggap mengenai ROI, None bila tidak."""
    rx1, ry1, rx2, ry2 = rule.rect
    if rule.mode == "center":
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if not (rx1 <= cx <= rx2 and ry1 <= cy <= ry2):
            return None
        hw, hh = max((rx2 - rx1) / 2, 1e-9), max((ry2 - ry1) / 2, 1e-9)
        off = max(abs(cx - (rx1 + rx2) / 2) / hw, abs(cy - (ry1 + ry2) / 2) / hh)
        return max(0.0, 1.0 - off)
    inter = _inter(box, rule.rect)
    if rule.mode == "fraction":
        score = inter / max(_area(box), 1e-9)
    elif rule.mode == "iou":
        score = inter / max(_area(box) + _area(rule.rect) - inter, 1e-9)
    else:
        return None
    return score if score >= rule.min_overlap - EPS else None


def compute_bits(rules: list, dets: list, max_det=None, single: bool = False):
    """dets: [(x1, y1, x2, y2, nama_class, conf)].
    Return (bits {nama: bool}, assign [(det, [nama ROI yang dikreditkan])])."""
    dets = sorted(dets, key=lambda d: d[5], reverse=True)
    if max_det is not None:
        dets = dets[:max_det]
    bits = {r.name: False for r in rules}
    assign = []
    for d in dets:
        box, name, cf = d[:4], d[4], d[5]
        cands = []
        for r in rules:
            if r.classes and name not in r.classes:
                continue
            if cf < r.conf:
                continue
            s = overlap_score(r, box)
            if s is not None:
                cands.append((r, s))
        free, best = [], {}
        for r, s in cands:
            g = "*" if single else r.group
            if not g:
                free.append(r)
                continue
            key = (r.priority, s)
            if g not in best or key > best[g][0]:
                best[g] = (key, r)
        chosen = free + [v[1] for v in best.values()]
        for r in chosen:
            bits[r.name] = True
        assign.append((d, [r.name for r in chosen]))
    return bits, assign


def _selftest_hits():
    A = (0, 0, 100, 100)
    B = (50, 0, 150, 100)
    both = (60, 40, 80, 60, "hand", 0.9)      # pusat (70,50): di A dan B
    only_a = (10, 40, 30, 60, "hand", 0.5)
    only_b = (120, 40, 140, 60, "hand", 0.9)

    def bits(rules, dets, **kw):
        return compute_bits(rules, dets, **kw)[0]

    r = bits([HitRule("A", A), HitRule("B", B)], [both])
    assert r == {"A": True, "B": True}, "tanpa grup: keduanya kena"

    g = [HitRule("A", A, group="g", priority=0), HitRule("B", B, group="g", priority=1)]
    assert bits(g, [both]) == {"A": False, "B": True}, "prioritas B menang"
    assert bits(g, [only_a]) == {"A": True, "B": False}, "hanya A kena"

    g2 = [HitRule("A", A, group="g"), HitRule("B", B, group="g")]
    assert bits(g2, [both]) == {"A": True, "B": False}, "seri prioritas: skor terdekat ke pusat (A)"

    frac = [HitRule("A", A), HitRule("B", B, mode="fraction", min_overlap=0.8)]
    assert bits(frac, [(40, 40, 80, 60, "hand", 0.9)]) == {"A": True, "B": False}, "fraksi 0.75 < 0.8"

    iou = [HitRule("A", A, mode="iou", min_overlap=0.9)]
    assert bits(iou, [(0, 0, 100, 100, "hand", 0.9)])["A"] is True
    assert bits(iou, [(0, 0, 100, 50, "hand", 0.9)])["A"] is False

    assert bits([HitRule("A", A, conf=0.95)], [both])["A"] is False, "conf per ROI"
    assert bits([HitRule("A", A, classes={"part"})], [both])["A"] is False, "filter class"

    s = bits([HitRule("A", A), HitRule("B", B)], [both], single=True)
    assert sum(s.values()) == 1, "single: satu deteksi hanya satu ROI"

    m = bits([HitRule("A", A), HitRule("B", B)], [only_a, only_b], max_det=1)
    assert m == {"A": False, "B": True}, "max_det=1 memakai conf tertinggi"
    print("lapisan input: semua uji lolos")
# ── Template urutan: mandatory / expected -> program PLC ───────────────────

@dataclass
class StepSpec:
    id: str
    tag: str = "expected"        # mandatory | expected (diabaikan untuk input/output)
    desc: str = ""
    dwell: float = 0.0           # detik: harus bertahan di ROI sebelum dihitung
    grace: float = 0.0           # detik: tetap dianggap di ROI setelah deteksi hilang


@dataclass
class SeqSpec:
    input: StepSpec
    output: StepSpec
    steps: list = field(default_factory=list)
    skip_mode: str = "immediate"          # immediate | final
    restart_on_input: bool = False


def build_sequence_program(spec: SeqSpec) -> list:
    nodes = []
    add = nodes.append
    steps = spec.steps
    IN, OUT = spec.input.id, spec.output.id

    for s in [spec.input, *steps, spec.output]:
        add(Node(f"hit_{s.id}", "IN", params={"name": s.id}))
        add(Node(f"dw_{s.id}", "TON", [f"hit_{s.id}"], {"pt": s.dwell}))
        add(Node(f"act_{s.id}", "TOF", [f"dw_{s.id}"], {"pt": s.grace}))
    add(Node("abort", "IN", params={"name": "__abort"}))
    add(Node("in_re", "R_TRIG", [f"act_{IN}"]))
    add(Node("start", "AND", ["in_re"] if spec.restart_on_input else ["in_re", "!run@"]))

    mand = []
    for k, s in enumerate(steps):
        pre = [f"done_{m}@" for m in mand]
        if s.tag != "mandatory" and spec.skip_mode == "immediate":
            pre += [f"!done_{m.id}@" for m in steps[k + 1:]]
        add(Node(f"ok_{s.id}", "AND", ["run@"] + pre))
        add(Node(f"credit_{s.id}", "AND", [f"ok_{s.id}", f"act_{s.id}", f"!done_{s.id}@"]))
        add(Node(f"lock_{s.id}", "AND", ["run@", f"act_{s.id}", f"!ok_{s.id}", f"!done_{s.id}@"]))
        add(Node(f"done_{s.id}", "RS", [f"credit_{s.id}", "start"]))
        if s.tag == "mandatory":
            mand.append(s.id)

    add(Node(f"ok_{OUT}", "AND", ["run@"] + [f"done_{m}@" for m in mand]))
    add(Node(f"credit_{OUT}", "AND", [f"ok_{OUT}", f"act_{OUT}"]))
    add(Node(f"lock_{OUT}", "AND", ["run@", f"act_{OUT}", f"!ok_{OUT}"]))
    add(Node(f"done_{OUT}", "RS", [f"credit_{OUT}", "start"]))
    add(Node("stop", "OR", [f"credit_{OUT}", "abort"]))
    add(Node("run", "SR", ["start", "stop"]))

    for k, s in enumerate(steps):
        if s.tag == "mandatory":
            continue
        if spec.skip_mode == "immediate":
            later = [f"done_{m.id}" for m in steps[k + 1:]] + [f"done_{OUT}"]
            add(Node(f"late_{s.id}", "OR", later))
            add(Node(f"skip_{s.id}", "AND", [f"!done_{s.id}", f"late_{s.id}"]))
        else:
            add(Node(f"skip_{s.id}", "AND", [f"!done_{s.id}", f"done_{OUT}"]))
    return nodes


class CycleRunner:
    """Engine + waktu siklus. scan(hits, t) dipanggil tiap hasil deteksi baru."""

    def __init__(self, spec: SeqSpec):
        self.spec = spec
        self.engine = Engine(build_sequence_program(spec))
        self._resets = self.engine.resets
        self.cycles = 0
        self.last_cycle = None
        self._clear()

    def _clear(self):
        self.t0 = None
        self.times = {}
        self.cycle_time = None
        self.aborted = False

    def scan(self, hits: dict, t: float, abort: bool = False):
        ext = dict(hits)
        ext["__abort"] = bool(abort)
        was_run = self.engine.q.get("run", False)
        q = self.engine.scan(ext, t)
        if self.engine.resets != self._resets:          # waktu mundur / seek
            self._resets = self.engine.resets
            self._clear()
        if q["start"]:
            self._clear()
            self.t0 = t
        OUT = self.spec.output.id
        if self.t0 is not None:
            for sid in [s.id for s in self.spec.steps] + [OUT]:
                if q[f"done_{sid}"] and sid not in self.times:
                    self.times[sid] = t - self.t0
            if q[f"done_{OUT}"] and self.cycle_time is None:
                self.cycle_time = self.last_cycle = self.times[OUT]
                self.cycles += 1
        if abort and was_run and not q[f"done_{OUT}"]:
            self.aborted = True

    @property
    def state(self) -> str:
        q = self.engine.q
        if q["run"]:
            return "RUNNING"
        if q[f"done_{self.spec.output.id}"]:
            return "COMPLETE"
        return "ABORTED" if self.aborted else "IDLE"

    def status(self) -> dict:
        """{id_roi: ("done", detik) | ("skip", None)} -> langsung cocok untuk panel.set_progress."""
        q, sp, out = self.engine.q, self.spec, {}
        if self.t0 is not None:
            out[sp.input.id] = ("done", 0.0)
        for s in sp.steps:
            if q[f"done_{s.id}"]:
                out[s.id] = ("done", self.times.get(s.id))
            elif q.get(f"skip_{s.id}"):
                out[s.id] = ("skip", None)
        if q[f"done_{sp.output.id}"]:
            out[sp.output.id] = ("done", self.times.get(sp.output.id))
        return out

    def message(self) -> str:
        q, sp = self.engine.q, self.spec
        OUT, steps, st = sp.output.id, sp.steps, self.state
        if st == "IDLE":
            return "Menunggu tangan di ROI Input"
        if st == "ABORTED":
            return "Siklus dibatalkan (abort)"
        if st == "COMPLETE":
            skips = sum(1 for v in self.status().values() if v[0] == "skip")
            return f"Siklus selesai {self.cycle_time:.2f}s" + (
                f" — {skips} skip process" if skips else " — urutan OK")

        def open_mandatory(upto):
            for s in steps[:upto]:
                if s.tag == "mandatory" and not q[f"done_{s.id}"]:
                    return s
            return None

        for k, s in enumerate(steps):
            if q[f"lock_{s.id}"]:
                b = open_mandatory(k)
                if b:
                    return f"Terkunci: selesaikan '{b.desc or b.id}' dulu"
        if q[f"lock_{OUT}"]:
            b = open_mandatory(len(steps))
            if b:
                return f"Output ditolak: '{b.desc or b.id}' belum selesai"
        for s in steps:
            if not q[f"done_{s.id}"] and not q.get(f"skip_{s.id}"):
                return f"Menunggu: {s.desc or s.id}"
        return "Menunggu Output"


def _spec(mode="immediate", dwell=0.0, restart=False, tags=("expected", "mandatory", "expected")):
    return SeqSpec(
        StepSpec("in"), StepSpec("out"),
        [StepSpec("s1", tags[0], "scan pwba"),
         StepSpec("s2", tags[1], "pasang LCD", dwell=dwell),
         StepSpec("s3", tags[2], "mask")],
        skip_mode=mode, restart_on_input=restart)


def _drive(r, frames, i0=0, dt=0.1):
    for i, hits in enumerate(frames, start=i0):
        r.scan({h: True for h in hits}, round(i * dt, 6))
    return r


def _selftest_sequence():
    # A: urutan penuh
    r = _drive(CycleRunner(_spec()), [[], ["in"], [], ["s1"], [], ["s2"], [], ["s3"], [], ["out"], []])
    st = r.status()
    assert r.state == "COMPLETE" and abs(r.cycle_time - 0.8) < 1e-6 and r.cycles == 1
    assert len(st) == 5 and all(v[0] == "done" for v in st.values()), st
    assert r.message().endswith("urutan OK")

    # B: expected terlewat -> skip
    r = _drive(CycleRunner(_spec()), [[], ["in"], [], ["s2"], [], ["out"], []])
    st = r.status()
    assert r.state == "COMPLETE" and st["s1"] == ("skip", None) and st["s3"] == ("skip", None)
    assert "2 skip process" in r.message()

    # C: mandatory mengunci step berikutnya dan Output
    r = _drive(CycleRunner(_spec()), [[], ["in"], [], ["s3"]])
    assert r.message().startswith("Terkunci") and not r.engine.q["done_s3"]
    _drive(r, [[], ["out"]], i0=4)
    assert r.state == "RUNNING" and r.message().startswith("Output ditolak")
    _drive(r, [["s2"], [], ["out"]], i0=6)
    assert r.state == "COMPLETE" and r.status()["s3"] == ("skip", None)

    # D: dua mandatory bertumpuk pada satu scan -> hanya satu yang lolos
    r = _drive(CycleRunner(_spec(tags=("mandatory", "mandatory", "expected"))),
               [[], ["in"], [], ["s1", "s2"]])
    assert r.engine.q["done_s1"] and not r.engine.q["done_s2"], "satu step per scan"
    _drive(r, [["s2"]], i0=4)
    assert r.engine.q["done_s2"]

    # E: abort, lalu siklus baru
    r = _drive(CycleRunner(_spec()), [[], ["in"], [], ["s1"]])
    r.scan({}, 0.4, abort=True)
    assert r.state == "ABORTED"
    r.scan({"s2": True}, 0.5)
    assert not r.engine.q["done_s2"], "setelah abort tidak boleh ada kredit"
    r.scan({}, 0.6)
    r.scan({"in": True}, 0.7)
    assert r.state == "RUNNING" and r.t0 == 0.7 and "s1" not in r.status()

    # F: restart_on_input
    frames = [[], ["in"], [], ["s1"], [], ["in"]]
    r = _drive(CycleRunner(_spec(restart=True)), frames)
    assert r.t0 == 0.5 and "s1" not in r.status(), "restart mereset siklus"
    r = _drive(CycleRunner(_spec(restart=False)), frames)
    assert r.t0 == 0.1 and r.status()["s1"][0] == "done", "tanpa restart, Input diabaikan"

    # G: mode skip
    frames = [[], ["in"], [], ["s2"], [], ["s1"], [], ["out"], []]
    r = _drive(CycleRunner(_spec("immediate")), frames)
    assert r.status()["s1"] == ("skip", None)
    r = _drive(CycleRunner(_spec("final")), frames)
    assert r.state == "COMPLETE" and r.status()["s1"][0] == "done"

    # H: dwell 0.3 detik pada s2
    r = _drive(CycleRunner(_spec(dwell=0.3)),
               [[], ["in"], [], ["s2"], ["s2"], [], [], ["s2"], ["s2"], ["s2"]])
    assert not r.engine.q["done_s2"], "sentuhan singkat tidak lolos dwell"
    _drive(r, [["s2"]], i0=10)
    assert r.engine.q["done_s2"], "bertahan 0.3 detik -> lolos"

    # I: waktu mundur mereset siklus
    r = _drive(CycleRunner(_spec()), [[], ["in"], [], ["s1"]])
    r.scan({}, 0.0)
    assert r.state == "IDLE" and r.t0 is None
    print("template urutan: semua uji lolos")
def _selftest():
    prog = [
        Node("hit", "IN", params={"name": "s1"}),
        Node("rst", "IN", params={"name": "rst"}),
        Node("dw", "TON", ["hit"], {"pt": 0.2}),
        Node("done", "SR", ["dw", "rst"]),
    ]
    eng = Engine(prog)
    res = {}
    for i in range(0, 21):
        t = i * 0.05
        ext = {"s1": i in (6, 7) or 10 <= i <= 15, "rst": i == 18}
        res[i] = eng.scan(ext, t)["done"]
    assert not res[7], "sentuhan singkat tidak boleh lolos dwell"
    assert not res[13] and res[14], "dwell 0.2s: selesai tepat di i=14"
    assert res[16] and res[17], "latch bertahan setelah tangan keluar"
    assert not res[18], "reset memadamkan latch"
    eng.scan({"s1": True}, 5.0)
    eng.scan({"s1": True}, 1.0)          # waktu mundur -> reset otomatis
    assert eng.q["dw"] is False
    try:
        Engine([Node("a", "AND", ["zzz"])])
        raise AssertionError("harus gagal: referensi tidak ada")
    except ValueError:
        pass
    print("plc_engine: semua uji lolos")


if __name__ == "__main__":
    _selftest()
    _selftest_hits()
    _selftest_sequence()