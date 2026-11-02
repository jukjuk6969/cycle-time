const db = require("./db");
const VERBOSE = process.env.LOG_VERBOSE === "true";
const stations = new Map();                 // "line-station" -> Station
let ready = false;
let ioRef = null;
let snapCache = null;                       // { all: [...], byLine: Map(line -> [...]) }, dibangun ulang hanya bila ada perubahan
let lastLines = new Set();                  // line yang pernah dikirim, supaya line yang kosong tetap diberi tahu
const removedLines = new Set();             // line yang kehilangan stasiun saat refresh; diumumkan di tick berikutnya
const shiftStartOf = (sec) => db.shiftRange(new Date(sec * 1000)).start.getTime();
const dayStartOf = (sec) => db.prodDayRange(new Date(sec * 1000)).start.getTime();
const msKey = (sec) => new Date(sec * 1000).getTime();   // sama persis dengan yang disimpan SQL
const FUTURE_TOLERANCE_SEC = 60;   // toleransi selisih jam perangkat; sesuaikan bila perlu

class Station {
    constructor(line, id) {
        this.line = line; this.id = id; this.meta = null;
        this.seq = 0; this.dirty = false;
        this.pending = 0;
        this.seen = new Set();
        this.shiftStart = 0; this.dayStart = 0;
        this.cyclesShift = 0; this.cyclesDay = 0; this.sumCtDay = 0; this.lastCt = null;
    }
    mark() { this.dirty = true; snapCache = null; }     // tandai berubah + buang cache snapshot
    roll(sec) { this.rollTo(shiftStartOf(sec), dayStartOf(sec)); }
    rollTo(s, d) {                              // tick menghitung s/d sekali untuk semua stasiun
        if (s > this.shiftStart) { this.shiftStart = s; this.seen.clear(); this.cyclesShift = 0; this.mark(); }
        if (d > this.dayStart) { this.dayStart = d; this.cyclesDay = 0; this.sumCtDay = 0; this.mark(); }
    }
    isLive(sec) { return shiftStartOf(sec) === this.shiftStart; }
    reserve(sec) {
        if (sec > Date.now() / 1000 + FUTURE_TOLERANCE_SEC) {
            const e = new Error("timestamp melebihi waktu server");
            e.code = "FUTURE_TS";
            throw e;                                // jangan memajukan shift dari jam perangkat yang salah
        }
        this.roll(sec);
        if (!this.isLive(sec)) return true;         // data shift lama: dedup diserahkan ke SQL (NOT EXISTS)
        const k = msKey(sec);
        if (this.seen.has(k)) return false;
        this.seen.add(k);
        return true;
    }
    rollback(sec) { this.seen.delete(msKey(sec)); }
    apply(ct) {
        this.cyclesShift++; this.cyclesDay++; this.sumCtDay += ct; this.lastCt = ct;
        this.seq++; this.mark();
    }
    snapshot() {
        return {
            ...this.meta,
            cycles_today: this.cyclesDay,
            avg_ct_today: this.cyclesDay ? this.sumCtDay / this.cyclesDay : null,
            last_cycle_sec: this.lastCt,
            cycle_count_total: this.cyclesShift,
        };
    }
}

function get(line, id) {
    const k = `${line}-${id}`;
    let s = stations.get(k);
    if (!s) stations.set(k, s = new Station(line, id));
    return s;
}
let decorate = (r) => r;
function setDecorator(fn) { decorate = fn; }
function markDirty(line, id) {
    const s = stations.get(`${line}-${id}`);
    if (s) s.mark();
}
function buildSnap() {
    if (snapCache) return snapCache;
    const all = [...stations.values()].filter((s) => s.meta)
        .sort((a, b) => a.line - b.line || a.id - b.id)
        .map((s) => decorate(s.snapshot()));
    const byLine = new Map();
    for (const r of all) {
        const l = Number(r.line);
        if (!byLine.has(l)) byLine.set(l, []);
        byLine.get(l).push(r);
    }
    return (snapCache = { all, byLine });
}
function snapshotAll() { return buildSnap().all; }
function snapshotForLines(lines) {
    const { byLine } = buildSnap();
    return lines.flatMap((l) => byLine.get(Number(l)) || []);
}

function emitSummary(io, rows = snapshotAll(), onlyLines = null) {
    const snap = buildSnap();
    let byLine = snap.byLine;
    if (rows !== snap.all) {                    // rows bukan snapshot cache: kelompokkan sendiri
        byLine = new Map();
        for (const r of rows) {
            const l = Number(r.line);
            if (!byLine.has(l)) byLine.set(l, []);
            byLine.get(l).push(r);
        }
    }
    io.to("all").emit("summary_update", rows);
    const lines = new Set([...lastLines, ...byLine.keys()]);
    for (const l of lines) {
        if (onlyLines && !onlyLines.has(l)) continue;
        io.to(`line:${l}`).emit("summary_update", byLine.get(l) || []);
    }
    lastLines = new Set(snap.byLine.keys());    // selalu dari snapshot penuh, bukan dari rows yang dikirim
}

async function refresh(withSeen = false) {
    const before = new Map();                  // kondisi sebelum query, untuk mendeteksi perubahan selama query
    for (const [k, s] of stations) before.set(k, { seq: s.seq, pending: s.pending });

    const rows = await db.getStationSummary();
    const now = Date.now() / 1000;
    const alive = new Set();

    for (const r of rows) {
        const key = `${r.line}-${r.station_id}`;
        const st = get(r.line, r.station_id);
        alive.add(key);
        st.meta = r;

        const b = before.get(key);
        const racing = b && (b.pending > 0 || st.pending > 0 || st.seq !== b.seq);
        if (racing) { st.mark(); continue; }   // hasil SQL mungkin belum/sudah memuat cycle ini: jangan timpa counter

        st.shiftStart = shiftStartOf(now); st.dayStart = dayStartOf(now);
        st.cyclesDay = r.cycles_today || 0;
        st.sumCtDay = (r.avg_ct_today || 0) * st.cyclesDay;
        st.lastCt = r.last_cycle_sec ?? null;
        st.cyclesShift = r.cycle_count_total || 0;
        st.mark();
    }
    for (const [k, s] of [...stations]) {
        if (!alive.has(k)) { removedLines.add(Number(s.line)); stations.delete(k); }
    }
    snapCache = null;

    if (withSeen) {                            // hanya saat startup, supaya tidak menimpa reservasi yang sedang jalan
        const p = await db.getPool();
        const r = await p.query(
            `SELECT line, station_id, end_timestamp FROM cycle_times
             WHERE is_complete = true AND end_timestamp >= $1`,
            [new Date(shiftStartOf(now))]
        );
        for (const st of stations.values()) st.seen.clear();
        for (const x of r.rows) stations.get(`${x.line}-${x.station_id}`)?.seen.add(x.end_timestamp.getTime());
    }
    return snapshotAll();
}

// ── group commit ──
const queue = [];
let busy = false;
const MAX_QUEUE = 20000;
const BATCH = 500;

function enqueue(row) {
    if (queue.length >= MAX_QUEUE) {
        const e = new Error("overloaded"); e.code = "BACKPRESSURE";
        return Promise.reject(e);
    }
    return new Promise((resolve, reject) => {
        queue.push({ row, resolve, reject });
        if (!busy) setImmediate(flush);
    });
}
// error yang berasal dari isi baris (bukan dari koneksi/server): layak diisolasi per baris
const isDataError = (e) => /^(22|23)/.test(String(e.code || ""));

async function commitGroup(items) {
    try {
        await db.insertCyclesBatch(items.map((b) => b.row));
        items.forEach((b) => b.resolve());
    } catch (e) {
        if (!isDataError(e) || items.length === 1) {   // infrastruktur, atau sudah tinggal satu baris
            items.forEach((b) => b.reject(e));
            return;
        }
        const mid = items.length >> 1;                 // bagi dua: baris buruk terisolasi dalam log2(n) langkah
        await commitGroup(items.slice(0, mid));
        await commitGroup(items.slice(mid));
    }
}
async function flush() {
    if (busy || !queue.length) return;
    busy = true;
    const batch = queue.splice(0, BATCH);
    const t0 = Date.now();
    try {
        await commitGroup(batch);
    } finally {
        const ms = Date.now() - t0;
        if (VERBOSE && ms > 200) console.log(`[engine] flush ${batch.length} baris ${ms}ms`);
        busy = false;
        if (queue.length) setImmediate(flush);
    }
}
setInterval(flush, 1000);

// ── tick: kirim summary dari memori maksimal 1x/detik, hanya untuk line yang berubah ──
function startTick(io) {
    setInterval(() => {
        const now = Date.now() / 1000;
        const sh = shiftStartOf(now), dy = dayStartOf(now);   // dihitung sekali, bukan per stasiun
        const dirtyLines = new Set();
        for (const l of removedLines) dirtyLines.add(l);
        removedLines.clear();
        for (const s of stations.values()) {
            s.rollTo(sh, dy);                   // reset counter saat pergantian shift walau tanpa cycle
            if (s.dirty) { s.dirty = false; dirtyLines.add(Number(s.line)); }
        }
        if (dirtyLines.size) emitSummary(io, snapshotAll(), dirtyLines);
    }, 1000);
}

async function start(io) {
    ioRef = io;
    for (; ;) {
        try { await refresh(true); ready = true; console.log("[engine] siap"); break; }
        catch (e) { console.error("[engine] muat awal gagal, ulang 5 detik:", e.message); await new Promise((r) => setTimeout(r, 5000)); }
    }
    startTick(io);
    setInterval(() => refresh().catch((e) => console.error("[engine] rekonsiliasi:", e.message)), 300_000);
}
// di flush(): queue.splice(0, BATCH); setInterval(flush, 50) bisa jadi 1000ms sebagai pengaman saja
module.exports = {
    get, enqueue, snapshotAll, snapshotForLines, emitSummary, refresh, start, setDecorator, markDirty,
    get ready() { return ready; },
    get queueLength() { return queue.length; },   // baru
};
