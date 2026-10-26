const db = require("./db");

const stations = new Map();                 // "line-station" -> Station
let ready = false;
let ioRef = null;
let snapCache = null;                       // { all: [...], byLine: Map(line -> [...]) }, dibangun ulang hanya bila ada perubahan
let lastLines = new Set();                  // line yang pernah dikirim, supaya line yang kosong tetap diberi tahu

const shiftStartOf = (sec) => db.shiftRange(new Date(sec * 1000)).start.getTime();
const dayStartOf = (sec) => db.prodDayRange(new Date(sec * 1000)).start.getTime();
const msKey = (sec) => new Date(sec * 1000).getTime();   // sama persis dengan yang disimpan SQL

class Station {
    constructor(line, id) {
        this.line = line; this.id = id; this.meta = null;
        this.seq = 0; this.dirty = false;
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
        this.roll(sec);
        if (!this.isLive(sec)) return true;     // data shift lama: dedup diserahkan ke SQL (NOT EXISTS)
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

// kirim summary: room "all" menerima semua; tiap room "line:N" hanya stasiun line-nya
function emitSummary(io, rows = snapshotAll(), onlyLines = null) {
    const byLine = new Map();
    for (const r of rows) {
        const l = Number(r.line);
        if (!byLine.has(l)) byLine.set(l, []);
        byLine.get(l).push(r);
    }
    io.to("all").emit("summary_update", rows);
    const lines = new Set([...lastLines, ...byLine.keys()]);
    for (const l of lines) {
        if (onlyLines && !onlyLines.has(l)) continue;
        io.to(`line:${l}`).emit("summary_update", byLine.get(l) || []);
    }
    lastLines = new Set(byLine.keys());
}

// ── muat dari SQL: dipakai saat startup, rekonsiliasi, dan setelah konfigurasi berubah ──
async function refresh(withSeen = false) {
    const rows = await db.getStationSummary();
    const now = Date.now() / 1000;
    const alive = new Set();

    for (const r of rows) {
        const st = get(r.line, r.station_id);
        alive.add(`${r.line}-${r.station_id}`);
        st.meta = r;
        st.shiftStart = shiftStartOf(now); st.dayStart = dayStartOf(now);
        st.cyclesDay = r.cycles_today || 0;
        st.sumCtDay = (r.avg_ct_today || 0) * st.cyclesDay;
        st.lastCt = r.last_cycle_sec ?? null;
        st.cyclesShift = r.cycle_count_total || 0;
        st.mark();
    }
    for (const k of [...stations.keys()]) if (!alive.has(k)) stations.delete(k);
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

async function flush() {
    if (busy || !queue.length) return;
    busy = true;
    const batch = queue.splice(0, BATCH);
    const t0 = Date.now();
    try {
        await db.insertCyclesBatch(batch.map((b) => b.row));
        batch.forEach((b) => b.resolve());
    } catch (e) {
        console.error("[engine] batch gagal:", e.message);
        if (batch.length === 1) batch[0].reject(e);
        else for (const b of batch) {            // satu baris bermasalah tidak boleh menjatuhkan yang lain
            try { await db.insertCyclesBatch([b.row]); b.resolve(); }
            catch (e2) { b.reject(e2); }
        }
    } finally {
        const ms = Date.now() - t0;
        if (ms > 200) console.log(`[engine] flush ${batch.length} baris ${ms}ms`);
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
