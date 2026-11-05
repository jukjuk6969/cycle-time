/**
 * auth.js — login dashboard CT Monitor (user hardcode, sesi cookie bertanda tangan).
 *
 * Tanpa dependency baru (hanya crypto bawaan Node + express).
 *
 * ENV (opsional):
 *   AUTH_SECRET          kunci penanda tangan sesi (min 16 karakter). Kalau kosong: acak tiap start
 *                        -> semua user harus login ulang setiap server restart.
 *   AUTH_MODE            enforce (default) | report (hanya catat, tidak memblok) | off
 *   AUTH_SESSION_HOURS   masa berlaku sesi, default 12 (satu shift)
 *   AUTH_COOKIE_SECURE   "true" bila dashboard diakses lewat HTTPS
 *
 * Buat hash password baru:   node auth.js hash "password-baru"
 */
"use strict";
const crypto = require("crypto");
const express = require("express");

// ───────────────────────── konfigurasi ─────────────────────────
const MODE = (process.env.AUTH_MODE || "enforce").toLowerCase();
const COOKIE = "ctm_session";
const SESSION_MS = (Number(process.env.AUTH_SESSION_HOURS) || 12) * 3600_000;
const COOKIE_SECURE = process.env.AUTH_COOKIE_SECURE === "true";

let SECRET = process.env.AUTH_SECRET || "";
if (SECRET.length < 16) {
  SECRET = crypto.randomBytes(32).toString("hex");
  if (require.main !== module)
    console.warn("[AUTH] AUTH_SECRET belum diset (min 16 karakter): memakai kunci acak, " +
      "semua sesi hangus setiap server restart.");
}

// ───────────────────────── user (hardcode) ─────────────────────────
// role: "admin"  = boleh semua (ubah/hapus station, hapus video, saving mode, dst.)
//       "viewer" = hanya melihat (data, video, live stream)
// Password TIDAK disimpan polos; isi `hash` dengan:  node auth.js hash "password"
const USERS = [
  { username: "admin",  name: "Administrator", role: "admin",  hash: "scrypt$16384$8$1$ba99e1f9596c8d7ade7faa6e3f80390f$0040a76fb8e02ed6e2110791cf9887b773fef086df669510555d96d1bde270c0" },
  { username: "viewer", name: "Viewer",        role: "viewer", hash: "scrypt$16384$8$1$a98a8f05d0acc74b4655a831eb5a6d61$fb6035acd4e98d8dfb4e4c45e99fc07a9c0739272bb2c093ed859088bf368be6" },
];

// ───────────────────────── route yang TIDAK butuh login ─────────────────────────
// Dipakai mini-PC (FD) / Python yang belum punya kredensial. Path relatif terhadap /api.
const PUBLIC = [
  ["GET",  "/health"],
  ["POST", "/fd/register"],
  ["POST", "/fd/heartbeat"],
  ["GET",  "/fd/identity"],
  ["POST", "/cycle"],
  ["POST", "/heartbeat"],
  ["POST", "/video/update-size"],
  ["GET",  "/summary"],
  ["GET",  "/stations"],
  ["GET",  "/videos"],
  ["GET",  /^\/operator\/[^/]+$/],
  ["POST", "/operator"],
];
// Viewer boleh method tulis hanya untuk ini (aksi navigasi live, bukan mengubah data)
const VIEWER_WRITE = [["POST", /^\/fd\/[^/]+\/[^/]+\/click$/]];

// ───────────────────────── password (scrypt) ─────────────────────────
const SCRYPT = { N: 16384, r: 8, p: 1 };

function hashPassword(pw) {
  const salt = crypto.randomBytes(16);
  const h = crypto.scryptSync(String(pw), salt, 32, SCRYPT);
  return `scrypt$${SCRYPT.N}$${SCRYPT.r}$${SCRYPT.p}$${salt.toString("hex")}$${h.toString("hex")}`;
}

function verifyPassword(pw, stored) {
  try {
    const [alg, N, r, p, saltHex, hashHex] = String(stored).split("$");
    if (alg !== "scrypt") return false;
    const want = Buffer.from(hashHex, "hex");
    const got = crypto.scryptSync(String(pw), Buffer.from(saltHex, "hex"), want.length,
      { N: +N, r: +r, p: +p });
    return crypto.timingSafeEqual(got, want);
  } catch { return false; }
}

const DUMMY_HASH = hashPassword(crypto.randomBytes(8).toString("hex"));   // samakan waktu bila user tak ada

// ───────────────────────── token sesi (HMAC) ─────────────────────────
const b64u = (s) => Buffer.from(s).toString("base64url");
const mac = (body) => crypto.createHmac("sha256", SECRET).update(body).digest("base64url");

function signToken(username) {
  const body = b64u(JSON.stringify({ u: username, exp: Date.now() + SESSION_MS }));
  return `${body}.${mac(body)}`;
}

function verifyToken(tok) {
  if (typeof tok !== "string") return null;
  const i = tok.indexOf(".");
  if (i < 1) return null;
  const body = tok.slice(0, i), sig = Buffer.from(tok.slice(i + 1));
  const good = Buffer.from(mac(body));
  if (sig.length !== good.length || !crypto.timingSafeEqual(sig, good)) return null;
  let p;
  try { p = JSON.parse(Buffer.from(body, "base64url").toString()); } catch { return null; }
  if (!p || !(p.exp > Date.now())) return null;
  const u = USERS.find((x) => x.username === p.u);          // user dihapus/ganti role -> langsung berlaku
  return u ? { username: u.username, name: u.name, role: u.role } : null;
}

function parseCookies(header) {
  const out = {};
  for (const part of String(header || "").split(";")) {
    const k = part.indexOf("=");
    if (k > 0) out[part.slice(0, k).trim()] = decodeURIComponent(part.slice(k + 1).trim());
  }
  return out;
}

const userFromCookieHeader = (header) => verifyToken(parseCookies(header)[COOKIE]);

function setSessionCookie(res, value, maxAgeMs) {
  const attrs = [`${COOKIE}=${value}`, "Path=/", "HttpOnly", "SameSite=Lax",
    `Max-Age=${Math.floor(maxAgeMs / 1000)}`];
  if (COOKIE_SECURE) attrs.push("Secure");
  res.append("Set-Cookie", attrs.join("; "));
}

// ───────────────────────── batas percobaan login ─────────────────────────
const attempts = new Map();            // key -> { n, until }
function lockLeft(key) {
  const a = attempts.get(key);
  return a && a.until > Date.now() ? Math.ceil((a.until - Date.now()) / 1000) : 0;
}
function fail(key, max, lockMs) {
  const a = attempts.get(key) || { n: 0, until: 0 };
  if (a.until && a.until <= Date.now()) { a.n = 0; a.until = 0; }
  if (++a.n >= max) { a.until = Date.now() + lockMs; a.n = 0; }
  attempts.set(key, a);
}
setInterval(() => {
  for (const [k, a] of attempts) if (a.until <= Date.now() && a.n === 0) attempts.delete(k);
}, 60_000).unref();

const clientIp = (req) => String(req.ip || "").replace(/^::ffff:/, "");

// ───────────────────────── endpoint /api/auth/* ─────────────────────────
const router = express.Router();

router.post("/login", (req, res) => {
  const { username, password } = req.body || {};
  if (typeof username !== "string" || typeof password !== "string" || !username || !password ||
      username.length > 64 || password.length > 256)
    return res.status(400).json({ error: "username dan password wajib diisi" });

  const ip = clientIp(req), kUser = `${ip}|${username.toLowerCase()}`, kIp = ip;
  const wait = Math.max(lockLeft(kUser), lockLeft(kIp));
  if (wait > 0) {
    res.set("Retry-After", String(wait));
    return res.status(429).json({ error: `Terlalu banyak percobaan. Coba lagi dalam ${wait} detik.` });
  }

  const u = USERS.find((x) => x.username === username);
  const ok = verifyPassword(password, u ? u.hash : DUMMY_HASH) && !!u;
  if (!ok) {
    fail(kUser, 5, 60_000);            // 5 salah per user+IP -> kunci 60 dtk
    fail(kIp, 20, 5 * 60_000);         // 20 salah per IP     -> kunci 5 mnt
    console.warn(`[AUTH] login GAGAL user="${username.slice(0, 32)}" ip=${ip}`);
    return res.status(401).json({ error: "Username atau password salah" });
  }
  attempts.delete(kUser);
  setSessionCookie(res, signToken(u.username), SESSION_MS);
  console.log(`[AUTH] login OK user=${u.username} role=${u.role} ip=${ip}`);
  res.json({ user: { username: u.username, name: u.name, role: u.role } });
});

router.post("/logout", (req, res) => {
  setSessionCookie(res, "", 0);
  res.json({ ok: true });
});

router.get("/me", (req, res) => {
  const user = userFromCookieHeader(req.headers.cookie);
  res.json({ user: user || null });      // 200 meski belum login: cek sesi di FE tidak memunculkan error merah di console
});

// ───────────────────────── penjaga untuk semua /api lainnya ─────────────────────────
const match = (rules, method, path) =>
  rules.some(([m, p]) => m === method && (p instanceof RegExp ? p.test(path) : p === path));
const reported = new Set();

function guard(req, res, next) {
  if (MODE === "off") return next();
  const method = req.method, path = req.path.replace(/\/+$/, "") || "/";
  if (method === "OPTIONS") return next();

  const user = userFromCookieHeader(req.headers.cookie);
  if (user) req.user = user;
  if (match(PUBLIC, method, path)) return next();

  let verdict = null;
  if (!user) verdict = { status: 401, error: "Login diperlukan", code: "AUTH_REQUIRED" };
  else if (!["GET", "HEAD"].includes(method) && user.role !== "admin" && !match(VIEWER_WRITE, method, path))
    verdict = { status: 403, error: "Akun ini hanya boleh melihat (viewer)", code: "FORBIDDEN" };
  if (!verdict) return next();

  if (MODE === "report") {                                   // uji coba: catat saja
    const k = `${method} ${path} ${verdict.status}`;
    if (!reported.has(k)) { reported.add(k); console.warn(`[AUTH:report] akan ditolak ${k} dari ${clientIp(req)}`); }
    return next();
  }
  res.status(verdict.status).json({ error: verdict.error, code: verdict.code });
}

module.exports = { router, guard, userFromCookieHeader, hashPassword, verifyPassword, MODE };

if (require.main === module) {
  const [cmd, pw] = process.argv.slice(2);
  if (cmd === "hash" && pw) console.log(hashPassword(pw));
  else console.log('Pemakaian: node auth.js hash "password"');
}