import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

const STORAGE_KEY = "ctm-theme";
const ThemeModeContext = createContext({ mode: "light", toggle: () => {}, setMode: () => {} });

function readStored() {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v === "dark" || v === "light" ? v : null;
  } catch {
    return null;
  }
}

function systemMode() {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Pasang atribut/kelas ke <html>. Kelas "dark" juga membuat token bawaan Chakra v3 ikut berganti. */
function applyMode(mode, animate) {
  const root = document.documentElement;
  if (animate) {
    root.classList.add("ctm-theme-anim");
    window.setTimeout(() => root.classList.remove("ctm-theme-anim"), 350);
  }
  root.dataset.theme = mode;
  root.classList.toggle("dark", mode === "dark");
  root.classList.toggle("light", mode === "light");
  root.style.colorScheme = mode;
}

export function ThemeModeProvider({ children }) {
  const [mode, setModeState] = useState(() => readStored() || systemMode());

  useEffect(() => { applyMode(mode, false); }, [mode]);

  const setMode = useCallback((next) => {
    setModeState(next);
    applyMode(next, true);
    try { localStorage.setItem(STORAGE_KEY, next); } catch { /* private mode: abaikan */ }
  }, []);

  const toggle = useCallback(() => setMode(mode === "dark" ? "light" : "dark"), [mode, setMode]);

  // Ikuti pengaturan OS selama pengguna belum memilih manual
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!mq) return undefined;
    const onChange = (e) => {
      if (readStored()) return;
      const next = e.matches ? "dark" : "light";
      setModeState(next);
      applyMode(next, true);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const value = useMemo(() => ({ mode, toggle, setMode }), [mode, toggle, setMode]);
  return <ThemeModeContext.Provider value={value}>{children}</ThemeModeContext.Provider>;
}

export const useThemeMode = () => useContext(ThemeModeContext);