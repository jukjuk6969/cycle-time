import React, { useRef, useEffect, useState } from "react";
import { ANOMALY_STYLE, getAnomalyType, expectedList } from "../hooks/useSocket";
import { BASE_URL } from "../config";

const MONO = `"JetBrains Mono", monospace`;

function Tag({ color, children, solid = false }) {
  return (
    <span style={{ background: solid ? color : "var(--ct-surface)", color: solid ? "#FFF" : color, fontSize: 10, fontFamily: MONO, fontWeight: 600, textTransform: "uppercase", padding: "2px 8px", borderRadius: 4, border: `1px solid ${color}`, whiteSpace: "nowrap" }}>
      {children}
    </span>
  );
}

export default function VideoPlayer({ video, onClose }) {
  const videoRef = useRef(null);
  const [failed, setFailed] = useState(false);
  const [layout, setLayout] = useState(null);
  const [slot, setSlot] = useState("all");
  const [pos, setPos] = useState({ cur: 0, dur: 0, paused: false });

  useEffect(() => {
    setFailed(false);
    if (videoRef.current) videoRef.current.focus();
  }, [video?.id]);

  useEffect(() => {
    setLayout(null);
    setSlot("all");
    if (!video) return;
    const vtype = video.is_buffer ? video.video_type : (video.is_anomaly ? "anomaly" : "normal");
    const ac = new AbortController();
    fetch(`${BASE_URL}/api/fd/${video.line}/${video.station_id}/video/${vtype}/${encodeURIComponent(video.filename)}/layout`, { signal: ac.signal, credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((l) => { if (l && Array.isArray(l.tiles) && l.tiles.length > 1 && Array.isArray(l.canvas)) setLayout(l); })
      .catch(() => {});
    return () => ac.abort();
  }, [video?.id]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!video) return null;

  const vid = encodeURIComponent(video.id);
  const fdBase = video.is_buffer ? `${BASE_URL}/api/fd/${video.line}/${video.station_id}/video/${video.video_type}/${encodeURIComponent(video.filename)}` : null;
  const streamUrl = fdBase || `${BASE_URL}/api/video/${vid}/stream`;
  const downloadUrl = fdBase ? `${fdBase}?download=1` : `${BASE_URL}/api/video/${vid}/download`;

  const tsFormatted = video.recorded_at ? new Date(video.recorded_at).toLocaleString("id-ID", { timeZone: "Asia/Jakarta" }) : "—";
  const type = video.is_buffer ? (video.is_anomaly ? "LONG" : "OK") : getAnomalyType(video);
  const skipped = video.is_buffer ? [] : expectedList(video);
  const label = String(video.label || "").toUpperCase();
  const showLabel = label && !(label === "GENESIS" && video.is_genesis);

  const tile = layout && slot !== "all" ? layout.tiles.find((t) => String(t.slot) === String(slot)) : null;
  const [rx, ry, rw, rh] = tile ? (tile.content || tile.rect) : [0, 0, 1, 1];
  const ratio = layout ? (rw * layout.canvas[0]) / (rh * layout.canvas[1]) : null;
  const cropMode = !!tile;
  
  const syncPos = (v) => setPos({ cur: v.currentTime || 0, dur: v.duration || 0, paused: v.paused });
  const togglePlay = () => { const v = videoRef.current; if (v) v.paused ? v.play() : v.pause(); };
  const fmt = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(13,17,23,0.85)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000, fontFamily: '"Inter", sans-serif' }}>
      <div onClick={(e) => e.stopPropagation()} style={{ background: "var(--ct-surface)", borderRadius: 8, padding: 20, width: "min(700px, 95vw)", maxHeight: "90vh", overflow: "auto" }}>
        
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6, flexWrap: "wrap" }}>
              {(type !== "OK" || skipped.length > 0) && (
                <Tag color={type !== "OK" ? ANOMALY_STYLE[type].color : "var(--ct-red)"}>
                  {video.is_buffer ? "ANOMALI" : type === "SHORT" ? "SHORT" : "ANOMALI"}
                </Tag>
              )}
              {skipped.length > 0 && <Tag color="var(--ct-yellow)">SKIPPED</Tag>}
              {video.is_genesis && <Tag color="var(--ct-purple)">GENESIS</Tag>}
              {video.is_buffer && <Tag color={video.pending ? "var(--ct-orange-strong)" : "var(--ct-red)"} solid>{video.pending ? "BUFFER · WAIT" : "BUFFER · ORPHAN"}</Tag>}
              {!video.is_buffer && video.is_buffered && <Tag color="var(--ct-cyan)">LATE</Tag>}
              {showLabel && <Tag color="var(--ct-ink-500)">{label}</Tag>}
              <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ct-black)", fontFamily: MONO, wordBreak: "break-all" }}>{video.filename}</span>
            </div>
            <div style={{ fontSize: 11, color: "var(--ct-ink-500)", fontFamily: MONO }}>
              L{video.line} ST{video.station_id} &nbsp;·&nbsp;
              CT: <strong style={{ color: "var(--ct-black)" }}>{video.cycle_time_sec != null ? `${Number(video.cycle_time_sec).toFixed(2)}s` : "—"}</strong> &nbsp;·&nbsp;
              {video.size_mb != null ? `${Number(video.size_mb).toFixed(1)} MB` : "—"} &nbsp;·&nbsp;
              {tsFormatted}
            </div>
          </div>
          <button onClick={onClose} style={{ border: "1px solid var(--ct-ink-200)", background: "var(--ct-canvas)", borderRadius: 4, width: 28, height: 28, cursor: "pointer", fontSize: 16, color: "var(--ct-ink-500)", flexShrink: 0, marginLeft: 8 }}>×</button>
        </div>

        {layout && (
          <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
            {["all", ...layout.tiles.map((t) => t.slot)].map((sl) => {
              const on = String(slot) === String(sl);
              return (
                <button key={sl} onClick={() => setSlot(sl)} style={{ padding: "4px 12px", borderRadius: 4, fontSize: 11, fontWeight: 600, fontFamily: MONO, cursor: "pointer", border: "1px solid var(--ct-blue)", background: on ? "var(--ct-blue)" : "var(--ct-blue-50)", color: on ? "var(--ct-on-accent)" : "var(--ct-blue)" }}>
                  {sl === "all" ? "ALL" : `S${sl}`}
                </button>
              );
            })}
          </div>
        )}

        <div style={layout ? { position: "relative", overflow: "hidden", background: "#000", borderRadius: 6, width: `min(100%, ${Math.round(420 * ratio)}px)`, aspectRatio: String(ratio), margin: "0 auto" } : {}}>
          <video key={video.id} ref={videoRef} src={streamUrl} controls={!cropMode} autoPlay playsInline preload="metadata" onClick={cropMode ? togglePlay : undefined} onTimeUpdate={(e) => syncPos(e.currentTarget)} onLoadedMetadata={(e) => syncPos(e.currentTarget)} onPlay={(e) => syncPos(e.currentTarget)} onPause={(e) => syncPos(e.currentTarget)}
            style={layout ? { position: "absolute", background: "#000", objectFit: "fill", display: "block", maxWidth: "none", maxHeight: "none", width: `${100 / rw}%`, height: `${100 / rh}%`, left: `${(-rx / rw) * 100}%`, top: `${(-ry / rh) * 100}%` } : { width: "100%", borderRadius: 6, background: "#000", maxHeight: 420 }}
            onError={(e) => { console.error("Video error:", e.currentTarget.error, streamUrl); setFailed(true); }}
          />
          {cropMode && (
            <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", background: "linear-gradient(transparent, rgba(0,0,0,0.65))" }}>
              <button onClick={togglePlay} aria-label={pos.paused ? "Putar" : "Jeda"} style={{ border: "none", background: "transparent", color: "#FFF", cursor: "pointer", fontSize: 14, width: 24 }}>
                {pos.paused ? "▶" : "❚❚"}
              </button>
              <input type="range" min={0} max={pos.dur || 0} step="0.01" value={pos.cur} onChange={(e) => { if (videoRef.current) videoRef.current.currentTime = Number(e.target.value); }} style={{ flex: 1 }} />
              <span style={{ color: "#FFF", fontSize: 10, fontFamily: MONO, whiteSpace: "nowrap" }}>{fmt(pos.cur)} / {fmt(pos.dur)}</span>
            </div>
          )}
        </div>
        
        {failed && (
          <div style={{ marginTop: 10, fontSize: 12, color: "var(--ct-red-text)", fontFamily: MONO }}>
            Video tidak bisa diputar. FD L{video.line} ST{video.station_id} mungkin offline, file sudah dihapus, atau format tidak didukung browser.{" "}
            <a href={streamUrl} target="_blank" rel="noreferrer" style={{ color: "var(--ct-blue)", fontWeight: 600 }}>Buka langsung</a>.
          </div>
        )}
        {video.is_buffer && (
          <div style={{ marginTop: 10, fontSize: 12, color: "var(--ct-orange-text)", fontFamily: MONO }}>
            {video.pending ? "Video ini masih di FD dan data cycle-nya menunggu dikirim ke server." : "Video ini ada di FD tetapi tidak tercatat di server maupun antrean kirim."}
          </div>
        )}
        
        <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
          <a href={downloadUrl} download={video.filename} style={{ padding: "8px 16px", borderRadius: 4, fontSize: 11, fontWeight: 600, fontFamily: MONO, textTransform: "uppercase", background: "var(--ct-blue-50)", color: "var(--ct-blue)", border: "1px solid var(--ct-blue)", textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 6 }}>
            Download Video
          </a>
          <button onClick={onClose} style={{ padding: "8px 16px", borderRadius: 4, fontSize: 11, fontWeight: 600, fontFamily: MONO, textTransform: "uppercase", background: "var(--ct-surface)", color: "var(--ct-ink-500)", border: "1px solid var(--ct-ink-200)", cursor: "pointer" }}>
            Close
          </button>
        </div>

      </div>
    </div>
  );
}