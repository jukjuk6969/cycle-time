# plc_logic.py
"""
Lapisan kustomisasi di atas plc_engine.py (tanpa Tk / cv2).

1. Bahasa program teks  -> compile_program(text) / program_to_text(nodes)
2. Konfigurasi JSON     -> save_config / load_config (rules ROI + template ATAU program teks)
3. Pipeline             -> deteksi YOLO + waktu -> bit ROI -> logika -> status/pesan/waktu siklus

Bahasa program (satu baris = satu node, urutan baris = urutan evaluasi):

    # komentar
    hit_s1  = $s1                  # $nama = input eksternal (bit ROI dari compute_bits)
    dw_s1   = TON(hit_s1, 0.3)     # TON/TOF/TP(sinyal, detik)
    act_s1  = TOF(dw_s1, 0.2)
    edge    = R_TRIG(act_s1)       # R_TRIG / F_TRIG
    ok      = run@ & !done_s1@     # & | ^ ! atau AND OR XOR NOT (huruf besar); () untuk kelompok
    done_s1 = RS(ok & act_s1, start)   # SR(set, reset) set-dominan, RS(set, reset) reset-dominan
    cnt3    = CTU(edge, rst, 3)    # CTU(naik, reset, nilai)
    x       = NAND(a, b)           # NAND / NOR / AND / OR / XOR juga boleh sebagai fungsi

Aturan: "nama@" = nilai scan lalu. Nama yang ada di BAWAH baris pemakainya juga dibaca
dari scan lalu (tertunda 1 scan). Prioritas operator: ! lalu & lalu ^ lalu |.

Uji mandiri:  python plc_logic.py
"""
from __future__ import annotations
import time
import json
import re
from dataclasses import asdict

from plc_engine import (
    CONSTS, Engine, HitRule, Node, SeqSpec, StepSpec, CycleRunner,
    build_sequence_program, compute_bits,
)

CONFIG_VERSION = 1


class ProgramError(ValueError):
    pass


# ── 1. Parser & compiler ───────────────────────────────────────────────────

_TOK = re.compile(r"\s*(?:([&|^!(),])|([^\s&|^!(),#]+))")
_NUM = re.compile(r"\d+(\.\d+)?$")
_KW = {"AND", "OR", "XOR", "NOT"}
_TIMED = {"TON", "TOF", "TP"}
_FUNCS = _TIMED | {"R_TRIG", "F_TRIG", "SR", "RS", "CTU", "AND", "OR", "XOR", "NAND", "NOR"}


def _tokenize(s: str) -> list:
    toks, pos = [], 0
    s = s.rstrip()
    while pos < len(s):
        m = _TOK.match(s, pos)
        if not m:
            raise ProgramError(f"karakter tidak dikenal dekat '{s[pos:pos + 8]}'")
        pos = m.end()
        if m.group(1):
            toks.append(("OP", m.group(1)))
        else:
            w = m.group(2)
            if w.startswith("$"):
                if len(w) == 1:
                    raise ProgramError("'$' harus diikuti nama input")
                toks.append(("IN", w[1:]))
            elif _NUM.match(w):
                toks.append(("NUM", w))
            elif w in _KW:
                toks.append(("KW", w))
            else:
                toks.append(("ID", w))
    return toks


class _Parser:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expect(self, text):
        k, v = self.take()
        if v != text or k != "OP":
            raise ProgramError(f"seharusnya '{text}'")

    def parse(self):
        ast = self.or_()
        if self.i < len(self.t):
            raise ProgramError(f"sisa tak terduga: '{self.t[self.i][1]}'")
        return ast

    def _chain(self, sub, sym, kw, op):
        items = [sub()]
        while self.peek() in (("OP", sym), ("KW", kw)):
            self.take()
            items.append(sub())
        return items[0] if len(items) == 1 else ("GATE", op, items)

    def or_(self):
        return self._chain(self.xor, "|", "OR", "OR")

    def xor(self):
        return self._chain(self.and_, "^", "XOR", "XOR")

    def and_(self):
        return self._chain(self.unary, "&", "AND", "AND")

    def unary(self):
        if self.peek() in (("OP", "!"), ("KW", "NOT")):
            self.take()
            return ("NOT", self.unary())
        return self.atom()

    def atom(self):
        k, v = self.take()
        if k is None:
            raise ProgramError("ekspresi terpotong")
        if k == "OP" and v == "(":
            e = self.or_()
            self.expect(")")
            return e
        if k == "IN":
            return ("IN", v)
        if k == "NUM":
            return ("NUM", v)
        if k in ("ID", "KW") and self.peek() == ("OP", "("):
            fn = v.upper() if k == "ID" else v
            if fn not in _FUNCS:
                raise ProgramError(f"fungsi '{v}' tidak dikenal")
            self.take()
            args = []
            if self.peek() != ("OP", ")"):
                args.append(self.or_())
                while self.peek() == ("OP", ","):
                    self.take()
                    args.append(self.or_())
            self.expect(")")
            return ("FUNC", fn, args)
        if k == "ID":
            return ("ID", v)
        raise ProgramError(f"token tidak terduga '{v}'")


def _neg(r: str) -> str:
    if r.startswith("!"):
        return r[1:]
    if r in CONSTS:
        return "FALSE" if CONSTS[r] else "TRUE"
    return "!" + r


def _num(ast, what: str) -> float:
    if ast[0] != "NUM":
        raise ProgramError(f"{what} harus berupa angka")
    return float(ast[1])


class _Compiler:
    def __init__(self):
        self.nodes, self.inputs, self.n = [], {}, 0

    def input_ref(self, name):
        if name not in self.inputs:
            nid = f"in_{name}"
            self.inputs[name] = nid
            self.nodes.append(Node(nid, "IN", params={"name": name}))
        return self.inputs[name]

    def ref(self, ast, owner):
        k = ast[0]
        if k == "ID":
            return ast[1]
        if k == "IN":
            return self.input_ref(ast[1])
        if k == "NUM":
            if ast[1] in ("0", "1"):
                return "TRUE" if ast[1] == "1" else "FALSE"
            raise ProgramError(f"angka {ast[1]} tidak bisa dipakai sebagai sinyal")
        if k == "NOT":
            return _neg(self.ref(ast[1], owner))
        self.n += 1
        tid = f"{owner}__{self.n}"
        self.node(ast, tid, owner)
        return tid

    def node(self, ast, nid, owner):
        k = ast[0]
        if k == "GATE":
            refs = [self.ref(a, owner) for a in ast[2]]
            self.nodes.append(Node(nid, ast[1], refs))
        elif k == "FUNC":
            fn, args = ast[1], ast[2]
            if fn in _TIMED:
                if len(args) != 2:
                    raise ProgramError(f"{fn}(sinyal, detik) butuh 2 argumen")
                r = self.ref(args[0], owner)
                self.nodes.append(Node(nid, fn, [r], {"pt": _num(args[1], "waktu")}))
            elif fn in ("R_TRIG", "F_TRIG"):
                if len(args) != 1:
                    raise ProgramError(f"{fn}(sinyal) butuh 1 argumen")
                self.nodes.append(Node(nid, fn, [self.ref(args[0], owner)]))
            elif fn in ("SR", "RS"):
                if len(args) != 2:
                    raise ProgramError(f"{fn}(set, reset) butuh 2 argumen")
                self.nodes.append(Node(nid, fn, [self.ref(a, owner) for a in args]))
            elif fn == "CTU":
                if len(args) != 3:
                    raise ProgramError("CTU(naik, reset, nilai) butuh 3 argumen")
                refs = [self.ref(args[0], owner), self.ref(args[1], owner)]
                self.nodes.append(Node(nid, "CTU", refs, {"pv": _num(args[2], "nilai")}))
            else:                                   # gerbang sebagai fungsi
                if not args:
                    raise ProgramError(f"{fn}() butuh minimal 1 argumen")
                self.nodes.append(Node(nid, fn, [self.ref(a, owner) for a in args]))
        elif k == "IN":
            self.nodes.append(Node(nid, "IN", params={"name": ast[1]}))
        else:                                       # alias / NOT / konstanta
            self.nodes.append(Node(nid, "AND", [self.ref(ast, owner)]))


_ID_OK = re.compile(r"[^\s&|^!(),#$=@]+$")


def compile_program(text: str) -> list:
    """Teks -> list[Node]. Melempar ProgramError dengan nomor baris."""
    c = _Compiler()
    for ln, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            if "=" not in line:
                raise ProgramError("format 'nama = ekspresi'")
            name, expr = (p.strip() for p in line.split("=", 1))
            if not _ID_OK.match(name) or name in CONSTS or name in _KW:
                raise ProgramError(f"nama '{name}' tidak valid")
            ast = _Parser(_tokenize(expr)).parse()
            c.node(ast, name, name)
        except ProgramError as e:
            raise ProgramError(f"baris {ln}: {e}") from None
    return c.nodes


def _fmt(v) -> str:
    return format(float(v), "g")


def program_to_text(nodes: list) -> str:
    """list[Node] -> teks yang bisa dikompilasi balik (perilaku identik)."""
    out = []
    for n in nodes:
        a, ty = n.inputs, n.type
        if ty == "IN":
            rhs = "$" + str(n.params.get("name", n.id))
        elif ty == "NOT":
            rhs = "!" + a[0] if not a[0].startswith("!") else a[0][1:]
        elif ty in ("AND", "OR", "XOR"):
            rhs = a[0] if len(a) == 1 else f" {'&' if ty == 'AND' else '|' if ty == 'OR' else '^'} ".join(a)
        elif ty in ("NAND", "NOR"):
            rhs = f"{ty}({', '.join(a)})"
        elif ty in _TIMED:
            rhs = f"{ty}({a[0]}, {_fmt(n.params.get('pt', 0))})"
        elif ty == "CTU":
            rhs = f"CTU({a[0]}, {a[1]}, {_fmt(n.params.get('pv', 1))})"
        else:                                       # R_TRIG F_TRIG SR RS
            rhs = f"{ty}({', '.join(a)})"
        out.append(f"{n.id} = {rhs}")
    return "\n".join(out) + "\n"


# ── 2. Konfigurasi JSON ────────────────────────────────────────────────────

def rule_to_dict(r: HitRule) -> dict:
    d = asdict(r)
    d["rect"] = list(r.rect)
    d["classes"] = sorted(r.classes)
    return d


def rule_from_dict(d: dict) -> HitRule:
    d = dict(d)
    d["rect"] = tuple(d["rect"])
    d["classes"] = set(d.get("classes", []))
    return HitRule(**d)


def spec_to_dict(s: SeqSpec) -> dict:
    return asdict(s)


def spec_from_dict(d: dict) -> SeqSpec:
    d = dict(d)
    d["input"] = StepSpec(**d["input"])
    d["output"] = StepSpec(**d["output"])
    d["steps"] = [StepSpec(**s) for s in d.get("steps", [])]
    return SeqSpec(**d)


def make_config(rules: list, spec: SeqSpec | None = None, program: str | None = None,
                cycle: dict | None = None, messages: dict | None = None,
                max_det=None, single: bool = False) -> dict:
    """Template (spec) ATAU program teks. Salah satu wajib."""
    if (spec is None) == (program is None):
        raise ValueError("isi tepat satu: spec atau program")
    cfg = {
        "version": CONFIG_VERSION,
        "rules": [rule_to_dict(r) for r in rules],
        "global": {"max_det": max_det, "single": single},
    }
    if spec is not None:
        cfg["mode"], cfg["spec"] = "template", spec_to_dict(spec)
    else:
        cfg["mode"], cfg["program"] = "program", program
        cfg["cycle"] = cycle or {}
        cfg["messages"] = messages or {}
    return cfg


def template_to_program_config(cfg: dict) -> dict:
    """Ubah konfigurasi template jadi mode program (titik awal untuk diedit user)."""
    spec = spec_from_dict(cfg["spec"])
    out = spec.output.id
    msgs = {f"lock_{s.id}": f"Terkunci: '{s.desc or s.id}' belum boleh (urutan)" for s in spec.steps}
    msgs[f"lock_{out}"] = "Output ditolak: ada step mandatory yang belum selesai"
    new = dict(cfg)
    new.pop("spec", None)
    new.update(mode="program",
               program=program_to_text(build_sequence_program(spec)),
               cycle={"start": "start", "run": "run", "finish": f"done_{out}",
                      "input": spec.input.id},
               messages=msgs)
    return new


def save_config(path: str, cfg: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if cfg.get("version", 0) > CONFIG_VERSION:
        raise ValueError(f"versi konfigurasi {cfg['version']} lebih baru dari program ({CONFIG_VERSION})")
    return cfg


# ── 3. Runner program bebas & Pipeline ─────────────────────────────────────

class ProgramRunner:
    """Antarmuka sama dengan CycleRunner, tapi program ditulis user.
    Konvensi nama (opsional): node done_<roi> / skip_<roi> -> status panel;
    cycle = {start, run, finish, input} -> waktu siklus; messages = {id_node: teks}."""

    def __init__(self, nodes: list, cycle: dict | None = None, messages: dict | None = None):
        self.engine = Engine(nodes)
        self.cycle = {"start": "start", "run": "run", "finish": None, "input": None, **(cycle or {})}
        self.messages = dict(messages or {})
        self._resets = self.engine.resets
        self.cycles, self.last_cycle = 0, None
        self._clear()

    def _clear(self):
        self.t0, self.times, self.cycle_time, self.aborted = None, {}, None, False

    def scan(self, hits: dict, t: float, abort: bool = False):
        ext = dict(hits)
        ext["__abort"] = bool(abort)
        c = self.cycle
        was_run = self.engine.q.get(c["run"], False)
        q = self.engine.scan(ext, t)
        if self.engine.resets != self._resets:
            self._resets = self.engine.resets
            self._clear()
        if q.get(c["start"]):
            self._clear()
            self.t0 = t
        fin = bool(c["finish"] and q.get(c["finish"]))
        if self.t0 is not None:
            for nid, v in q.items():
                if v and nid.startswith("done_") and nid[5:] not in self.times:
                    self.times[nid[5:]] = t - self.t0
            if fin and self.cycle_time is None:
                self.cycle_time = self.last_cycle = t - self.t0
                self.cycles += 1
        if abort and was_run and not fin:
            self.aborted = True

    @property
    def state(self) -> str:
        q, c = self.engine.q, self.cycle
        if q.get(c["run"]):
            return "RUNNING"
        if c["finish"] and q.get(c["finish"]):
            return "COMPLETE"
        return "ABORTED" if self.aborted else "IDLE"

    def status(self) -> dict:
        q, out = self.engine.q, {}
        if self.t0 is not None and self.cycle.get("input"):
            out[self.cycle["input"]] = ("done", 0.0)
        for nid, v in q.items():
            if nid.startswith("done_") and v:
                out[nid[5:]] = ("done", self.times.get(nid[5:]))
            elif nid.startswith("skip_") and v:
                out[nid[5:]] = ("skip", None)
        return out

    def message(self) -> str:
        for nid, text in self.messages.items():
            if self.engine.q.get(nid):
                return text
        st = self.state
        if st == "COMPLETE" and self.cycle_time is not None:
            return f"Siklus selesai {self.cycle_time:.2f}s"
        return {"IDLE": "Menunggu", "RUNNING": "Berjalan", "ABORTED": "Siklus dibatalkan"}.get(st, "")


class Pipeline:
    """Satu objek untuk main.py / editor: Pipeline(cfg).update(dets, t, abort)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.rules = [rule_from_dict(d) for d in cfg.get("rules", [])]
        names = [r.name for r in self.rules]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            raise ValueError(f"nama ROI dobel: {sorted(dup)}")
        g = cfg.get("global", {})
        self.max_det, self.single = g.get("max_det"), bool(g.get("single", False))
        if cfg.get("mode", "template") == "template":
            self.runner = CycleRunner(spec_from_dict(cfg["spec"]))
        else:
            self.runner = ProgramRunner(compile_program(cfg["program"]),
                                        cfg.get("cycle"), cfg.get("messages"))
        known = set(names) | {"__abort"}
        self.warnings = list(self.runner.engine.warnings) + [
            f"input '${n.params.get('name', n.id)}' tidak punya ROI"
            for n in self.runner.engine.nodes
            if n.type == "IN" and n.params.get("name", n.id) not in known]
        self.assign = []
        self._snap = None
        self._prog_text = program_to_text(self.runner.engine.nodes)
        
    def update(self, dets: list, t: float, abort: bool = False) -> dict:
        """dets: [(x1,y1,x2,y2,nama_class,conf)] dari hasil inferensi TERBARU; t = waktu frame (detik)."""
        bits, self.assign = compute_bits(self.rules, dets, self.max_det, self.single)
        self.runner.scan(bits, t, abort)
        self._snap = {"wall": time.time(), "t": t,
                      "dbg": self.runner.engine.debug(),      # {id: (nilai, ET/CV)}
                      "state": self.state, "cycle_time": self.cycle_time,
                      "last_cycle": self.last_cycle, "cycles": self.cycles,
                      "msg": self.message(), "program": self._prog_text}
        return bits
    def snapshot(self):
        return self._snap
    def reset(self):
        self.runner.engine.reset()

    state = property(lambda s: s.runner.state)
    cycle_time = property(lambda s: s.runner.cycle_time)
    last_cycle = property(lambda s: s.runner.last_cycle)
    cycles = property(lambda s: s.runner.cycles)
    def status(self): return self.runner.status()
    def message(self): return self.runner.message()
    def debug(self): return self.runner.engine.debug()


# ── Uji mandiri ────────────────────────────────────────────────────────────

def _run(nodes, frames, dt=0.1):
    e, res = Engine(nodes), []
    for i, ext in enumerate(frames):
        res.append(e.scan({k: True for k in ext}, round(i * dt, 6)))
    return res


def _selftest_lang():
    # operator & presedensi
    n = compile_program("a = $x\nb = $y\nc = $z\nr = a | b & c\nq = !a ^ b\nn = NOT (a OR b)\n")
    r = _run(n, [["x"], ["y"], ["y", "z"], []])
    assert [f["r"] for f in r] == [True, False, True, False], "presedensi: a | (b & c)"
    assert [f["q"] for f in r] == [False, False, False, True], "xor dengan negasi"
    assert [f["n"] for f in r] == [False, False, False, True]

    # fungsi waktu / pemicu / counter / latch
    n = compile_program("""
        h = $h
        d = TON(h, 0.2)
        g = TOF(h, 0.2)
        p = R_TRIG(h)
        c = CTU(p, $rst, 2)
        l = SR(c, $rst)
        nd = NAND(h, TRUE)
    """)
    r = _run(n, [["h"], ["h"], ["h"], [], [], [], ["h"], [], ["rst"]])
    assert [f["d"] for f in r][:4] == [False, False, True, False]
    assert r[3]["g"] and not r[4]["g"], "TOF 0.2s: padam tepat di 0.2s"
    assert not r[3]["c"] and r[6]["c"] and r[6]["l"] and r[7]["l"], "CTU 2 lalu latch"
    assert not r[8]["l"], "reset"
    assert r[0]["nd"] is False and r[3]["nd"] is True

    # referensi scan lalu dan konstanta
    n = compile_program("a = $x\nb = a & !b@\nk = !TRUE | 1\n")
    r = _run(n, [["x"], ["x"], ["x"]])
    assert [f["b"] for f in r] == [True, False, True], "toggle lewat b@"
    assert r[0]["k"] is True

    # galat berformat
    for bad, frag in [("a = FOO(x)", "baris 1"), ("a = (x & y", "baris 1"),
                      ("ok = $x\nb = TON(ok)", "baris 2"), ("a = TON(x, y)", "angka"),
                      ("tanpa sama dengan", "format"), ("a = x &", "terpotong")]:
        try:
            compile_program(bad)
            raise AssertionError(f"harus gagal: {bad}")
        except ProgramError as e:
            assert frag in str(e), (bad, str(e))
    try:
        Engine(compile_program("a = zzz"))
        raise AssertionError("harus gagal: referensi tidak ada")
    except ValueError:
        pass
    print("bahasa program: semua uji lolos")


def _tpl(**kw):
    return SeqSpec(
        StepSpec("in"), StepSpec("out"),
        [StepSpec("s1", "expected", "scan"), StepSpec("s2", "mandatory", "pasang", dwell=0.2),
         StepSpec("s3", "expected", "mask", grace=0.1)], **kw)


def _selftest_roundtrip():
    # template -> teks -> compile: perilaku identik di semua node, semua frame
    frames = [[], ["in"], [], ["s3"], [], ["s2"], ["s2"], ["s2"], [], ["s1"], [], ["out"], ["out"], [],
              ["in"], [], ["s2"], ["s2"], ["s2"], [], ["out"], []]
    for kw in ({}, {"skip_mode": "final"}, {"restart_on_input": True}):
        nodes = build_sequence_program(_tpl(**kw))
        again = compile_program(program_to_text(nodes))
        assert _run(nodes, frames) == _run(again, frames), kw
    print("roundtrip template<->teks: semua uji lolos")


def _selftest_pipeline():
    rules = [HitRule(n, r, group="g") for n, r in [
        ("in", (0, 0, 50, 50)), ("s1", (60, 0, 110, 50)), ("s2", (120, 0, 170, 50)),
        ("s3", (180, 0, 230, 50)), ("out", (240, 0, 290, 50))]]

    def det(name):
        x1, y1, x2, y2 = next(r.rect for r in rules if r.name == name)
        return [((x1 + x2) / 2 - 5, 20, (x1 + x2) / 2 + 5, 30, "hand", 0.9)]

    seq = ["", "in", "", "s1", "", "s2", "s2", "s2", "", "s3", "", "out", ""]
    dets_frames = [det(s) if s else [] for s in seq]
    tpl_cfg = make_config(rules, spec=_tpl(), max_det=2)

    def run(cfg):
        p = Pipeline(cfg)
        for i, d in enumerate(dets_frames):
            p.update(d, round(i * 0.1, 6))
        return p

    a = run(tpl_cfg)
    assert a.state == "COMPLETE" and a.cycles == 1 and abs(a.cycle_time - 1.0) < 1e-6, (a.state, a.cycle_time)

    # JSON roundtrip
    import os, tempfile
    path = os.path.join(tempfile.mkdtemp(), "cfg.json")
    save_config(path, tpl_cfg)
    b = run(load_config(path))
    assert (b.state, b.cycle_time, b.status()) == (a.state, a.cycle_time, a.status())

    # mode program hasil konversi template: hasil sama
    prog_cfg = template_to_program_config(tpl_cfg)
    c = run(prog_cfg)
    assert c.state == "COMPLETE" and abs(c.cycle_time - a.cycle_time) < 1e-6
    assert c.status() == a.status(), (c.status(), a.status())
    assert c.cycles == 1

    # program edit user: s3 hanya dihitung bila s2 sudah selesai DAN ada 2 tangan? (contoh: ekstra syarat)
    prog_cfg["program"] += "extra = done_s3 & done_s2\n"
    d = run(prog_cfg)
    assert d.runner.engine.q["extra"] is True

    # peringatan input tanpa ROI
    w = Pipeline(make_config(rules[:2], program="a = $in\nb = $ghost\n")).warnings
    assert any("ghost" in x for x in w) and not any("'$in'" in x for x in w), w
    print("pipeline + JSON + mode program: semua uji lolos")


if __name__ == "__main__":
    _selftest_lang()
    _selftest_roundtrip()
    _selftest_pipeline()