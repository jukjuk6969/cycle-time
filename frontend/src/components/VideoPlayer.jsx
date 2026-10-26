/**
 * VideoPlayer.jsx
 * Modal player untuk memutar video cycle inline di dashboard.
 * video.id = nama file (dari BE), jadi harus di-encode di URL.
 */

import React, { useRef, useEffect, useState } from "react";
import { ANOMALY_STYLE, getAnomalyType, expectedList } from "../hooks/useSocket";

import { BASE_URL } from "../config";

function Tag({ color, children, solid = false }) {
  return (
    <span
      style={{
        background: solid ? color : "#fff",
        color: solid ? "#fff" : color,
        fontSize: 11,
        fontWeight: 500,
        padding: "2px 8px",
        borderRadius: 20,
        border: `1px solid ${color}`,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

export default function VideoPlayer({ video, onClose }) {
  const videoRef = useRef(null);
  const [failed, setFailed] = useState(false);

  // reset error tiap ganti video; fokus ke player
  useEffect(() => {
    setFailed(false);
    if (videoRef.current) videoRef.current.focus();
  }, [video?.id]);

  // tutup dengan Esc
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!video) return null;

  const vid = encodeURIComponent(video.id);

  const fdBase = video.is_buffer
    ? `${BASE_URL}/api/fd/${video.line}/${video.station_id}/video/${video.video_type}/${encodeURIComponent(video.filename)}`
    : null;

  const streamUrl = fdBase || `${BASE_URL}/api/video/${vid}/stream`;                 // tanpa parameter
  const downloadUrl = fdBase ? `${fdBase}?download=1` : `${BASE_URL}/api/video/${vid}/download`;

  const tsFormatted = video.recorded_at
    ? new Date(video.recorded_at).toLocaleString("id-ID", { timeZone: "Asia/Jakarta" })
    : "—";

  const type = video.is_buffer
    ? (video.is_anomaly ? "LONG" : "OK")   // hanya untuk menentukan ada/tidaknya tag anomali
    : getAnomalyType(video);
  const skipped = video.is_buffer ? [] : expectedList(video);
  const label = String(video.label || "").toUpperCase();
  // jangan tampilkan label yang sama dengan tag Genesis
  const showLabel = label && !(label === "GENESIS" && video.is_genesis);

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0,0,0,0.72)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 1000,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "white", borderRadius: 14, padding: 20,
          width: "min(700px, 95vw)", maxHeight: "90vh", overflow: "auto",
        }}
      >
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4, flexWrap: "wrap" }}>
                {(type !== "OK" || skipped.length > 0) && (
                  <Tag color={type !== "OK" ? ANOMALY_STYLE[type].color : "#dc2626"}>
                    {video.is_buffer ? "ANOMALI" : type === "SHORT" ? "SHORT ANOMALI" : "ANOMALI"}
                  </Tag>
                )}
                {skipped.length > 0 && <Tag color="#ca8a04">SKIPPED</Tag>}
              {video.is_genesis && <Tag color="#7c3aed">GENESIS</Tag>}

              {video.is_buffer && (
                <Tag color={video.pending ? "#ea580c" : "#dc2626"} solid>
                  {video.pending ? "BUFFER · MENUNGGU" : "BUFFER · YATIM"}
                </Tag>
              )}
              {!video.is_buffer && video.is_buffered && <Tag color="#0891b2">TERLAMBAT</Tag>}
              {showLabel && <Tag color="#6b7280">{label}</Tag>}
              <span style={{ fontSize: 13, fontWeight: 600, color: "#1a1a1a", wordBreak: "break-all" }}>
                {video.filename}
              </span>
            </div>
            <div style={{ fontSize: 12, color: "#888" }}>
              L{video.line} ST{video.station_id} &nbsp;·&nbsp;
              CT: <strong>{video.cycle_time_sec != null ? `${Number(video.cycle_time_sec).toFixed(2)}s` : "—"}</strong> &nbsp;·&nbsp;
              {video.size_mb != null ? `${Number(video.size_mb).toFixed(1)} MB` : "—"} &nbsp;·&nbsp;
              {tsFormatted}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Tutup"
            style={{
              border: "none", background: "#f0f0f0", borderRadius: 8,
              width: 32, height: 32, cursor: "pointer", fontSize: 18,
              display: "flex", alignItems: "center", justifyContent: "center",
              color: "#555", flexShrink: 0, marginLeft: 8,
            }}
          >×</button>
        </div>

        {/* Player: key memaksa elemen dibuat ulang saat ganti video.
            Tanpa crossOrigin: media tidak butuh CORS. */}
        <video
          key={video.id}
          ref={videoRef}
          src={streamUrl}
          controls
          autoPlay
          playsInline
          preload="metadata"
          style={{ width: "100%", borderRadius: 8, background: "#000", maxHeight: 420 }}
          onError={(e) => {
            console.error("Video error:", e.currentTarget.error, streamUrl);
            setFailed(true);
          }}
        />
        {failed && (
          <div style={{ marginTop: 8, fontSize: 12, color: "#a32d2d" }}>
            Video tidak bisa diputar. FD L{video.line} ST{video.station_id} mungkin offline,
            file sudah dihapus, atau format tidak didukung browser.{" "}
            <a href={streamUrl} target="_blank" rel="noreferrer" style={{ color: "#185fa5" }}>
              Buka langsung
            </a>{" "}
            untuk melihat pesan error dari server.
          </div>
        )}
        {video.is_buffer && (
          <div style={{ marginTop: 8, fontSize: 12, color: "#9a3412" }}>
            {video.pending
              ? "Video ini masih di FD dan data cycle-nya menunggu dikirim ke server. Akan hilang dari daftar buffer otomatis setelah terkirim."
              : "Video ini ada di FD tetapi tidak tercatat di server maupun antrean kirim. Perlu dicek oleh operator."}
          </div>
        )}
        {/* Actions */}
        <div style={{ display: "flex", gap: 10, marginTop: 14 }}>
          <a
            href={downloadUrl}
            download={video.filename}
            style={{
              padding: "8px 16px", borderRadius: 8, fontSize: 13,
              background: "#e6f1fb", color: "#185fa5",
              border: "1.5px solid #378add", textDecoration: "none",
              fontWeight: 500, display: "inline-flex", alignItems: "center", gap: 6,
            }}
          >
            Download video
          </a>
          <button
            onClick={onClose}
            style={{
              padding: "8px 16px", borderRadius: 8, fontSize: 13,
              background: "white", color: "#555",
              border: "1.5px solid #ddd", cursor: "pointer",
            }}
          >
            Tutup
          </button>
        </div>
      </div>
    </div>
  );
}