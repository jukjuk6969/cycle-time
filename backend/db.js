const sql = require("mssql");          // sementara: hanya agar export `sql` lama tetap ada; hapus setelah server.js dicek
const pg = require("pg");
const { Pool, types } = pg;
const envResult = require("dotenv").config();

const dbLog = (m) => console.log(`[${new Date().toISOString()}] [db] ${m}`);

// Kolom timestamp (tanpa zona) berisi UTC → baca & tulis sebagai UTC, bukan zona lokal Node
pg.defaults.parseInputDatesAsUTC = true;
types.setTypeParser(1114, (s) => new Date(s.replace(" ", "T") + "Z")); // timestamp
types.setTypeParser(20, (s) => Number(s));        // bigint (COUNT, SUM) → number
types.setTypeParser(1700, (s) => parseFloat(s));  // numeric → number

const config = {
  host: process.env.DBSERVER || "localhost",
  database: process.env.DBNAME || "CTMonitor",
  user: process.env.DBUSER || "123321123321123321123321123321123321123321",
  password: process.env.DBPASSWORD || "123321123321123321123321123321123321123321123321123321",
  port: parseInt(process.env.DBPORT) || 909090909090990990909090990990909090990909090909090909,
  max: parseInt(process.env.DB_POOLMAX) || 30,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 5000,   // gagal cepat
  statement_timeout: 10000,        // setara requestTimeout
  ssl: process.env.DBENCRYPT === "true" ? { rejectUnauthorized: false } : false,
};

dbLog(`.env: ${envResult.error ? "TIDAK TERBACA (" + envResult.error.code + ") cwd=" + process.cwd() : "terbaca, " + Object.keys(envResult.parsed || {}).length + " key"}`);
dbLog(`config: server=${config.host} port=${config.port} db=${config.database} user=${config.user} ssl=${!!config.ssl}`);
["DBSERVER", "DBNAME", "DBUSER", "DBPASSWORD", "DBPORT"].forEach((k) => {
  if (!process.env[k]) dbLog(`  ${k} tidak diset → pakai default`);
});

let poolPromise = null;
let attempt = 0;

function getPool() {
  if (!poolPromise) {
    const n = ++attempt;
    const t0 = Date.now();
    dbLog(`connect #${n} → ${config.host}:${config.port}/${config.database}`);

    const p = new Pool(config);
    // error pada koneksi idle; Pool membuat koneksi baru sendiri saat berikutnya dibutuhkan
    p.on("error", (e) => dbLog(`pool error: code=${e.code || "-"} ${e.message}`));

    poolPromise = p.query("SELECT 1")
      .then(() => {
        dbLog(`connect #${n} OK (${Date.now() - t0}ms)`);
        return p;
      })
      .catch((e) => {
        dbLog(`connect #${n} GAGAL (${Date.now() - t0}ms) code=${e.code || "-"} ${e.message}`);
        poolPromise = null;
        p.end().catch(() => { });
        throw e;
      });
  }
  return poolPromise;
}

// ── Filter waktu & shift ─────────────────────────────────────────────────────
// end_timestamp tersimpan UTC → jam WIB = +7. Batas shift hanya di sini.
const HOUR_WIB = "EXTRACT(HOUR FROM end_timestamp + interval '7 hours')";
const SHIFT1_SQL = `(${HOUR_WIB} >= 19 OR ${HOUR_WIB} < 7)`;
const SHIFT2_SQL = `(${HOUR_WIB} >= 7 AND ${HOUR_WIB} < 19)`;

// ── Periode penomoran cycle ───────────────────────────────────────────────────
// Shift 1 = 19:00-07:00, Shift 2 = 07:00-19:00 (WIB). end_timestamp tersimpan UTC (WIB = UTC+7).
const RESET_HOUR_WIB = 19;
function prodDayRange(d) {
  const b = (RESET_HOUR_WIB - 7 + 24) % 24;            // jam batas dalam UTC (12:00)
  const start = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), b));
  if (start > d) start.setUTCDate(start.getUTCDate() - 1);
  const end = new Date(start);
  end.setUTCDate(end.getUTCDate() + 1);
  return { start, end };
}
// reset cycle_count tiap pergantian shift: 07:00 dan 19:00 WIB = 00:00 dan 12:00 UTC
function shiftRange(d) {
  const h = d.getUTCHours() >= 12 ? 12 : 0;
  const start = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), h));
  return { start, end: new Date(start.getTime() + 12 * 3600 * 1000) };
}
function toDate(v, name) {
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) throw new Error(`${name} tidak valid: ${v}`);
  return d;
}

// ps = array parameter posisional ($1, $2, ...); fungsi ini menambahkan ke ps
function applyRange(ps, { from = null, to = null, shift = null } = {}) {
  let where = "";
  if (from) { ps.push(toDate(from, "from")); where += ` AND end_timestamp >= $${ps.length}`; }
  if (to)   { ps.push(toDate(to, "to"));     where += ` AND end_timestamp < $${ps.length}`; }
  if (String(shift) === "1") where += ` AND ${SHIFT1_SQL}`;
  else if (String(shift) === "2") where += ` AND ${SHIFT2_SQL}`;
  return where;
}

const CYCLE_COLS =
  "cycle_count, cycle_time_sec, start_timestamp, end_timestamp, is_anomaly, anomaly_type, is_genesis, is_buffered, operator_id, video_filename, anomaly_label, skipped_expected";

async function getRecentInRange(line, station_id, opts = {}, limit = 100) {
  const p = await getPool();
  const n = Math.min(Math.max(parseInt(limit) || 100, 1), 1000);
  const ps = [line, station_id];
  const where = applyRange(ps, opts);
  const r = await p.query(`
    SELECT * FROM (
      SELECT ${CYCLE_COLS},
             CASE WHEN ${SHIFT2_SQL} THEN 2 ELSE 1 END AS shift
      FROM cycle_times
      WHERE line = $1 AND station_id = $2 AND is_complete = true ${where}
      ORDER BY end_timestamp DESC
      LIMIT ${n}
    ) t ORDER BY end_timestamp ASC
  `, ps);
  return r.rows;
}

async function ensureOperator(operator_id) {
  if (!operator_id) return;
  const p = await getPool();
  const shift = operator_id.match(/S([123])$/i)?.[1] || "1";
  await p.query(
    `INSERT INTO operators (id, name, shift) VALUES ($1, $1, $2)
     ON CONFLICT (id) DO NOTHING`,
    [operator_id, shift]
  );
}

async function getRecordedFilenames(line, station_id, sinceDate) {
  const p = await getPool();
  const r = await p.query(
    `SELECT video_filename FROM cycle_times
     WHERE line = $1 AND station_id = $2
       AND video_filename IS NOT NULL
       AND end_timestamp >= $3`,
    [Number(line), Number(station_id), sinceDate]
  );
  return new Set(r.rows.map((x) => x.video_filename));
}

// ── insertCycleTime ───────────────────────────────────────────────────────────
// Return: { cycle_count, anomaly_type }
// cycle_count = nomor urut cycle di station ini dalam shift (reset tiap pergantian shift)
function resolveAnomalyType(data) {
  const t = String(data.anomaly_type || "").toUpperCase();
  if (["OK", "LONG", "SHORT"].includes(t)) return t;
  // klien lama tanpa anomaly_type: hitung sendiri
  return data.target_cycle_time > 0 && data.cycle_time_sec > data.target_cycle_time
    ? "LONG" : "OK";
}

async function insertCycleTime(data) {
  const p = await getPool();
  if (!Number.isInteger(Number(data.line)) || !Number.isInteger(Number(data.station_id)))
    throw new Error("line dan station_id wajib berupa angka");
  const anomalyType = resolveAnomalyType(data);
  const isAnomaly = Boolean(anomalyType !== "OK" || data.skipped_expected);
  const vp = videoParams(data) || {};
  await ensureOperator(data.operator_id);
  const line = Number(data.line), sid = Number(data.station_id);
  const endTs = new Date(data.end_timestamp * 1000);
  const { start: dayStart, end: dayEnd } = shiftRange(endTs);

  const c = await p.connect();
  try {
    await c.query("BEGIN");
    // pengganti UPDLOCK/HOLDLOCK: serialkan penomoran per (line, station)
    await c.query("SELECT pg_advisory_xact_lock($1, $2)", [line, sid]);
    await c.query(
      `INSERT INTO cycle_times
        (station_id, line, operator_id, cycle_time_sec, start_timestamp, end_timestamp,
         is_complete, is_anomaly, anomaly_type, is_genesis, is_buffered, cycle_count,
         video_filename, video_size_mb, video_recorded_at, video_label,
         skipped_expected, anomaly_label, created_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,0,$12,$13,$14,$15,$16,$17,$6)`,
      [sid, line, data.operator_id || null, data.cycle_time_sec,
       new Date(data.start_timestamp * 1000), endTs,
       !!data.is_complete, isAnomaly, anomalyType,
       !!data.is_genesis, !!data.is_buffered,
       vp.filename || null, vp.size_mb ?? null, vp.recorded_at || null, vp.label || null,
       data.skipped_expected || null, data.anomaly_label || null]
    );
    await c.query(
      `UPDATE cycle_times c SET cycle_count = r.rn
       FROM (SELECT end_timestamp AS et,
                    ROW_NUMBER() OVER (ORDER BY end_timestamp) AS rn
             FROM cycle_times
             WHERE line = $1 AND station_id = $2 AND is_complete = true
               AND end_timestamp >= $3 AND end_timestamp < $4) r
       WHERE c.line = $1 AND c.station_id = $2 AND c.end_timestamp = r.et
         AND c.cycle_count <> r.rn`,
      [line, sid, dayStart, dayEnd]
    );
    const r = await c.query(
      `SELECT cycle_count FROM cycle_times
       WHERE line = $1 AND station_id = $2 AND end_timestamp = $3`,
      [line, sid, endTs]
    );
    await c.query("COMMIT");
    return { cycle_count: r.rows[0].cycle_count, anomaly_type: anomalyType };
  } catch (e) {
    await c.query("ROLLBACK").catch(() => { });
    throw e;
  } finally {
    c.release();
  }
}

// ── upsertHeartbeat ───────────────────────────────────────────────────────────
async function upsertHeartbeat(line, station_id, state, elapsed_sec) {
  const p = await getPool();
  await p.query(
    `INSERT INTO heartbeats (line, station_id, state, elapsed_sec, updated_at)
     VALUES ($1, $2, $3, $4, timezone('UTC', now()))
     ON CONFLICT (line, station_id) DO UPDATE
       SET state = EXCLUDED.state,
           elapsed_sec = EXCLUDED.elapsed_sec,
           updated_at = EXCLUDED.updated_at`,
    [line, station_id, state, elapsed_sec ?? null]
  );
}

async function getStationSummary() {
  const p = await getPool();
  const r = await p.query(`
    SELECT
      s.station_id,
      s.name AS station_name,
      s.line,
      s.takt_time,
      s.target_cycle_time,
      s.roi_timeout_sec,
      s.min_cycle_sec,
      s.short_anomaly_sec,
      s.is_active,
      today.cycles_today,
      today.avg_ct_today,
      lc.last_cycle_sec,
      total.cycle_count_total
    FROM stations s
    LEFT JOIN (
      SELECT line, station_id,
             COUNT(*) AS cycles_today,
             AVG(cycle_time_sec) AS avg_ct_today
      FROM cycle_times
      WHERE is_complete = true AND end_timestamp >= $1
      GROUP BY line, station_id
    ) today ON today.line = s.line AND today.station_id = s.station_id
    LEFT JOIN (
      SELECT line, station_id, COUNT(*) AS cycle_count_total
      FROM cycle_times
      WHERE is_complete = true AND end_timestamp >= $2
      GROUP BY line, station_id
    ) total ON total.line = s.line AND total.station_id = s.station_id
    LEFT JOIN LATERAL (
      SELECT c.cycle_time_sec AS last_cycle_sec
      FROM cycle_times c
      WHERE c.line = s.line AND c.station_id = s.station_id AND c.is_complete = true
      ORDER BY c.end_timestamp DESC
      LIMIT 1
    ) lc ON true
    WHERE s.is_active = true
    ORDER BY s.line, s.station_id
  `, [prodDayRange(new Date()).start, shiftRange(new Date()).start]);
  return r.rows;
}

async function upsertFdRegistry({ ip, port, hostname }) {
  const p = await getPool();
  const found = await p.query(
    `SELECT station_id, line, name, ip, port, hostname,
            takt_time, target_cycle_time, roi_timeout_sec, min_cycle_sec, short_anomaly_sec
     FROM stations WHERE ip = $1 AND is_active = true`, [ip]);

  if (found.rows.length === 0)
    throw new Error(`IP ${ip} tidak terdaftar di stations`);
  if (found.rows.length > 1)
    throw new Error(`IP ${ip} terdaftar di lebih dari satu station aktif`);

  const st = found.rows[0];
  const newPort = port || st.port || 5001;
  const newHost = hostname || st.hostname || null;

  await p.query(
    `UPDATE stations
     SET port = $3, hostname = $4,
         last_seen = timezone('UTC', now()), is_online = true
     WHERE line = $1 AND station_id = $2`,
    [st.line, st.station_id, newPort, newHost]);

  return { ...st, ip, port: newPort, hostname: newHost };
}

async function updateFdHeartbeat({ ip, port }) {
  const p = await getPool();
  const r = await p.query(
    `UPDATE stations
     SET port = $2, last_seen = timezone('UTC', now()), is_online = true
     WHERE ip = $1 AND is_active = true`, [ip, port]);
  return r.rowCount;
}

// satu query untuk banyak stasiun (dipanggil tiap 10 detik); ANY(array) tidak kena batas parameter
async function updateFdSeenBatch(ips) {
  const uniq = [...new Set(ips)];
  if (!uniq.length) return;
  const p = await getPool();
  await p.query(
    `UPDATE stations
     SET last_seen = timezone('UTC', now()), is_online = true
     WHERE is_active = true AND ip = ANY($1::text[])`, [uniq]);
}

async function getFdStatus() {
  const p = await getPool();
  const r = await p.query(`
    SELECT station_id, name, line, ip, port,
           hostname, last_seen, is_online, is_active
    FROM stations
    WHERE is_active = true
    ORDER BY line, station_id
  `);
  return r.rows.map((row) => ({
    ...row,
    online: row.last_seen
      ? Date.now() - new Date(row.last_seen).getTime() < 60_000
      : false,
  }));
}

// ── CT TERBARU: 100 terakhir PER SHIFT, tanpa filter tanggal ─────────────────
// shift null → 100 terbaru Shift 1 + 100 terbaru Shift 2 (maks 200)
async function getRecentCycles(line, station_id, shift = null, limit = 100) {
  const p = await getPool();
  const n = Math.min(Math.max(parseInt(limit) || 100, 1), 500);
  const s = String(shift);

  const part = (cond, label) => `
    SELECT * FROM (
      SELECT ${CYCLE_COLS}, ${label} AS shift
      FROM cycle_times
      WHERE line = $1 AND station_id = $2 AND is_complete = true AND ${cond}
      ORDER BY end_timestamp DESC
      LIMIT ${n}
    ) t${label}`;

  const parts = [];
  if (s !== "2") parts.push(part(SHIFT1_SQL, 1));
  if (s !== "1") parts.push(part(SHIFT2_SQL, 2));

  const r = await p.query(
    `${parts.join(" UNION ALL ")} ORDER BY end_timestamp ASC`,
    [line, station_id]);
  return r.rows;
}

// ── Semua cycle dalam rentang (untuk export CSV) ─────────────────────────────
async function getCyclesInRange(line, station_id, opts = {}, max = 200000) {
  const p = await getPool();
  const ps = [line, station_id];
  const where = applyRange(ps, opts);
  const r = await p.query(`
    SELECT ${CYCLE_COLS},
           CASE WHEN ${SHIFT2_SQL} THEN 2 ELSE 1 END AS shift
    FROM cycle_times
    WHERE line = $1 AND station_id = $2 AND is_complete = true ${where}
    ORDER BY end_timestamp ASC
    LIMIT ${parseInt(max)}
  `, ps);
  return r.rows;
}

// ── CT TOTAL: statistik dalam rentang + shift ────────────────────────────────
async function getStationStats(line, station_id, opts = {}) {
  const p = await getPool();
  const ps = [line, station_id];
  const where = applyRange(ps, opts);
  const r = await p.query(`
    SELECT
      COUNT(*)                                        AS total_cycles,
      AVG(cycle_time_sec)                             AS avg_ct,
      MIN(cycle_time_sec)                             AS min_ct,
      MAX(cycle_time_sec)                             AS max_ct,
      STDDEV_SAMP(cycle_time_sec)                     AS stddev_ct,
      SUM(CASE WHEN is_anomaly THEN 1 ELSE 0 END)     AS over_takt_count,
      SUM(CASE WHEN anomaly_type = 'SHORT' THEN 1 ELSE 0 END) AS short_count,
      SUM(CASE WHEN anomaly_type = 'LONG'
                 OR (anomaly_type IS NULL AND is_anomaly) THEN 1 ELSE 0 END) AS long_count,
      SUM(CASE WHEN skipped_expected IS NOT NULL THEN 1 ELSE 0 END) AS expected_count,
      (SELECT takt_time FROM stations
        WHERE line = $1 AND station_id = $2) AS takt_time
    FROM cycle_times
    WHERE line = $1 AND station_id = $2 AND is_complete = true ${where}
  `, ps);
  const row = r.rows[0] || {};
  const tot = row.total_cycles || 0;
  const over = row.over_takt_count || 0;
  return {
    ...row,
    over_takt_count: over,
    efficiency_pct: tot > 0 ? Math.round(((tot - over) / tot) * 100) : null,
  };
}

// ── Rata-rata per bucket waktu (WIB), lebar bucket mengikuti lebar rentang ───
function pickGrain(from, to) {
  if (!from || !to) return { name: "day", len: 10 };
  const hours = (new Date(to) - new Date(from)) / 3600000;
  if (hours <= 3) return { name: "minute", len: 16 };
  if (hours <= 48) return { name: "hour", len: 13 };
  if (hours <= 24 * 62) return { name: "day", len: 10 };
  return { name: "month", len: 7 };
}

async function getBucketStats(line, station_id, opts = {}) {
  const p = await getPool();
  const grain = pickGrain(opts.from, opts.to);
  const ps = [line, station_id];
  const where = applyRange(ps, opts);
  const b = `LEFT(TO_CHAR(end_timestamp + interval '7 hours', 'YYYY-MM-DD HH24:MI:SS'), ${grain.len})`;
  const r = await p.query(`
    SELECT ${b} AS bucket,
           COUNT(*)            AS cycles,
           AVG(cycle_time_sec) AS avg_ct,
           MIN(cycle_time_sec) AS min_ct,
           MAX(cycle_time_sec) AS max_ct
    FROM cycle_times
    WHERE line = $1 AND station_id = $2 AND is_complete = true ${where}
    GROUP BY 1
    ORDER BY 1
  `, ps);
  return { grain: grain.name, buckets: r.rows };
}

// Video = kolom di cycle_times. Waktu rekam berasal dari FD.
function videoParams(data) {
  const v = data.video;
  if (!v?.filename) return null;
  let rec = null;
  const ts = Number(v.recorded_ts ?? data.end_timestamp);
  if (Number.isFinite(ts) && ts > 0) rec = new Date(ts * 1000);
  else {
    const m = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/.exec(v.recorded_at || "");
    if (m) rec = new Date(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}+07:00`);
  }
  return {
    filename: v.filename,
    size_mb: v.size_mb ?? null,
    recorded_at: rec && !Number.isNaN(rec.getTime()) ? rec : null,
    label: String(v.label ?? data.label ?? "").toUpperCase() || null,
  };
}

// Dipakai saat kiriman ulang: cycle sudah ada, video belum menempel
async function attachVideo(data) {
  const vp = videoParams(data);
  if (!vp) return 0;
  const p = await getPool();
  const r = await p.query(
    `UPDATE cycle_times
     SET video_filename = $4, video_size_mb = $5,
         video_recorded_at = $6, video_label = $7
     WHERE line = $1 AND station_id = $2
       AND end_timestamp = $3 AND video_filename IS NULL`,
    [Number(data.line), Number(data.station_id),
     new Date(Number(data.end_timestamp) * 1000),
     vp.filename, vp.size_mb, vp.recorded_at, vp.label]);
  return r.rowCount;
}

const VIDEO_SELECT = `
  video_filename AS id, station_id, line, video_filename AS filename,
  video_size_mb AS size_mb, cycle_time_sec, is_anomaly, anomaly_type,
  video_label AS label, is_genesis, is_buffered, video_recorded_at AS recorded_at,
  anomaly_label, skipped_expected`;
const VIDEO_SORT = {
  time: "COALESCE(video_recorded_at, end_timestamp)",
  ct: "cycle_time_sec",
  size: "video_size_mb",
  line: "line",
  station: "station_id",
};

async function getVideos({
  line = null, station_id = null,
  anomaly_only = false, normal_only = false,
  atype = null, genesis = false, late = false, expected = false,
  from = null, to = null,
  sort = "time", dir = "desc", limit = 200, offset = 0,
} = {}) {
  const p = await getPool();
  const n = Math.min(Math.max(parseInt(limit) || 200, 1), 1000);
  const off = Math.max(parseInt(offset) || 0, 0);
  const col = VIDEO_SORT[sort] || VIDEO_SORT.time;          // whitelist, aman dari injection
  const d = String(dir).toLowerCase() === "asc" ? "ASC" : "DESC";

  // Filter waktu WAJIB. Tanpa from/to -> hari produksi berjalan.
  let { start, end } = prodDayRange(new Date());
  if (from || to) {
    start = from ? toDate(from, "from") : new Date(0);
    end = to ? toDate(to, "to") : new Date(Date.now() + 86400000);
  }
  if (end <= start) {
    const e = new Error("'to' harus lebih besar dari 'from'");
    e.status = 400;
    throw e;
  }

  const ps = [start, end];
  let where = "WHERE video_filename IS NOT NULL AND is_complete = true AND end_timestamp >= $1 AND end_timestamp < $2";

  if (line !== null && line !== "") {
    ps.push(Number(line));
    where += ` AND line = $${ps.length}`;
  }
  if (station_id !== null && station_id !== "") {
    ps.push(Number(station_id));
    where += ` AND station_id = $${ps.length}`;
  }
  if (anomaly_only) where += " AND is_anomaly = true";
  else if (normal_only) where += " AND is_anomaly = false";

  const atypes = String(atype || "").toUpperCase().split(",")
    .filter((t) => t === "LONG" || t === "SHORT");           // hanya nilai whitelist
  if (atypes.length)
    where += ` AND is_anomaly = true AND COALESCE(anomaly_type, 'LONG') IN (${atypes.map((t) => `'${t}'`).join(",")})`;
  if (genesis) where += " AND is_genesis = true";
  if (late) where += " AND is_buffered = true";
  if (expected) where += " AND skipped_expected IS NOT NULL";

  const [rows, cnt] = await Promise.all([
    p.query(`
      SELECT ${VIDEO_SELECT}
      FROM cycle_times
      ${where}
      ORDER BY ${col} ${d}, end_timestamp DESC, video_filename
      LIMIT ${n} OFFSET ${off}
    `, ps),
    p.query(`SELECT COUNT(*) AS total FROM cycle_times ${where}`, ps),
  ]);
  return { rows: rows.rows, total: cnt.rows[0].total };
}

async function getVideoByFilename(filename) {
  const p = await getPool();
  const r = await p.query(
    `SELECT video_filename AS filename, is_anomaly, line, station_id
     FROM cycle_times WHERE video_filename = $1 LIMIT 1`,
    [String(filename)]);
  return r.rows[0] || null;
}

async function updateVideoSize(filename, size_mb) {
  const p = await getPool();
  const r = await p.query(
    "UPDATE cycle_times SET video_size_mb = $2 WHERE video_filename = $1",
    [String(filename), size_mb]);
  return r.rowCount;
}

// Kosongkan kolom video; baris cycle TIDAK dihapus
async function clearVideo(filename) {
  const p = await getPool();
  const r = await p.query(
    `UPDATE cycle_times
     SET video_filename = NULL, video_size_mb = NULL,
         video_recorded_at = NULL, video_label = NULL
     WHERE video_filename = $1`,
    [String(filename)]);
  return r.rowCount;
}

async function markFdOffline(line, station_id) {
  const p = await getPool();
  await p.query(
    `UPDATE stations SET is_online = false, last_seen = NULL
     WHERE line = $1 AND station_id = $2`,
    [line, station_id]);
}

async function updateMinCycle(line, station_id, value) {
  const p = await getPool();
  const r = await p.query(
    "UPDATE stations SET min_cycle_sec = $3 WHERE line = $1 AND station_id = $2",
    [line, station_id, value]);
  return r.rowCount;
}

async function updateShortAnomaly(line, station_id, value) {
  const p = await getPool();
  const r = await p.query(
    "UPDATE stations SET short_anomaly_sec = $3 WHERE line = $1 AND station_id = $2",
    [line, station_id, value]);
  return r.rowCount;
}

const tsStr = (sec) => new Date(sec * 1000).toISOString().slice(0, -1);   // UTC tanpa "Z"
const dtStr = (d) => (d ? d.toISOString().slice(0, -1) : null);

async function insertCyclesBatch(rows) {
  if (!rows.length) return;
  const p = await getPool();

  for (const id of new Set(rows.map((r) => r.operator_id).filter(Boolean)))
    await ensureOperator(id);

  const json = JSON.stringify(rows.map((r) => {
    const v = r.video || {};
    return {
      line: r.line, station_id: r.station_id, operator_id: r.operator_id,
      ct: r.cycle_time_sec,
      st: tsStr(r.start_timestamp), et: tsStr(r.end_timestamp),
      cmp: !!r.is_complete, anom: !!r.is_anomaly, atype: r.anomaly_type,
      gen: !!r.is_genesis, buf: !!r.is_buffered,
      tl: r.target_limit_sec ?? null, sl: r.short_limit_sec ?? null,
      vfn: v.filename ?? null, vsz: v.size_mb ?? null,
      vra: dtStr(v.recorded_at), vlb: v.label ?? null,
      sx: r.skipped_expected || null, al: r.anomaly_label || null,
    };
  }));

  const c = await p.connect();
  try {
    await c.query("BEGIN");

    // kunci per (line, station), urut tetap agar tidak deadlock antar batch
    await c.query(
      `SELECT pg_advisory_xact_lock(line, station_id)
       FROM (SELECT DISTINCT line, station_id
             FROM json_to_recordset($1::json) AS j(line int, station_id int)
             ORDER BY 1, 2) x`,
      [json]
    );

    // 1) insert, duplikat dilewati; kembalikan hanya baris yang benar-benar baru
    const ins = await c.query(
      `INSERT INTO cycle_times
        (station_id, line, operator_id, cycle_time_sec, start_timestamp, end_timestamp,
         is_complete, is_anomaly, anomaly_type, is_genesis, is_buffered, cycle_count,
         video_filename, video_size_mb, video_recorded_at, video_label,
         target_limit_sec, short_limit_sec, skipped_expected, anomaly_label, created_at)
       SELECT j.station_id, j.line, j.operator_id, j.ct, j.st, j.et,
              j.cmp, j.anom, j.atype, j.gen, j.buf, 0,
              j.vfn, j.vsz, j.vra, j.vlb, j.tl, j.sl, j.sx, j.al, j.et
       FROM json_to_recordset($1::json) AS j(
         line int, station_id int, operator_id varchar(100), ct double precision,
         st timestamp, et timestamp, cmp boolean, anom boolean, atype varchar(10),
         gen boolean, buf boolean, tl double precision, sl double precision,
         vfn varchar(260), vsz double precision, vra timestamp, vlb varchar(50),
         sx varchar(500), al varchar(300))
       ON CONFLICT (line, station_id, end_timestamp) DO NOTHING
       RETURNING line, station_id, end_timestamp`,
      [json]
    );

    // 2) penomoran ulang HANYA dari titik sisip, per (line, station, shift)
    const groups = new Map();
    for (const r of ins.rows) {
      const { start, end } = shiftRange(r.end_timestamp);
      const k = `${r.line}|${r.station_id}|${start.getTime()}`;
      const g = groups.get(k);
      if (!g) groups.set(k, { line: r.line, sid: r.station_id, ds: start, de: end, min: r.end_timestamp });
      else if (r.end_timestamp < g.min) g.min = r.end_timestamp;
    }
    for (const g of groups.values()) {
      await c.query(
        `UPDATE cycle_times c SET cycle_count = r.rn
         FROM (SELECT end_timestamp AS et,
                      (SELECT COUNT(*) FROM cycle_times b
                        WHERE b.line = $1 AND b.station_id = $2 AND b.is_complete = true
                          AND b.end_timestamp >= $3 AND b.end_timestamp < $5)
                      + ROW_NUMBER() OVER (ORDER BY end_timestamp) AS rn
               FROM cycle_times
               WHERE line = $1 AND station_id = $2 AND is_complete = true
                 AND end_timestamp >= $5 AND end_timestamp < $4) r
         WHERE c.line = $1 AND c.station_id = $2 AND c.end_timestamp = r.et
           AND c.cycle_count <> r.rn`,
        [g.line, g.sid, g.ds, g.de, g.min]
      );
    }

    await c.query("COMMIT");
  } catch (e) {
    await c.query("ROLLBACK").catch(() => { });
    throw e;
  } finally {
    c.release();
  }
}

module.exports = {
  getPool,
  insertCycleTime,
  upsertHeartbeat,
  getVideos,
  getStationSummary,
  getRecentCycles,
  getRecentInRange,
  getCyclesInRange,
  getStationStats,
  getBucketStats,
  attachVideo, getVideoByFilename, updateVideoSize, clearVideo,
  updateMinCycle,
  updateShortAnomaly,
  upsertFdRegistry,
  updateFdHeartbeat, updateFdSeenBatch,
  getFdStatus,
  getRecordedFilenames,
  markFdOffline,
  shiftRange, prodDayRange, resolveAnomalyType, videoParams,
  insertCyclesBatch,
  sql,
};
