/**
 * server.js
 * Express + Socket.io backend untuk CT Monitor.
 *
 * Endpoints:
 *   POST /api/cycle      — terima data cycle time dari Python
 *   POST /api/heartbeat  — terima status live dari Python
 *   GET  /api/summary    — ringkasan semua stasiun
 *   GET  /api/station/:id/history   — riwayat CT satu stasiun
 *   GET  /api/station/:id/hourly    — statistik per jam
 *
 * Socket.io events (server → client):
 *   cycle_complete  — data cycle baru (broadcast ke semua dashboard)
 *   heartbeat       — update state live stasiun
 *   summary_update  — ringkasan terbaru semua stasiun
 */

require("dotenv").config();
const express = require("express");
const http = require("http");
const { Server } = require("socket.io");
const cors = require("cors");
const db = require("./db");
const os = require("os");
const net = require("net");
const engine = require("./engine");
async function broadcastSummary() { engine.emitSummary(io, await engine.refresh()); }

// ── routing event per room ──
// "all"      : dashboard yang belum subscribe (menerima semuanya)
// "line:N"   : dashboard yang subscribe ke line N
// "fd:L-S"   : socket.io milik stasiun Python itu sendiri (hanya menerima konfigurasi miliknya)
const roomsOf = (line) => ["all", `line:${Number(line)}`];
function emitLine(line, ev, payload) { io.to(roomsOf(line)).emit(ev, payload); }
function emitCfg(line, station_id, ev, payload) {     // perubahan konfigurasi: dashboard + stasiun yang bersangkutan
  io.to([...roomsOf(line), `fd:${Number(line)}-${Number(station_id)}`]).emit(ev, payload);
}

const HB_PORT = Number(process.env.HB_PORT || 5002);


const sessions = new Map();           // "line-station" -> { sock, gen, ip, offlineTimer }
let genCounter = 0;
const seenDirty = new Set();          // sesi yang perlu di-flush last_seen ke DB
const savingKeys = new Set();         // "line-station" yang sedang saving mode
const liveStreams = new Map();        // "line-station" -> Set<fungsi penghenti>
const keyOf = (l, s) => `${Number(l)}-${Number(s)}`;
const isSaving = (l, s) => savingKeys.has(keyOf(l, s));
const isOnline = (l, s) => sessions.has(keyOf(l, s)) || savingKeys.has(keyOf(l, s));
engine.setDecorator((r) => {
  const on = isOnline(r.line, r.station_id);
  return { ...r, is_online: on, online: on, saving: isSaving(r.line, r.station_id) };
});
const fdAddr = new Map();
process.on("uncaughtException", (err) => console.error("[uncaughtException]", err));
process.on("unhandledRejection", (err) => console.error("[unhandledRejection]", err));
const app = express();
const server = http.createServer(app);
const allowedOrigins = (process.env.CORS_ORIGINS || "")
  .split(",")
  .map(s => s.trim())
  .filter(Boolean);

const HB_DB_MIN_MS = 15_000;
const hbState = new Map();   // "line-station" -> status terakhir
const hbDirty = new Set();   // stasiun yang berubah sejak tick terakhir

function writeHbToDb(h) {
  if (h.closed) return;
  if (h.dbBusy) { h.dbPending = true; return; }
  h.dbBusy = true;
  h.dbPending = false;
  h.dbAt = Date.now();
  h.inflight = db.upsertHeartbeat(h.line, h.station_id, h.state, h.elapsed_sec ?? null)
    .catch((e) => console.error("[hb db]", e.message))
    .finally(() => {
      h.dbBusy = false;
      if (h.dbPending && !h.closed) writeHbToDb(h);
    });
}

function emitHb(h) {
  emitLine(h.line, "heartbeat", {
    line: h.line, station_id: h.station_id, state: h.state,
    elapsed_sec: h.elapsed_sec, roi_idle_sec: h.roi_idle_sec,
  });
}

function pushState(line, station_id, { state, elapsed_sec, roi_idle_sec }) {
  if (!state) return;
  const key = `${line}-${station_id}`;
  let h = hbState.get(key);
  if (!h) {
    h = { line, station_id, state: null, dbAt: 0, dbBusy: false, dbPending: false, closed: false };
    hbState.set(key, h);
  }
  const changed = h.state !== state;
  h.state = state; h.elapsed_sec = elapsed_sec; h.roi_idle_sec = roi_idle_sec;

  if (changed || Date.now() - h.dbAt >= HB_DB_MIN_MS) writeHbToDb(h);

  if (changed) { hbDirty.delete(key); emitHb(h); }   // perubahan state: langsung
  else hbDirty.add(key);                              // selain itu: tunggu tick
}

setInterval(() => {
  if (!hbDirty.size) return;
  const batch = [];
  for (const key of hbDirty) {
    const h = hbState.get(key);
    if (h && !h.closed) batch.push({
      line: h.line, station_id: h.station_id, state: h.state,
      elapsed_sec: h.elapsed_sec, roi_idle_sec: h.roi_idle_sec,
    });
  }
  hbDirty.clear();
  if (!batch.length) return;
  const byLine = new Map();                         // kirim per line, bukan satu paket untuk semua
  for (const x of batch) {
    if (!byLine.has(x.line)) byLine.set(x.line, []);
    byLine.get(x.line).push(x);
  }
  io.to("all").volatile.emit("heartbeat_batch", batch);              // satu paket utuh untuk yang menerima semua
  for (const [l, rows] of byLine) io.to(`line:${l}`).volatile.emit("heartbeat_batch", rows);   // dashboard per line
}, 1000);

async function clearHb(line, station_id) {
  const key = `${line}-${station_id}`;
  const h = hbState.get(key);
  if (!h) return;
  h.closed = true;
  hbDirty.delete(key);
  hbState.delete(key);
  await h.inflight;      // tunggu tulis yang sedang jalan selesai, baru OFFLINE
}

async function finalizeOffline(key, gen, line, station_id) {
  const cur = sessions.get(key);
  if (!cur || cur.gen !== gen) return;        // sudah ada sesi baru, batalkan

  sessions.delete(key);
  engine.markDirty(line, station_id);
  const clearing = clearHb(line, station_id); // bagian sinkronnya jalan sekarang, sebelum ada await lain

  emitLine(line, "fd_status", { line, station_id, online: false });
  emitLine(line, "heartbeat", { line, station_id, state: "OFFLINE", elapsed_sec: null, roi_idle_sec: null });
  log(`[FD-HB] offline L${line} ST${station_id}`);

  try {
    await clearing;
    if (sessions.has(key)) return;            // reconnect saat menunggu inflight
    await db.markFdOffline(line, station_id);
    if (sessions.has(key)) return;            // cek lagi setelah await
    await db.upsertHeartbeat(line, station_id, "OFFLINE", null);
  } catch (e) {
    console.error("[FD-HB close]", e.message);
  }
}
let seenFlushing = false;
setInterval(async () => {
  if (seenFlushing || !seenDirty.size) return;
  seenFlushing = true;
  const keys = [...seenDirty];
  seenDirty.clear();
  try {
    const ips = [];
    for (const key of keys) {
      const s = sessions.get(key);
      if (s) ips.push(s.ip);
    }
    if (ips.length) {
      try { await db.updateFdSeenBatch(ips); }       // satu query per 500 stasiun, bukan satu per stasiun
      catch (e) { console.error("[fd seen db]", e.message); for (const k of keys) seenDirty.add(k); }
    }
  } finally { seenFlushing = false; }
}, 10_000);
// helper baru
function fdStatusAll() {
  return engine.snapshotAll().map((s) => {
    const key = `${s.line}-${s.station_id}`;
    const x = sessions.get(key);
    const sv = savingKeys.has(key);
    return {
      line: s.line, station_id: s.station_id,
      online: !!x || sv, saving: sv,
      state: x?.state ?? (sv ? "SAVING_MODE" : "OFFLINE"),
      rtt: x?.srtt != null ? Math.round(x.srtt) : null,
    };
  });
}
// cache hasil registry: saat server restart ratusan stasiun hello bersamaan; tanpa cache tiap hello = 2 query SQL
const HELLO_TTL_MS = 30_000;
const helloCache = new Map();         // "ip|port|host" -> { st, exp }
async function registerHello({ ip, port, hostname }) {
  const k = `${ip}|${port ?? ""}|${hostname ?? ""}`;
  const hit = helloCache.get(k);
  if (hit && hit.exp > Date.now()) return hit.st;
  const st = await db.upsertFdRegistry({ ip, port, hostname });
  helloCache.set(k, { st, exp: Date.now() + HELLO_TTL_MS });
  if (helloCache.size > 5000) helloCache.clear();
  return st;
}
const hbServer = net.createServer((sock) => {
  const remoteIp = String(sock.remoteAddress).replace(/^::ffff:/, "");
  let ident = null;                   // { line, station_id, key }
  let buf = "";
  let chain = Promise.resolve();      // proses pesan berurutan (hello harus selesai dulu)

  sock.setEncoding("utf8");
  sock.setKeepAlive(true, 10_000);
  sock.setTimeout(15_000);            // sebelum hello: 15 detik (saat restart massal, antrean DB bisa lebih dari 5 detik)
  sock.on("timeout", () => { sock._why = sock._why || "idle-timeout"; sock.destroy(); });
  sock.on("error", (e) => { sock._why = sock._why || `error:${e.code || e.message}`; });

async function handleLine(line) {
  let msg;
  try { msg = JSON.parse(line); } catch { return; }

  if (msg.type === "hello") {
    let st;
    try {
      st = await registerHello({ ip: remoteIp, port: msg.port, hostname: msg.hostname });
    } catch (e) {
      log(`[FD-HB] ditolak ${remoteIp}: ${e.message}`);
      sock.end(JSON.stringify({ type: "error", error: e.message }) + "\n");
      return;
    }
    const key = `${st.line}-${st.station_id}`;
    const prev = sessions.get(key);
    const wasOnline = !!prev;
    if (prev) {
      clearTimeout(prev.offlineTimer);
      if (prev.sock !== sock) { prev.sock._why = "replaced-by-new-hello"; prev.sock.destroy(); }
    }
    sessions.set(key, {
      sock, gen: ++genCounter, ip: remoteIp, port: st.port,
      ...newStats(prev), offlineTimer: null,
    });
    fdAddr.set(key, `http://${remoteIp}:${st.port}`);
    ident = { line: st.line, station_id: st.station_id, key };
    const wasSaving = savingKeys.delete(key);   // hello = FD tidak saving (aturan emas)

    sock.setTimeout(0);                       // phi menggantikan idle timeout
    sock.write(JSON.stringify({ type: "ack" }) + "\n");
    if (!wasOnline || wasSaving) {
      emitLine(st.line, "fd_status", {
        line: st.line, station_id: st.station_id, online: true, saving: false,
      });
      engine.markDirty(st.line, st.station_id);
    }
    return;
  }

  if (!ident) return;
  const s = sessions.get(ident.key);
  if (!s || s.sock !== sock) return;
  recordArrival(s);
  seenDirty.add(ident.key);                   // pengganti touch()
  if (msg.type === "pong") onPong(s, msg);
  else if (msg.type === "state") pushState(ident.line, ident.station_id, msg);
}

  sock.on("data", (chunk) => {
    buf += chunk;
    //    if (buf.length > 65_536) return sock.destroy();   // proteksi buffer
    if (buf.length > 65_536) { sock._why = "buffer-overflow"; return sock.destroy(); }
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (line)
        chain = chain.then(() => handleLine(line))
          .catch((e) => console.error("[FD-HB]", e.message));
    }
  });
  sock.on("close", (hadError) => {
    log(`[FD-HB] close ${ident?.key ?? remoteIp} why=${sock._why || (hadError ? "error" : "remote-closed")}`);
    const s = ident && sessions.get(ident.key);
    if (s && s.sock === sock && !s.probing) judge(ident.key, s);   // hanya jika ini sesi aktif
  });
});

hbServer.on("error", (e) => console.error("[FD-HB] listener error:", e.message));
hbServer.listen(HB_PORT, "0.0.0.0", () => log(`FD heartbeat socket di port ${HB_PORT}`));
const io = new Server(server, {
  cors: { origin: allowedOrigins, methods: ["GET", "POST"], credentials: true },
  transports: ["websocket"],        // lewati long-polling
  perMessageDeflate: false,         // hemat CPU; payload kecil
  pingInterval: 20000, pingTimeout: 20000,
});
const PORT = process.env.PORT || 3000;
app.use(cors({
  origin: allowedOrigins,
  exposedHeaders: ["Content-Range", "Accept-Ranges", "Content-Length", "X-Total-Count"],
  credentials: true,
}));
app.use(express.json());

app.use((req, res, next) => {
  const t0 = Date.now();
  const ip = String(req.ip).replace(/^::ffff:/, "");
  res.on("finish", () => {
    const ms = Date.now() - t0;
    if (req.originalUrl.startsWith("/api/") && (ms > 500 || res.statusCode >= 400))
      log(`HTTP ${req.method} ${req.originalUrl} → ${res.statusCode} ${ms}ms dari ${ip}`);
  });
  res.on("close", () => {
    if (!res.writableFinished)
      log(`HTTP ${req.method} ${req.originalUrl} PUTUS sebelum dibalas (${Date.now() - t0}ms) dari ${ip}`);
  });
  next();
});
const { monitorEventLoopDelay } = require("perf_hooks");
const loopH = monitorEventLoopDelay({ resolution: 20 });
loopH.enable();
setInterval(() => {
  const m = process.memoryUsage();
  const mb = (n) => (n / 1e6) | 0;
  log(`[perf] loop p99=${(loopH.percentile(99) / 1e6).toFixed(1)}ms queue=${engine.queueLength} ` +
    `fd=${sessions.size} dash=${io.engine.clientsCount} live=${liveLimit.count()} vid=${videoLimit.count()} ` +
    `rss=${mb(m.rss)}MB heapUsed=${mb(m.heapUsed)}MB heapTotal=${mb(m.heapTotal)}MB ` +
    `ext=${mb(m.external)}MB buf=${mb(m.arrayBuffers)}MB`);
  loopH.reset();
}, 10_000).unref();
function log(msg) { console.log(`[${new Date().toISOString()}] ${msg}`); }
const q = async (text, params = []) => (await db.getPool()).query(text, params);
// ── batas koneksi proxy video/stream: melindungi bandwidth & CPU server ──
function limiter(name, max) {
  let n = 0;
  const mw = (req, res, next) => {
    if (n >= max) {
      res.set("Retry-After", "3");
      return res.status(503).json({ error: `${name} penuh (${max}), coba lagi sebentar` });
    }
    n++;
    let freed = false;
    const free = () => { if (!freed) { freed = true; n--; } };
    res.on("close", free);                    // close selalu terpanggil: selesai normal maupun putus
    next();
  };
  mw.count = () => n;
  return mw;
}
const liveLimit = limiter("live stream", Number(process.env.MAX_LIVE_STREAMS || 30));
const videoLimit = limiter("proxy video", Number(process.env.MAX_VIDEO_PROXY || 20));
app.post('/api/fd/register', async (req, res) => {
  const ip = String(req.ip).replace(/^::ffff:/, "");
  const { port, hostname } = req.body || {};
  try {
    log(`[FD] register masuk dari ${ip}:${port} host=${hostname}`);
    let station;
    try {
      station = await db.upsertFdRegistry({ ip, port, hostname });
    } catch (e) {
      return res.status(403).json({ error: e.message });
    }
    const key = keyOf(station.line, station.station_id);
    fdAddr.set(key, `http://${ip}:${station.port}`);
    if (req.body?.saving === true && !savingKeys.has(key))
      await enterSaving(station.line, station.station_id);
    console.log(
      `[FD] Registered: L${station.line} ST${station.station_id} ` +
      `ip=${ip}:${port} host=${hostname}`
    );
    res.json({
      registered: true,
      line: station.line,
      station_id: station.station_id,   // nomor station asli; identitas = (line, station_id)
      name: station.name,
      takt_time: station.takt_time,
      target_cycle_time: station.target_cycle_time ?? station.takt_time,
      roi_timeout_sec: station.roi_timeout_sec,
      min_cycle_sec: station.min_cycle_sec ?? null,
      short_anomaly_sec: station.short_anomaly_sec ?? null,
      ip, port,
      hostname: hostname || station.hostname || null,
    });
  } catch (e) {
    console.error('[FD] Register error:', e.message);
    res.status(500).json({ error: e.message });
  }
});
setInterval(() => {                       // ping, kirim ke semua sesi
  const t = Date.now();
  for (const s of sessions.values())
    if (!s.sock.destroyed) s.sock.write(JSON.stringify({ type: "ping", t }) + "\n");
}, 5000);

setInterval(() => {
  const now = Date.now();
  for (const [key, s] of sessions) {
    s.phi = phi(s, now);
    if (s.phi >= PHI_SUSPECT && !s.probing && now >= (s.nextJudge || 0)) judge(key, s);
  }
}, 1000);

app.post('/api/fd/heartbeat', async (req, res) => {
  try {
    const { ip, port } = req.body;
    if (!ip)
      return res.status(400).json({ error: 'ip wajib ada' });
    const n = await db.updateFdHeartbeat({ ip, port });
    if (!n)
      return res.status(404).json({ error: 'IP tidak terdaftar' });
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get('/api/fd/status', async (req, res) => {
  try {
res.json((await db.getFdStatus()).map((r) => ({
  ...r, online: isOnline(r.line, r.station_id), saving: isSaving(r.line, r.station_id),
})));
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});
async function getFdAddress(line, station_id) {
  return fdAddr.get(`${Number(line)}-${Number(station_id)}`) || null;
}
function newStats(prev) {
  return {
    win: prev?.win ?? [],          // riwayat jeda antar pesan (ms)
    last: Date.now(),
    srtt: prev?.srtt ?? null,
    rttvar: prev?.rttvar ?? null,
    state: "ONLINE", phi: 0, probeFails: 0, probing: false,
  };
}

function recordArrival(s) {
  const now = Date.now();
  const gap = now - s.last;
  if (gap < 15000) {
    s.win.push(gap);
    if (s.win.length > 100) s.win.shift();
  }
  s.last = now;
  if (s.state !== "ONLINE") { s.state = "ONLINE"; s.probeFails = 0; s.degradedSince = 0; }
}

async function judge(key, s) {
  const [line, station_id] = key.split("-").map(Number);
  s.nextJudge = Date.now() + 3000;
  s.probing = true; s.state = "SUSPECT";
  if (!s.degradedSince) s.degradedSince = Date.now();

  const tag = `[JUDGE] L${line} ST${station_id}`;
  const sinceLast = Date.now() - s.last;
  const t0 = Date.now();
  log(`${tag} mulai phi=${(s.phi ?? 0).toFixed(1)} sinceLastMsg=${sinceLast}ms ` +
      `samples=${s.win.length} srtt=${s.srtt != null ? Math.round(s.srtt) : "-"}ms ` +
      `sockDestroyed=${s.sock.destroyed} fails=${s.probeFails}`);
  try {
    await fdGetJson(fdAddr.get(key), "/health", 2500);
    log(`${tag} /health OK ${Date.now() - t0}ms`);
    s.probeFails = 0;
    s.state = "DEGRADED";
    if (!s.sock.destroyed) { s.sock._why = "phi-suspect-health-ok"; s.sock.destroy(); }
    const degradedFor = Date.now() - s.degradedSince;
    if (degradedFor > 60_000) {
      log(`${tag} OFFLINE: health OK tapi HB tidak pulih ${Math.round(degradedFor / 1000)}s`);
      s.probing = false;
      return finalizeOffline(key, s.gen, line, station_id);
    }
  } catch (e) {
    s.probeFails++;
    log(`${tag} /health GAGAL (${e.message}) ${Date.now() - t0}ms fails=${s.probeFails}/2`);
    if (s.probeFails >= 2) {
      log(`${tag} OFFLINE: health gagal 2x`);
      s.probing = false;
      return finalizeOffline(key, s.gen, line, station_id);
    }
  }
  s.probing = false;
}

function onPong(s, msg) {
  const rtt = Date.now() - msg.t;
  if (s.srtt == null) { s.srtt = rtt; s.rttvar = rtt / 2; }
  else {
    s.rttvar = 0.75 * s.rttvar + 0.25 * Math.abs(s.srtt - rtt);
    s.srtt   = 0.875 * s.srtt + 0.125 * rtt;
  }
}

const ACCEPTABLE_PAUSE_MS = 2000;
const MIN_SD_MS = 500;
const PHI_SUSPECT = 8;

function phi(s, now) {
  if (s.win.length < 10) return (now - s.last) > 15000 ? 99 : 0;   // belum cukup sampel
  const mean = s.win.reduce((a, b) => a + b, 0) / s.win.length + ACCEPTABLE_PAUSE_MS;
  const v = s.win.reduce((a, b) => a + (b - (mean - ACCEPTABLE_PAUSE_MS)) ** 2, 0) / s.win.length;
  const sd = Math.max(Math.sqrt(v), MIN_SD_MS);
  const dt = now - s.last;
  const y = (dt - mean) / sd;
  const e = Math.exp(-y * (1.5976 + 0.070566 * y * y));
  const p = dt > mean ? e / (1 + e) : 1 - 1 / (1 + e);
  return Math.min(16, -Math.log10(Math.max(p, 1e-16)));
}
// helper: proxy aman ke FD
function proxyToFd(req, res, options, { forwardHeaders = [], requireOk = false } = {}) {
  const upstream = http.get({ ...options, agent: false }, (fdRes) => {   // ← ubah: agent: false
    if (requireOk && fdRes.statusCode !== 200) {
      fdRes.resume();
      if (!res.headersSent)
        res.status(502).json({ error: `FD membalas ${fdRes.statusCode}` });
      return;
    }
    res.status(fdRes.statusCode);
    forwardHeaders.forEach((h) => {
      if (fdRes.headers[h]) res.setHeader(h, fdRes.headers[h]);
    });
    res.socket?.setNoDelay(true);                                         // ← tambah: sisi browser
    fdRes.pipe(res);
    fdRes.on("error", () => res.end());
  });

  upstream.on("socket", (s) => s.setNoDelay(true));                       // ← tambah: sisi FD

  upstream.on("error", () => {
    if (!res.headersSent) res.status(502).json({ error: "FD tidak bisa dijangkau" });
    else res.end();
  });

  // browser menutup koneksi -> putuskan ke FD
  res.on("close", () => upstream.destroy());
}


app.get("/api/fd/:line/:station/stream", liveLimit, async (req, res) => {
  let upstream = null;
  let fdRes = null;
  let cleaned = false;

  // Pastikan koneksi ke FD dihentikan ketika browser benar-benar putus.
  const cleanup = () => {
    if (cleaned) return;
    cleaned = true;

    if (fdRes && !fdRes.destroyed) {
      fdRes.destroy();
    }

    if (upstream && !upstream.destroyed) {
      upstream.destroy();
    }
  };

  try {
    const skey = keyOf(req.params.line, req.params.station);
    if (savingKeys.has(skey))
      return res.status(409).json({ error: "FD sedang Saving Mode" });

    const stop = () => { cleanup(); if (!res.destroyed) res.destroy(); };
    if (!liveStreams.has(skey)) liveStreams.set(skey, new Set());
    liveStreams.get(skey).add(stop);
    res.on("close", () => {
      const set = liveStreams.get(skey);
      if (set) { set.delete(stop); if (!set.size) liveStreams.delete(skey); }
    });
    const addr = await getFdAddress(
      req.params.line,
      req.params.station
    );

    if (!addr) {
      return res.status(503).json({
        error: "FD offline atau tidak terdaftar"
      });
    }

    const fd = new URL(addr);

    upstream = http.get({
      hostname: fd.hostname,
      port: fd.port,
      path: "/stream",
      agent: false,
      headers: {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive"
      }
    }, (response) => {
      fdRes = response;

      // Browser mungkin sudah putus saat FD baru menjawab.
      if (res.destroyed || res.writableEnded) {
        cleanup();
        return;
      }

      if (response.statusCode !== 200) {
        response.resume();

        if (!res.headersSent) {
          res.status(502).json({
            error: `FD membalas ${response.statusCode}`
          });
        } else {
          res.destroy();
        }

        cleanup();
        return;
      }

      res.statusCode = 200;

      res.setHeader(
        "Content-Type",
        response.headers["content-type"] ||
          "multipart/x-mixed-replace; boundary=frame"
      );

      res.setHeader(
        "Cache-Control",
        "no-cache, no-store, must-revalidate"
      );
      res.setHeader("Pragma", "no-cache");
      res.setHeader("Expires", "0");
      res.setHeader("X-Accel-Buffering", "no");

      res.socket?.setNoDelay(true);
      response.socket?.setNoDelay(true);

      // Teruskan frame dari FD ke browser.
      response.pipe(res);
    });

    upstream.on("socket", (socket) => {
      socket.setNoDelay(true);
      socket.setKeepAlive(true, 10_000);
    });

    upstream.on("error", (err) => {
      console.error("[STREAM] FD upstream:", err.message);

      if (!res.headersSent) {
        res.status(502).json({
          error: "FD tidak bisa dijangkau"
        });
      } else if (!res.destroyed) {
        res.destroy();
      }

      cleanup();
    });

    upstream.setTimeout(10_000, () => {
      // Timeout hanya untuk koneksi awal/header FD,
      // bukan untuk durasi livestream setelah tersambung.
      if (!fdRes) {
        upstream.destroy(
          new Error("Timeout menunggu respons awal FD")
        );
      }
    });

    res.on("close", () => {
      // close normal setelah respons selesai tidak perlu dianggap
      // sebagai browser yang memutus streaming secara mendadak.
      if (!res.writableFinished) {
        cleanup();
      }
    });

    res.on("error", cleanup);

    req.on("aborted", cleanup);
  } catch (err) {
    console.error("[STREAM] Proxy error:", err.message);

    cleanup();

    if (!res.headersSent && !res.destroyed) {
      res.status(500).json({
        error: err.message
      });
    }
  }
});
app.get("/api/health", async (req, res) => {
  try {
    await q("SELECT 1 AS ok");
    res.json({ db: true });
  } catch (e) {
    res.status(503).json({ db: false, error: e.message });
  }
});
function fdSendJson(addr, path, method, payload, timeout = 4000) {
  return new Promise((resolve, reject) => {
    const fd = new URL(addr);
    const data = JSON.stringify(payload ?? {});
    const r = http.request({
      hostname: fd.hostname, port: fd.port, path, method, agent: false, timeout,
      headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) },
    }, (fdRes) => {
      let body = "";
      fdRes.on("data", (d) => body += d);
      fdRes.on("end", () => {
        if (fdRes.statusCode !== 200) return reject(new Error(`FD ${fdRes.statusCode}`));
        try { resolve(body ? JSON.parse(body) : {}); } catch { reject(new Error("parse error")); }
      });
    });
    r.on("error", reject);
    r.on("timeout", () => { r.destroy(); reject(new Error("timeout")); });
    r.end(data);
  });
}
async function bootstrapSaving() {
  let rows;
  try { rows = await db.getFdStatus(); }
  catch (e) { return console.error("[saving boot] DB:", e.message); }

  const todo = rows.filter((r) => r.ip && r.port);
  let found = 0, i = 0;
  const worker = async () => {
    while (i < todo.length) {
      const r = todo[i++];
      const key = keyOf(r.line, r.station_id);
      try {
        const j = await fdGetJson(`http://${r.ip}:${r.port}`, "/saving-mode", 3000);
        if (!fdAddr.has(key)) fdAddr.set(key, `http://${r.ip}:${r.port}`);
        if (j.saving === true && !savingKeys.has(key)) {
          found++;
          await enterSaving(r.line, r.station_id);
        }
      } catch { /* FD tidak menjawab: dianggap offline, bukan saving */ }
    }
  };
  await Promise.all(Array.from({ length: 20 }, worker));   // maksimal 20 permintaan bersamaan
  log(`[SAVING] bootstrap selesai: ${found} FD saving dari ${todo.length} dicek`);
}
async function enterSaving(line, station_id) {
  const key = keyOf(line, station_id);
  savingKeys.add(key);                         // tambah dulu supaya isOnline tidak sempat false
  const s = sessions.get(key);
  if (s) {
    clearTimeout(s.offlineTimer);
    sessions.delete(key);                      // hapus sesi SEBELUM destroy, supaya "close" tidak memanggil judge()
    s.sock._why = "saving-mode";
    s.sock.destroy();
  }
  engine.markDirty(line, station_id);
  for (const stop of [...(liveStreams.get(key) || [])]) stop();
  liveStreams.delete(key);
  await clearHb(line, station_id);             // tunggu tulis yang sedang jalan selesai
  if (!savingKeys.has(key)) return;            // hello datang saat menunggu: batalkan
  emitLine(line, "fd_status", { line, station_id, online: true, saving: true });
  emitLine(line, "heartbeat", { line, station_id, state: "SAVING_MODE", elapsed_sec: null, roi_idle_sec: null });
  try { await db.upsertHeartbeat(line, station_id, "SAVING_MODE", null); }
  catch (e) { console.error("[saving db]", e.message); }
  log(`[SAVING] masuk L${line} ST${station_id}`);
}

app.put("/api/station/:line/:station/saving-mode", async (req, res) => {
  try {
    const line = Number(req.params.line), sid = Number(req.params.station);
    const enabled = req.body?.enabled;
    if (!Number.isInteger(line) || !Number.isInteger(sid) || typeof enabled !== "boolean")
      return res.status(400).json({ error: "line, station, dan enabled (boolean) wajib ada" });

    const key = keyOf(line, sid);
    const addr = fdAddr.get(key);
    if (!addr) return res.status(503).json({ error: "FD tidak terjangkau atau belum terdaftar" });

    try {
      await fdSendJson(addr, "/saving-mode", "POST", { enabled });   // FD dulu; gagal = status tidak berubah
    } catch (e) {
      return res.status(502).json({ error: `FD gagal mengubah mode: ${e.message}` });
    }

    if (enabled) await enterSaving(line, sid);
    // keluar mode: flag TIDAK dihapus di sini; hanya hello dari FD yang menghapusnya (aturan emas)

    res.json({ ok: true, line, station_id: sid, saving: enabled ? true : isSaving(line, sid) });
  } catch (err) {
    console.error("[saving-mode]", err.message);
    res.status(500).json({ error: err.message });
  }
});
function fdGetJson(addr, path, timeout = 4000) {
  return new Promise((resolve, reject) => {
    const fd = new URL(addr);
    const r = http.get({ hostname: fd.hostname, port: fd.port, path, agent: false, timeout }, (fdRes) => {
      let body = "";
      fdRes.on("data", d => body += d);
      fdRes.on("end", () => {
        if (fdRes.statusCode !== 200) return reject(new Error(`FD ${fdRes.statusCode}`));
        try { resolve(JSON.parse(body)); } catch { reject(new Error("parse error")); }
      });
    });
    r.on("error", reject);
    r.on("timeout", () => { r.destroy(); reject(new Error("timeout")); });
  });
}

// Video yang ada di FD tetapi belum tercatat di DB
// app.get("/api/fd/:line/:station/videos/buffer", async (req, res) => {
//   try {
//     const { line, station } = req.params;
//     const addr = await getFdAddress(line, station);
//     if (!addr) return res.status(503).json({ error: "FD offline atau tidak terdaftar" });

//     const files = await fdGetJson(addr, "/videos/list");
//     if (!files.length) return res.json([]);

//     const oldest = Math.min(...files.map(f => f.mtime)) * 1000 - 3600_000;
//     const known = await db.getRecordedFilenames(line, station, new Date(oldest));

//     res.json(
//       files
//         .filter(f => !known.has(f.filename))
//         .sort((a, b) => b.mtime - a.mtime)
//         .map(f => ({
//           id: f.filename,
//           line: Number(line),
//           station_id: Number(station),
//           filename: f.filename,
//           size_mb: f.size_mb,
//           is_anomaly: f.type === "anomaly",
//           recorded_at: new Date(f.mtime * 1000).toISOString(),
//           is_buffer: true,        // ← badge BUFFER
//           pending: !!f.pending,
//           video_type: f.type,     // dipakai untuk URL stream
//         }))
//     );
//   } catch (e) {
//     res.status(502).json({ error: e.message });
//   }
// });

app.get("/api/fd/:line/:station/video/:type/:filename", videoLimit, async (req, res) => {
  try {
    const addr = await getFdAddress(req.params.line, req.params.station);
    if (!addr) return res.status(503).json({ error: "FD offline atau tidak terdaftar" });

    const { type, filename } = req.params;
    if (!["normal", "anomaly"].includes(type))
      return res.status(400).json({ error: "type harus normal atau anomaly" });
    if (filename.includes("..") || filename.includes("/") || filename.includes("\\"))
      return res.status(400).json({ error: "Nama file tidak valid" });

    if (req.query.download === "1")
      res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);

    const fd = new URL(addr);
    const headers = {};
    if (req.headers.range) headers["Range"] = req.headers.range;

    proxyToFd(req, res,
      {
        hostname: fd.hostname, port: fd.port,
        path: `/videos/${type}/${encodeURIComponent(filename)}`, headers
      },
      { forwardHeaders: ["content-type", "content-length", "content-range", "accept-ranges"] });
  } catch (e) {
    if (!res.headersSent) res.status(500).json({ error: e.message });
  }
});

app.post("/api/cycle", async (req, res) => {
  if (!engine.ready) return res.status(503).json({ error: "server belum siap" });   // FD akan buffer dan retry
  try {
    const data = req.body;
    const line = Number(data.line);
    const station_id = Number(data.station_id);
    if (!Number.isInteger(line) || !Number.isInteger(station_id)
      || !(Number(data.cycle_time_sec) > 0))
      return res.status(400).json({
        error: "line, station_id, dan cycle_time_sec wajib ada"
      });

    // metadata dari memori, tanpa query SQL
    const st = engine.get(line, station_id);
    if (!st.meta)
      return res.status(404).json({ error: `Station L${line}-ST${station_id} tidak ditemukan` });
    data.line = line;
    data.station_id = station_id;
    data.takt_time = st.meta.takt_time || 0;
    data.target_cycle_time = st.meta.target_cycle_time || 0;

    if (data.is_complete === undefined) data.is_complete = true;
    data.label = String(data.label || "").toUpperCase();
    data.is_genesis = data.is_genesis === true || data.label === "GENESIS";
    data.is_buffered = data.buffered === true;
    const endTs = Number(data.end_timestamp);
    if (!Number.isFinite(endTs) || endTs <= 0)
      return res.status(400).json({ error: "end_timestamp dari FD wajib ada" });
    data.end_timestamp = endTs;

    const startTs = Number(data.start_timestamp);
    data.start_timestamp = Number.isFinite(startTs) && startTs > 0
      ? startTs
      : endTs - data.cycle_time_sec;

    const anomaly_type = db.resolveAnomalyType(data);
    const skippedList = Array.isArray(data.skipped_expected)
      ? data.skipped_expected
          .map((x) => String(x?.description || x?.id || "").trim())
          .filter(Boolean)
      : [];
    const skipped_expected = skippedList.length ? skippedList.join("; ").slice(0, 500) : null;
    const lbl = String(data.anomaly_label || "").trim();
    const anomaly_label = lbl && lbl !== "OK" ? lbl.slice(0, 300) : null;
    const is_anomaly = anomaly_type !== "OK" || skippedList.length > 0;

    if (!st.reserve(endTs)) {
      if (data.video?.filename) {
        db.attachVideo({ line, station_id, end_timestamp: endTs, video: data.video, label: data.label })
          .then((n) => n && log(`VIDEO dipulihkan: ${data.video.filename}`))
          .catch((e) => console.error("[video recover]", e.message));
      }
      return res.status(200).json({ ok: true, duplicate: true });
    }

    const row = {
      line, station_id,
      operator_id: data.operator_id || null,
      cycle_time_sec: data.cycle_time_sec,
      start_timestamp: data.start_timestamp,
      end_timestamp: endTs,
      is_complete: data.is_complete ? 1 : 0,
      is_anomaly: is_anomaly ? 1 : 0,
      anomaly_type,
      skipped_expected,
      anomaly_label,
      is_genesis: data.is_genesis ? 1 : 0,
      is_buffered: data.is_buffered ? 1 : 0,
      target_limit_sec: st.meta.target_cycle_time || null,   // baru
      short_limit_sec: st.meta.short_anomaly_sec || null,    // baru
      video: db.videoParams(data),
    };

    try {
      await engine.enqueue(row);          // durable: resolve setelah batch commit
    } catch (e) {
      st.rollback(endTs);
      if (e.code === "BACKPRESSURE") return res.status(503).set("Retry-After", "2").json({ error: "overloaded" });
      throw e;
    }

    // data terlambat dari shift lama tidak boleh masuk counter shift berjalan
    const live = st.isLive(endTs); if (live) st.apply(data.cycle_time_sec);
    const cycle_count = live ? st.cyclesShift : null;

    log(`CYCLE L${line} ST${station_id} n=${cycle_count ?? "-"} ct=${data.cycle_time_sec}s [${anomaly_type}${skipped_expected ? "+EXPECTED:" + skipped_expected : ""}]`
      + (data.is_buffered ? " [BUFFERED]" : ""));

    emitLine(line, "cycle_complete", {
      line, station_id, cycle_count, seq: st.seq,
      cycle_time_sec: data.cycle_time_sec,
      operator_id: data.operator_id || null,
      end_timestamp: endTs,
      is_anomaly, anomaly_type,
      anomaly_label,
      skipped_expected,
      is_genesis: data.is_genesis,
      label: data.label,
      is_buffered: data.is_buffered,
    });

    res.status(201).json({ ok: true, cycle_count });
    // summary_update TIDAK dipanggil di sini lagi; dikirim dari tick (tahap berikutnya)
  } catch (err) {
    console.error("[cycle]", err.message);
    res.status(500).json({ error: err.message });
  }
});
app.post("/api/heartbeat", (req, res) => {
  const line = Number(req.body.line);
  const station_id = Number(req.body.station_id);
  const { state } = req.body;
  if (!Number.isInteger(line) || !Number.isInteger(station_id) || !state)
    return res.status(400).json({ error: "line, station_id, dan state wajib ada" });

  if (isSaving(line, station_id)) return res.status(200).json({ ok: true, ignored: true });   // ← baris baru

  pushState(line, station_id, req.body);
  res.status(200).json({ ok: true });
});
app.get("/api/operator/:id", async (req, res) => {
  try {
    const r = await q(
      "SELECT id, name, shift FROM operators WHERE id = $1 AND is_active = true",
      [req.params.id.toUpperCase()]);
    if (!r.rows.length) return res.status(404).json({ exists: false });
    res.json({ exists: true, operator: r.rows[0] });
  } catch (err) {
    console.error("[operator check]", err.message);
    res.status(500).json({ error: err.message });
  }
});

app.post("/api/operator", async (req, res) => {
  try {
    const { id, name, shift } = req.body;
    if (!id) return res.status(400).json({ error: "id wajib ada" });
    const op_id = id.toUpperCase();
    const shiftFromId = op_id.match(/S([123])$/i)?.[1] || shift || "1";

    const r = await q(
      `INSERT INTO operators (id, name, shift) VALUES ($1, $2, $3)
       ON CONFLICT (id) DO NOTHING`,
      [op_id, name || op_id, String(shiftFromId)]);

    if (!r.rowCount)
      return res.json({ ok: true, created: false, message: "Operator sudah terdaftar" });
    log(`OPERATOR registered: ${op_id}`);
    res.status(201).json({ ok: true, created: true });
  } catch (err) {
    console.error("[operator register]", err.message);
    res.status(500).json({ error: err.message });
  }
});

app.get("/api/summary", (req, res) => res.json(engine.snapshotAll()));
const STATION_COLS = `station_id, line, name, ip, port, hostname,
  takt_time, target_cycle_time, roi_timeout_sec,
  min_cycle_sec, short_anomaly_sec, is_active, is_online, last_seen`;

app.get("/api/stations", async (req, res) => {
  try {
    const r = await q(`SELECT ${STATION_COLS} FROM stations ORDER BY line, station_id`);
    res.json(r.rows.map((x) => ({
      ...x, is_online: isOnline(x.line, x.station_id), saving: isSaving(x.line, x.station_id),
    })));
  } catch (err) {
    console.error("[station list]", err.message);
    res.status(500).json({ error: err.message });
  }
});

app.get("/api/fd/identity", async (req, res) => {
  try {
    const ip = req.query.ip || String(req.ip).replace(/^::ffff:/, "");
    const r = await q(
      `SELECT ${STATION_COLS} FROM stations WHERE ip = $1 ORDER BY line, station_id`, [ip]);
    if (!r.rows.length) return res.status(404).json({ error: "IP tidak terdaftar" });
    res.json(r.rows[0]);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});
app.post("/api/station/register", async (req, res) => {
  try {
    const { station_id, line, name, ip, takt_time, roi_timeout_sec } = req.body;

    if (station_id == null || line == null || !name || !ip
      || takt_time == null || roi_timeout_sec == null || roi_timeout_sec === "")
      return res.status(400).json({
        error: "station_id, line, name, ip, takt_time, dan roi_timeout_sec wajib diisi"
      });

    const sid = Number(station_id);
    const lineNumber = Number(line);
    const taktTime = Number(takt_time);
    const roiTimeout = Number(roi_timeout_sec);
    const cleanIp = String(ip).trim();

    if (!Number.isInteger(sid) || sid <= 0)
      return res.status(400).json({ error: "station_id harus integer positif" });
    if (!Number.isInteger(lineNumber) || lineNumber <= 0)
      return res.status(400).json({ error: "line harus integer positif" });
    if (!Number.isFinite(taktTime) || taktTime <= 0)
      return res.status(400).json({ error: "takt_time harus angka positif" });
    if (!Number.isFinite(roiTimeout) || roiTimeout <= 0)
      return res.status(400).json({ error: "roi_timeout_sec harus angka positif" });

    const ipRegex =
      /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
    if (!ipRegex.test(cleanIp))
      return res.status(400).json({ error: "Format IP tidak valid" });

    const dupKey = await q(
      "SELECT 1 FROM stations WHERE line = $1 AND station_id = $2", [lineNumber, sid]);
    if (dupKey.rows.length)
      return res.status(409).json({ error: `Station L${lineNumber}-ST${sid} sudah terdaftar` });

    const dupIp = await q("SELECT line, station_id, name FROM stations WHERE ip = $1", [cleanIp]);
    if (dupIp.rows.length) {
      const old = dupIp.rows[0];
      return res.status(409).json({
        error: `IP ${cleanIp} sudah dipakai L${old.line}-ST${old.station_id} (${old.name})`
      });
    }

    await q(
      `INSERT INTO stations
         (station_id, line, name, ip, takt_time, target_cycle_time,
          roi_timeout_sec, is_active, is_online)
       VALUES ($1,$2,$3,$4,$5,$5,$6,true,false)`,
      [sid, lineNumber, String(name).trim(), cleanIp, taktTime, roiTimeout]);

    log(`[STATION REGISTER] L${lineNumber} ST${sid} | ${name} | IP=${cleanIp}`);

    res.status(201).json({
      ok: true,
      station: {
        line: lineNumber,
        station_id: sid,
        name: String(name).trim(),
        ip: cleanIp,
        takt_time: taktTime,
        target_cycle_time: taktTime,
        roi_timeout_sec: roiTimeout,
        is_active: true,
        is_online: false,
      },
    });
  } catch (err) {
    console.error("[station register]", err.message);
    res.status(500).json({ error: err.message });
  }
});
const IP_RE = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;

// UPDATE: identitas (line, station_id) tidak bisa diubah; field lain opsional
app.put("/api/station/:line/:station", async (req, res) => {  
  try {
    const line = Number(req.params.line), sid = Number(req.params.station);
    const cur = (await q(
      `SELECT name, ip, takt_time, roi_timeout_sec, is_active
       FROM stations WHERE line = $1 AND station_id = $2`, [line, sid])).rows[0];
    if (!cur)
      return res.status(404).json({ error: `Station L${line}-ST${sid} tidak ditemukan` });

    const b = req.body || {};
    const next = { ...cur };

    if (b.name !== undefined) {
      next.name = String(b.name).trim();
      if (!next.name) return res.status(400).json({ error: "name tidak boleh kosong" });
    }
    if (b.ip !== undefined) {
      next.ip = String(b.ip).trim();
      if (!IP_RE.test(next.ip)) return res.status(400).json({ error: "Format IP tidak valid" });
    }
    if (b.takt_time !== undefined) {
      next.takt_time = Number(b.takt_time);
      if (!Number.isFinite(next.takt_time) || next.takt_time <= 0)
        return res.status(400).json({ error: "takt_time harus angka positif" });
    }
    if (b.roi_timeout_sec !== undefined) {
      next.roi_timeout_sec = Number(b.roi_timeout_sec);
      if (!Number.isFinite(next.roi_timeout_sec) || next.roi_timeout_sec <= 0)
        return res.status(400).json({ error: "roi_timeout_sec harus angka positif" });
    }
if (b.is_active !== undefined) next.is_active = !!b.is_active;

    const ipChanged = next.ip !== cur.ip;
    const taktChanged = next.takt_time !== cur.takt_time;
    const roiChanged = next.roi_timeout_sec !== cur.roi_timeout_sec;

    if (ipChanged) {
      const dup = await q(
        `SELECT line, station_id, name FROM stations
         WHERE ip = $1 AND NOT (line = $2 AND station_id = $3)`, [next.ip, line, sid]);
      if (dup.rows.length) {
        const o = dup.rows[0];
        return res.status(409).json({ error: `IP ${next.ip} sudah dipakai L${o.line}-ST${o.station_id} (${o.name})` });
      }
    }
    await q(
      `UPDATE stations SET
         name = $3, ip = $4, takt_time = $5, roi_timeout_sec = $6, is_active = $7,
         target_cycle_time = CASE WHEN $8::boolean THEN $5 ELSE target_cycle_time END,
         last_seen = CASE WHEN $9::boolean THEN NULL ELSE last_seen END,
         is_online = CASE WHEN $9::boolean THEN false ELSE is_online END
       WHERE line = $1 AND station_id = $2`,
      [line, sid, next.name, next.ip, next.takt_time, next.roi_timeout_sec,
       !!next.is_active, taktChanged, ipChanged]);
    helloCache.clear();
    log(`STATION UPDATE L${line} ST${sid}`);
    // supaya FD yang sedang jalan langsung ikut nilai baru
    if (taktChanged)
      emitCfg(line, sid, "takt_time_update", { line, station_id: sid, takt_time: next.takt_time });
    if (roiChanged)
      emitCfg(line, sid, "roi_timeout_update", { line, station_id: sid, roi_timeout_sec: next.roi_timeout_sec });
    await broadcastSummary();
    emitLine(line, "station_update", {
      line, station_id: sid,
      name: next.name, ip: next.ip, is_active: !!next.is_active,
    });
    res.json({ ok: true, line, station_id: sid });
  } catch (err) {
    console.error("[station update]", err.message);
    res.status(500).json({ error: err.message });
  }
});

// DELETE permanen
app.delete("/api/station/:line/:station", async (req, res) => {
  try {
    const line = Number(req.params.line), sid = Number(req.params.station);
    const r = await q("DELETE FROM stations WHERE line = $1 AND station_id = $2", [line, sid]);
    if (!r.rowCount)
      return res.status(404).json({ error: `Station L${line}-ST${sid} tidak ditemukan` });
    helloCache.clear();
    log(`STATION DELETE L${line} ST${sid}`);
    await broadcastSummary(); res.json({ ok: true });
  }catch (err) {
    if (err.code === "23503")      // foreign_key_violation (sebelumnya 547)
      return res.status(409).json({ error: "Station masih punya data (cycle/heartbeat/video). Nonaktifkan saja, atau hapus data terkait dulu." });
    console.error("[station delete]", err.message);
    res.status(500).json({ error: err.message });
  }
});

app.put("/api/station/:line/:station/takt-time", async (req, res) => {
  try {
    const line = +req.params.line, sid = +req.params.station;
    const takt_time = Number(req.body.takt_time);
    if (!Number.isFinite(takt_time) || takt_time <= 0)
      return res.status(400).json({ error: "takt_time harus angka positif" });

    const r = await q(
      `UPDATE stations SET takt_time = $3, target_cycle_time = $3
      WHERE line = $1 AND station_id = $2`, [line, sid, takt_time]);
    if (!r.rowCount) return res.status(404).json({ error: `Station L${line}-ST${sid} tidak ditemukan` });

    log(`TAKT_TIME UPDATE L${line} ST${sid} → ${takt_time}s`);
    emitCfg(line, sid, "takt_time_update", { line, station_id: sid, takt_time });
    await broadcastSummary();
    res.json({ ok: true, line, station_id: sid, takt_time });
  } catch (err) {
    console.error("[takt_time update]", err.message);
    res.status(500).json({ error: err.message });
  }
});
app.put("/api/station/:line/:station/roi-timeout", async (req, res) => {
  try {
    const line = +req.params.line, sid = +req.params.station;
    const value = Number(req.body.roi_timeout_sec);
    if (!Number.isFinite(value) || value <= 0)
      return res.status(400).json({ error: "roi_timeout_sec harus angka positif" });

    const r = await q(
      "UPDATE stations SET roi_timeout_sec = $3 WHERE line = $1 AND station_id = $2",
      [line, sid, value]);
    if (!r.rowCount) return res.status(404).json({ error: `Station L${line}-ST${sid} tidak ditemukan` });

    log(`ROI_TIMEOUT UPDATE L${line} ST${sid} → ${value}s`);
    emitCfg(line, sid, "roi_timeout_update", { line, station_id: sid, roi_timeout_sec: value });
    await broadcastSummary();
    res.json({ ok: true, line, station_id: sid, roi_timeout_sec: value });
  } catch (err) {
    console.error("[roi-timeout]", err.message);
    res.status(500).json({ error: err.message });
  }
});
app.put("/api/station/:line/:station_id/min-cycle", async (req, res) => {
  try {
    const line = Number(req.params.line);
    const station_id = Number(req.params.station_id);
    const v = Number(req.body.min_cycle_sec);

    if (!Number.isInteger(line) || !Number.isInteger(station_id))
      return res.status(400).json({ error: "line dan station_id tidak valid" });
    if (!Number.isFinite(v) || v <= 0)
      return res.status(400).json({ error: "min_cycle_sec harus angka lebih besar dari 0" });

    const n = await db.updateMinCycle(line, station_id, v);
    if (n === 0) return res.status(404).json({ error: "Station tidak ditemukan" });

    emitCfg(line, station_id, "min_cycle_update", { line, station_id, min_cycle_sec: v });
    res.json({ ok: true });
  } catch (err) {
    console.error("[min-cycle]", err.message);
    res.status(500).json({ error: err.message });
  }
});
app.put("/api/station/:line/:station_id/short-anomaly", async (req, res) => {
  try {
    const line = Number(req.params.line);
    const station_id = Number(req.params.station_id);
    const v = Number(req.body.short_anomaly_sec);

    if (!Number.isInteger(line) || !Number.isInteger(station_id))
      return res.status(400).json({ error: "line dan station_id tidak valid" });
    if (!Number.isFinite(v) || v < 0)
      return res.status(400).json({ error: "short_anomaly_sec harus angka >= 0 (0 = nonaktif)" });

    const st = (await q(
      `SELECT min_cycle_sec, target_cycle_time FROM stations
      WHERE line = $1 AND station_id = $2`, [line, station_id])).rows[0];
    if (!st) return res.status(404).json({ error: "Station tidak ditemukan" });

    if (v > 0) {
      if (st.min_cycle_sec != null && v <= st.min_cycle_sec)
        return res.status(400).json({ error: `Short limit harus > min cycle (${st.min_cycle_sec}s)` });
      if (st.target_cycle_time > 0 && v >= st.target_cycle_time)
        return res.status(400).json({ error: `Short limit harus < anomaly limit (${st.target_cycle_time}s)` });
    }

    await db.updateShortAnomaly(line, station_id, v);
    log(`SHORT_ANOMALY UPDATE L${line} ST${station_id} → ${v}s`);
    emitCfg(line, station_id, "short_anomaly_update", { line, station_id, short_anomaly_sec: v });
    await broadcastSummary();
    res.json({ ok: true, line, station_id, short_anomaly_sec: v });
  } catch (err) {
    console.error("[short-anomaly]", err.message);
    res.status(500).json({ error: err.message });
  }
});
app.get("/api/station/:line/:station/export", async (req, res) => {
  try {
    const line = +req.params.line, sid = +req.params.station;
    const { from, to, shift } = req.query;
    const rows = await db.getCyclesInRange(line, sid, { from, to, shift });

    const csvCell = (v) => {
      const s = v == null ? "" : String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const header = "line,station_id,cycle_count,cycle_time_sec,start_timestamp,end_timestamp,operator_id,is_anomaly,anomaly_type,is_genesis,is_buffered,shift,anomaly_label,skipped_expected\n";
    const csv = rows.map(r => [
      line, sid, r.cycle_count, r.cycle_time_sec,
      new Date(r.start_timestamp).toISOString(),
      new Date(r.end_timestamp).toISOString(),
      r.operator_id || "", r.is_anomaly ? 1 : 0,
      r.anomaly_type || (r.is_anomaly ? "LONG" : "OK"),
      r.is_genesis ? 1 : 0,
      r.is_buffered ? 1 : 0,
      r.shift,
      r.anomaly_label || "",
      r.skipped_expected || "",
    ].map(csvCell).join(",")).join("\n");

    const dateStr = new Date().toISOString().slice(0, 10);
    res.setHeader("Content-Type", "text/csv");
    res.setHeader("Content-Disposition", `attachment; filename="ct_L${line}_ST${sid}_${dateStr}.csv"`);
    res.send(header + csv);
    log(`EXPORT L${line} ST${sid} rows=${rows.length}`);
  } catch (e) { res.status(500).json({ error: e.message }); }
});
app.get("/api/station/:line/:station/history", async (req, res) => {
  try {
    const line = +req.params.line, sid = +req.params.station;
    const { from, to, shift, limit } = req.query;
    res.json(from || to
      ? await db.getRecentInRange(line, sid, { from, to, shift }, limit || 100)
      : await db.getRecentCycles(line, sid, shift || null, limit || 100));
  } catch (e) { res.status(500).json({ error: e.message }); }
});
app.get("/api/station/:line/:station/stats", async (req, res) => {
  try {
    const { from, to, shift } = req.query;
    res.json(await db.getStationStats(+req.params.line, +req.params.station, { from, to, shift }));
  } catch (e) { res.status(500).json({ error: e.message }); }
});
app.get("/api/station/:line/:station/hourly", async (req, res) => {
  try {
    const { from, to, shift } = req.query;
    res.json(await db.getBucketStats(+req.params.line, +req.params.station, { from, to, shift }));
  } catch (e) { res.status(500).json({ error: e.message }); }
});
app.post("/api/video/update-size", async (req, res) => {
  try {
    const { filename, size_mb } = req.body;
    if (!filename || size_mb == null)
      return res.status(400).json({ error: "filename dan size_mb wajib ada" });
    const n = await db.updateVideoSize(filename, size_mb);
    if (!n) return res.status(404).json({ error: "video belum tercatat" });
    res.json({ ok: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});
app.get("/api/videos", async (req, res) => {
  try {
    const { line = null, station_id = null } = req.query;
    if (station_id !== null && station_id !== "" && (line === null || line === ""))
      return res.status(400).json({ error: "station_id harus disertai line" });

    const { rows, total } = await db.getVideos({
      line, station_id,
      anomaly_only: req.query.anomaly === "true",
      normal_only: req.query.anomaly === "false",
      atype: req.query.atype,
      genesis: req.query.genesis === "true",
      late: req.query.late === "true",
      expected: req.query.expected === "true",
      from: req.query.from,        // ← tambah
      to: req.query.to,            // ← tambah
      sort: req.query.sort,
      dir: req.query.dir,
      limit: req.query.limit,
      offset: req.query.offset,
    });
    res.setHeader("X-Total-Count", total);
    res.json(rows);
  } catch (err) {
    console.error("[GET /api/videos]", err.message);
    res.status(err.status || 500).json({ error: err.message });
  }
});
app.get("/api/video/:videoId/stream", videoLimit, async (req, res) => {
  try {
    const row = await db.getVideoByFilename(req.params.videoId);
    if (!row) return res.status(404).json({ error: "Video tidak ditemukan di DB" });
    const { filename, is_anomaly, line, station_id } = row;
    const addr = await getFdAddress(line, station_id);
    if (!addr)
      return res.status(503).json({ error: `FD L${line} ST${station_id} offline` });

    const vtype = is_anomaly ? "anomaly" : "normal";
    const fdPath = `/videos/${vtype}/${encodeURIComponent(filename)}`;
    const fdHost = new URL(addr);
    const reqHeaders = {};
    if (req.headers.range) reqHeaders["Range"] = req.headers.range;

    http.get({
      hostname: fdHost.hostname,
      port: fdHost.port,
      path: fdPath,
      headers: reqHeaders,
    }, (fdRes) => {
      res.status(fdRes.statusCode);
      ["content-type", "content-length", "content-range", "accept-ranges"]
        .forEach(h => { if (fdRes.headers[h]) res.setHeader(h, fdRes.headers[h]); });
      fdRes.pipe(res);
      req.on("close", () => fdRes.destroy());
    }).on("error", (e) => {
      log(`[video stream] proxy error: ${e.message}`);
      if (!res.headersSent)
        res.status(502).json({ error: "FD tidak bisa dijangkau" });
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});
app.get("/api/video/:videoId/download", videoLimit, async (req, res) => {
  try {
    const row = await db.getVideoByFilename(req.params.videoId);
    if (!row) return res.status(404).json({ error: "Video tidak ditemukan di DB" });
    const { filename, is_anomaly, line, station_id } = row;
    const addr = await getFdAddress(line, station_id);
    if (!addr)
      return res.status(503).json({ error: `FD L${line} ST${station_id} offline` });
    const vtype = is_anomaly ? "anomaly" : "normal";
    const fdHost = new URL(addr);
    const fdPath = `/videos/${vtype}/${encodeURIComponent(filename)}`;

    http.get({
      hostname: fdHost.hostname,
      port: fdHost.port,
      path: fdPath,
    }, (fdRes) => {
      if (fdRes.statusCode !== 200)
        return res.status(fdRes.statusCode).json({ error: "File tidak ditemukan di FD" });
      res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);
      res.setHeader("Content-Type", "video/mp4");
      if (fdRes.headers["content-length"])
        res.setHeader("Content-Length", fdRes.headers["content-length"]);
      fdRes.pipe(res);
      req.on("close", () => fdRes.destroy());
    }).on("error", (e) => {
      log(`[video download] proxy error: ${e.message}`);
      if (!res.headersSent)
        res.status(502).json({ error: "FD tidak bisa dijangkau" });
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});
app.delete("/api/video/:videoId", async (req, res) => {
  try {
    const row = await db.getVideoByFilename(req.params.videoId);
    if (!row) return res.status(404).json({ error: "Video tidak ditemukan di DB" });
    const { filename, is_anomaly, line, station_id } = row;
    // Hanya boleh hapus anomaly
    if (!is_anomaly)
      return res.status(403).json({ error: "Hanya video anomaly yang boleh dihapus" });

    const addr = await getFdAddress(line, station_id);
    if (!addr)
      return res.status(503).json({ error: `FD L${line} ST${station_id} offline` });

    // Kirim DELETE ke FD
    const fdHost = new URL(addr);
    const deleteReq = http.request({
      hostname: fdHost.hostname,
      port: fdHost.port,
      path: `/videos/anomaly/${encodeURIComponent(filename)}`,
      method: "DELETE",
    }, async (fdRes) => {
      fdRes.resume();
      try {
        if (fdRes.statusCode === 200 || fdRes.statusCode === 404) {
          await db.clearVideo(filename);
          log(`VIDEO DELETE anomaly: ${filename} | L${line} ST${station_id}`);
          res.json({ ok: true, deleted: filename });
        } else {
          res.status(fdRes.statusCode).json({ error: "FD gagal hapus file" });
        }
      } catch (e) {
        if (!res.headersSent) res.status(500).json({ error: e.message });
      }
    });
    deleteReq.on("error", (e) => {
      log(`[video delete] proxy error: ${e.message}`);
      if (!res.headersSent)
        res.status(502).json({ error: "FD tidak bisa dijangkau" });
    });
    deleteReq.end();

  } catch (err) {
    console.error("[video delete]", err.message);
    res.status(500).json({ error: err.message });
  }
});
app.get("/api/storage/usage", async (req, res) => {
  try {
    const fds = await db.getFdStatus();
    const online = fds.filter((fd) => isOnline(fd.line, fd.station_id) && !isSaving(fd.line, fd.station_id));
    const results = await Promise.allSettled(
      online.map(fd =>
        new Promise((resolve, reject) => {
          const req2 = http.get(
            `http://${fd.ip}:${fd.port}/health`,
            { timeout: 3000 },
            (fdRes) => {
              let body = "";
              fdRes.on("data", d => body += d);
              fdRes.on("end", () => {
                try { resolve({ line: fd.line, station_id: fd.station_id, name: fd.name, ...JSON.parse(body) }); } catch { reject(new Error("parse error")); }
              });
            }
          );
          req2.on("error", reject);
          req2.on("timeout", () => { req2.destroy(); reject(new Error("timeout")); });
        })
      )
    );

    const stations = results
      .filter(r => r.status === "fulfilled")
      .map(r => r.value);

    res.json({
      stations,                                                               // per FD
      total_normal_gb: stations.reduce((s, u) => s + (u.normal_gb || 0), 0),
      total_anomaly_gb: stations.reduce((s, u) => s + (u.anomaly_gb || 0), 0),
      total_gb: stations.reduce((s, u) => s + (u.normal_gb || 0) + (u.anomaly_gb || 0), 0),
      online_count: stations.length,
      offline_count: online.length - stations.length,
    });
  } catch (err) { res.status(500).json({ error: err.message }); }
});
io.on("connection", (socket) => {
  log(`Dashboard connected: ${socket.id}`);
  socket.join("all");
  socket.data.lines = [];

  // dashboard memilih line yang ingin dipantau; array kosong = kembali menerima semua
  socket.on("subscribe", ({ lines = [] } = {}) => {
    const want = [...new Set((Array.isArray(lines) ? lines : []).map(Number).filter(Number.isInteger))];
    for (const l of socket.data.lines) socket.leave(`line:${l}`);
    socket.data.lines = want;
    if (!want.length) { socket.join("all"); return socket.emit("summary_update", engine.snapshotAll()); }
    socket.leave("all");
    want.forEach((l) => socket.join(`line:${l}`));
    socket.emit("summary_update", engine.snapshotForLines(want));   // langsung beri data, tidak menunggu tick
  });

  socket.emit("summary_update", engine.snapshotAll());
  socket.emit("fd_status_all", fdStatusAll());
  socket.on("ping", (data) => {
    if (data?.client_type === "python_station") {
      log(`Heartbeat ping dari L${data.line} ST${data.station_id} (${socket.id})`);
      const room = `fd:${Number(data.line)}-${Number(data.station_id)}`;
      if (!socket.rooms.has(room)) {          // stasiun tidak perlu menerima seluruh siaran dashboard
        socket.leave("all");
        socket.join(room);
      }
    }
    socket.emit("pong", {
      received_at: data?.sent_at ?? null,
      server_time: Date.now(),
    });
  });
  socket.on("disconnect", () => log(`Dashboard disconnected: ${socket.id}`));
});
function diagnoseDbError(err) {
  const hints = {
    ECONNREFUSED: "PostgreSQL tidak berjalan / port salah / firewall. Cek DBSERVER dan DBPORT.",
    ENOTFOUND: `Host "${process.env.DBSERVER}" tidak bisa di-resolve. Cek DBSERVER.`,
    ETIMEDOUT: "Timeout ke server. Cek jaringan/VPN/firewall.",
    "28P01": "Password salah. Cek DBUSER dan DBPASSWORD.",
    "28000": "Autentikasi ditolak. Cek pg_hba.conf dan DBUSER.",
    "3D000": `Database "${process.env.DBNAME}" tidak ada. Cek DBNAME.`,
    "57P03": "Server sedang startup/recovery, coba lagi.",
  };
  const sep = "─".repeat(60);
  console.error(`\n${sep}\n[DB] ✗ KONEKSI GAGAL\n${sep}`);
  console.error(`[DB] code=${err.code || "-"} message=${err.message}`);
  console.error(`[DB] Kemungkinan: ${hints[err.code] || "tidak dikenal, lihat stack trace"}`);
  console.error(`[DB] Config: host=${process.env.DBSERVER || "localhost"} port=${process.env.DBPORT || 5432} ` +
    `db=${process.env.DBNAME || "CTMonitor"} user=${process.env.DBUSER || "postgres"} ` +
    `password=${process.env.DBPASSWORD ? "(diset)" : "(default)"} ssl=${process.env.DBENCRYPT === "true"}`);
  console.error(err.stack || "");
  console.error(`${sep}\n`);
}
function getLocalIPs() {
  const out = [];
  for (const [iface, addrs] of Object.entries(os.networkInterfaces())) {
    for (const a of addrs || []) {
      if (a.family === "IPv4" && !a.internal) {
        out.push({ iface, address: a.address });
      }
    }
  }
  return out;
}
server.listen(PORT, "0.0.0.0", () => {
  log(`Server running on port ${PORT}`);
  log(`Socket.io ready`);
  db.getPool().catch(diagnoseDbError);
  engine.start(io);
  bootstrapSaving();
  let dbWasOk = null;
  setInterval(async () => {
    const t0 = Date.now();
    try {
      const p = await db.getPool();
      await p.query("SELECT 1");
      const ms = Date.now() - t0;
      if (dbWasOk !== true) log(`[DB] ONLINE (ping ${ms}ms)`);
      else if (ms > 1000) log(`[DB] ping lambat ${ms}ms`);
      dbWasOk = true;
    } catch (e) {
      if (dbWasOk !== false) log(`[DB] OFFLINE: ${e.code || e.number || ""} ${e.message}`);
      dbWasOk = false;
    }
  }, 10_000);
  log(`── Frontend / Python station bisa konek lewat:`);
  log(`   Local   : http://localhost:${PORT}`);

  const ips = getLocalIPs();
  if (ips.length === 0) {
    log(`   (tidak ada IP jaringan terdeteksi, cek koneksi LAN/WiFi)`);
  } else {
    ips.forEach(({ iface, address }) => {
      log(`   Network : http://${address}:${PORT}   [${iface}]`);
    });
  }

  log(`── CORS origins diizinkan: ${allowedOrigins.length ? allowedOrigins.join(", ") : "(kosong, semua origin browser akan ditolak)"}`);
});
