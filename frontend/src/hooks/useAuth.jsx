/**
 * useAuth.jsx
 * Sesi login dashboard (cookie HttpOnly dari server). Bungkus <App /> dengan <AuthProvider>.
 *
 * - axios.defaults.withCredentials = true  -> semua axios di project otomatis kirim cookie sesi.
 * - Respons 401 AUTH_REQUIRED dari API mana pun (sesi habis) -> kembali ke halaman login.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import axios from "axios";

import { BASE_URL } from "../config";

axios.defaults.withCredentials = true;

const NOT_READY = { user: null, loading: false, isAdmin: false, login: async () => {}, logout: async () => {} };
const AuthCtx = createContext(NOT_READY);
export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(undefined);        // undefined = sedang cek sesi, null = belum login

  // cek sesi yang masih berlaku (mis. setelah refresh halaman)
  useEffect(() => {
    let alive = true;
    axios.get(`${BASE_URL}/api/auth/me`)
      .then((r) => alive && setUser(r.data.user))
      .catch(() => alive && setUser(null));
    return () => { alive = false; };
  }, []);

  // sesi habis / cookie dihapus di tengah pemakaian
  useEffect(() => {
    const id = axios.interceptors.response.use(
      (r) => r,
      (err) => {
        const url = String(err.config?.url || "");
        if (err.response?.status === 401 && err.response.data?.code === "AUTH_REQUIRED" && !url.includes("/api/auth/"))
          setUser(null);
        return Promise.reject(err);
      }
    );
    return () => axios.interceptors.response.eject(id);
  }, []);

  const login = useCallback(async (username, password) => {
    const r = await axios.post(`${BASE_URL}/api/auth/login`, { username, password });
    setUser(r.data.user);
  }, []);

  const logout = useCallback(async () => {
    try { await axios.post(`${BASE_URL}/api/auth/logout`); } finally { setUser(null); }
  }, []);

  const value = useMemo(
    () => ({ user: user ?? null, loading: user === undefined, isAdmin: user?.role === "admin", login, logout }),
    [user, login, logout]
  );
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}
