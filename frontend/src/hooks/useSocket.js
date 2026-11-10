import { useEffect, useRef, useSyncExternalStore } from "react";
import { io } from "socket.io-client";
import axios from "axios";
import { BASE_URL } from "../config";

export const ANOMALY_STYLE = {
  OK: { label: "OK", color: "#2e7d32" },
  LONG: { label: "LONG", color: "#c62828" },
  SHORT: { label: "SHORT", color: "#ef6c00" },
};
export const TYPE_UI = {
  OK: { palette: "green", label: "OK", bg: "white", hover: "gray.50", text: "gray.800", bar: null },
  LONG: { palette: "red", label: "Over target", bg: "red.50", hover: "red.100", text: "red.700", bar: "red" },
  SHORT: { palette: "orange", label: "Short", bg: "orange.50", hover: "orange.100", text: "orange.700", bar: "orange" },
};
export function getAnomalyType(row) {
  if (!row) return "OK";
  if (row.anomaly_type && ANOMALY_STYLE[row.anomaly_type]) return row.anomaly_type;
  return row.is_anomaly ? "LONG" : "OK";
}
/* ---- Expected terlewat ---------------------------------------------- *
 * Siklus bisa anomali walau anomaly_type = "OK" (hanya ROI expected yang
 * terlewat). Server menyimpan keterangannya di skipped_expected,
 * dipisah "; " (mis. "LCD; mask"), dan teks lengkapnya di anomaly_label.
 * ---------------------------------------------------------------------- */
export const EXPECTED_STYLE = { label: "Skipped", color: "#b7791f" };
export const EXPECTED_UI = {
  palette: "yellow", label: "Skipped",
  bg: "yellow.50", hover: "yellow.100", text: "yellow.800", bar: "yellow",
};

export function expectedList(row) {
  return String(row?.skipped_expected || "")
    .split(";").map((s) => s.trim()).filter(Boolean);
}
export const hasExpected = (row) => expectedList(row).length > 0;

// anomali = LONG/SHORT, atau ada expected yang terlewat
export function isAnomalyRow(row) {
  if (!row) return false;
  return getAnomalyType(row) !== "OK" || hasExpected(row);
}

// gaya baris: LONG/SHORT memakai gayanya sendiri; expected-only memakai kuning
export function getRowUi(row) {
  const t = getAnomalyType(row);
  if (t === "OK" && hasExpected(row)) return EXPECTED_UI;
  return TYPE_UI[t];
}

const DEBUG = false;            // true untuk log diagnosis offline/disconnect
const HB_FLUSH_MS = 500;
const CLOSE_DELAY_MS = 1000;    // jeda sebelum socket ditutup (menahan mount ulang React StrictMode / pindah halaman)
const EMPTY = [];

let state = { summary: EMPTY, heartbeats: {}, fdOnline: {}, connected: false, socket: null };
const listeners = new Set();
let socket = null;
let users = 0;
let closeTimer = null;
let flushTimer = null;
let hbBuffer = {};
let sentKey = "all";
const lineWants = new Map();    // token pemakai -> number[] | null (null = semua line)

const patch = (p) => { state = { ...state, ...p }; listeners.forEach((l) => l()); };
const subscribeStore = (l) => { listeners.add(l); return () => listeners.delete(l); };
const getSnapshot = () => state;

// hindari render ulang kalau isi summary sama persis dengan sebelumnya
function sameRows(a, b) {
  if (a === b) return true;
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i], y = b[i];
    const kx = Object.keys(x);
    if (kx.length !== Object.keys(y).length) return false;
    for (const k of kx) if (x[k] !== y[k]) return false;
  }
  return true;
}
// buang heartbeat/fdOnline milik line di luar daftar langganan
function pruneToLines(lines) {
  const keep = new Set(lines.map(String));
  const pick = (obj) => {
    const out = {};
    let changed = false;
    for (const k in obj) {
      if (keep.has(k.split("-")[0])) out[k] = obj[k];
      else changed = true;
    }
    return changed ? out : obj;     // referensi lama dipertahankan kalau tidak ada yang dibuang
  };
  const heartbeats = pick(state.heartbeats);
  const fdOnline = pick(state.fdOnline);
  if (heartbeats !== state.heartbeats || fdOnline !== state.fdOnline) patch({ heartbeats, fdOnline });
  for (const k in hbBuffer) if (!keep.has(k.split("-")[0])) delete hbBuffer[k];
}
// kirim daftar line yang dipantau ke server (gabungan semua pemakai); kosong = semua line
function syncLines() {
  if (!socket || !socket.connected) return;
  const wants = [...lineWants.values()];
  const all = wants.length === 0 || wants.some((w) => w === null || w.length === 0);
  const lines = all ? [] : [...new Set(wants.flat())].sort((a, b) => a - b);
  const key = all ? "all" : lines.join(",");
  if (key === sentKey) return;
  sentKey = key;
  socket.emit("subscribe", { lines });
  if (!all) pruneToLines(lines);
}

function connect() {
  if (socket) return;
  socket = io(BASE_URL, { transports: ["websocket"] });   // server memang hanya menerima websocket
  patch({ socket });

  socket.on("connect", () => {
    sentKey = "all";               // koneksi baru = server menaruh kita di room "all" lagi
    patch({ connected: true });
    syncLines();
  });

  socket.on("disconnect", (reason) => {
    if (DEBUG) console.warn(`[DBG] socket disconnect reason=${reason}`, new Date().toISOString());
    patch({ connected: false });
    // jika server yang memutus, socket.io tidak menyambung ulang otomatis
    if (reason === "io server disconnect" && socket) socket.connect();
  });

  socket.on("summary_update", (data) => {
    if (!sameRows(state.summary, data)) patch({ summary: data });
  });

  socket.on("heartbeat", ({ line, station_id, state: st, elapsed_sec, roi_idle_sec }) => {
    if (DEBUG && st === "OFFLINE")
      console.warn(`[DBG] heartbeat OFFLINE dari server L${line} ST${station_id}`, new Date().toISOString());
    hbBuffer[`${line}-${station_id}`] = { state: st, elapsed_sec, roi_idle_sec, received: Date.now() };
  });

  socket.on("heartbeat_batch", (batch) => {
    const now = Date.now();
    for (const h of batch) {
      hbBuffer[`${h.line}-${h.station_id}`] = {
        state: h.state, elapsed_sec: h.elapsed_sec, roi_idle_sec: h.roi_idle_sec, received: now,
      };
    }
  });

  socket.on("fd_status", ({ line, station_id, online }) => {
    if (DEBUG && !online)
      console.warn(`[DBG] fd_status online=false L${line} ST${station_id}`, new Date().toISOString());
    const k = `${line}-${station_id}`;
    if (state.fdOnline[k] === online) return;
    patch({ fdOnline: { ...state.fdOnline, [k]: online } });
  });

  socket.on("fd_status_all", (rows) => {
    const m = {};
    rows.forEach((r) => { m[`${r.line}-${r.station_id}`] = r.online; });
    patch({ fdOnline: m });
  });

  flushTimer = setInterval(() => {
    const buf = hbBuffer;
    if (Object.keys(buf).length === 0) return;
    hbBuffer = {};
    patch({ heartbeats: { ...state.heartbeats, ...buf } });
  }, HB_FLUSH_MS);
}
function disconnect() {
  if (flushTimer) { clearInterval(flushTimer); flushTimer = null; }
  if (socket) { socket.removeAllListeners(); socket.disconnect(); socket = null; }
  hbBuffer = {};
  sentKey = "all";
  patch({ socket: null, connected: false, summary: EMPTY, heartbeats: {}, fdOnline: {} });
}
// buang undefined/null/"" supaya tidak terkirim sebagai "undefined"/"null"; 0 dan false tetap dikirim
function buildParams(obj = {}) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null || v === "") continue;
    if (typeof v === "number" && Number.isNaN(v)) continue;
    p.append(k, String(v));
  }
  return p;
}
async function fetchHistory(line, station_id, limit = 100, filters = {}, { signal } = {}) {
  const params = buildParams({ ...filters, limit: filters.limit ?? limit });
  const res = await axios.get(`${BASE_URL}/api/station/${line}/${station_id}/history?${params}`, { signal });
  return res.data;
}
async function fetchHourly(line, station_id, filters = {}, { signal } = {}) {
  const params = buildParams(filters);
  const res = await axios.get(`${BASE_URL}/api/station/${line}/${station_id}/hourly?${params}`, { signal });
  return res.data;            // { grain, buckets }
}
async function fetchStats(line, station_id, filters = {}, { signal } = {}) {
  const params = buildParams(filters);
  const res = await axios.get(`${BASE_URL}/api/station/${line}/${station_id}/stats?${params}`, { signal });
  return res.data;
}
function exportCSV(line, station_id, filters = {}) {
  const params = buildParams(filters);
  const a = document.createElement("a");
  a.href = `${BASE_URL}/api/station/${line}/${station_id}/export?${params}`;
  a.click();
}
// fungsi fetch kini juga diekspor langsung, tidak perlu lewat hook
export { fetchHistory, fetchHourly, fetchStats, exportCSV };
export const isAbort = (e) => axios.isCancel(e) || e?.name === "CanceledError";
// selector bermodul-level supaya referensinya stabil
export const selectSummary = (s) => s.summary;
export const selectHeartbeats = (s) => s.heartbeats;
export const selectFdOnline = (s) => s.fdOnline;
export const selectConnected = (s) => s.connected;
export const selectSocket = (s) => s.socket;

// mengatur koneksi (ref-count) dan daftar line; tidak membaca state sama sekali
function useSocketLifecycle(lines) {
  const token = useRef({}).current;
  const linesKey = lines ? [...lines].map(Number).sort((a, b) => a - b).join(",") : null;

  useEffect(() => {
    users++;
    if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
    connect();
    return () => {
      lineWants.delete(token);
      syncLines();
      users--;
      if (users === 0)
        closeTimer = setTimeout(() => { closeTimer = null; if (users === 0) disconnect(); }, CLOSE_DELAY_MS);
    };
  }, [token]);

  useEffect(() => {
    lineWants.set(token, linesKey === null ? null : linesKey === "" ? [] : linesKey.split(",").map(Number));
    syncLines();
  }, [token, linesKey]);
}

/**
 * Hook baru: hanya re-render kalau irisan yang dipilih berubah.
 *   const summary = useSocketState(selectSummary, { lines: [1, 2] });
 *   const heartbeats = useSocketState(selectHeartbeats, { lines });
 * Selector harus mengembalikan referensi yang sudah ada di state (jangan membuat objek baru).
 */
export function useSocketState(selector, { lines } = {}) {
  useSocketLifecycle(lines);
  return useSyncExternalStore(
    subscribeStore,
    () => selector(state),
    () => selector(state)
  );
}

/**
 * Kompatibilitas lama: mengembalikan semuanya, jadi tetap re-render
 * tiap flush heartbeat. Pindahkan pemakai ke useSocketState secara bertahap.
 */
export function useSocket({ lines } = {}) {
  useSocketLifecycle(lines);
  const s = useSyncExternalStore(subscribeStore, getSnapshot, getSnapshot);
  return {
    summary: s.summary,
    socket: s.socket,
    heartbeats: s.heartbeats,
    recentCycles: EMPTY,
    connected: s.connected,
    fdOnline: s.fdOnline,
    fetchHistory,
    fetchHourly,
    fetchStats,
    exportCSV,
  };
}
