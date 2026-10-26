/**
 * LiveStream.jsx
 * Modal untuk melihat tampilan kamera live dari FD (MJPEG via proxy server).
 */
import React, { useEffect, useRef, useState } from "react";

import { BASE_URL } from "../config";

export default function LiveStream({ line, stationId, name, onClose }) {
    const imgRef = useRef(null);
    const [status, setStatus] = useState("loading");
    const [nonce, setNonce] = useState(() => Date.now());

    // ?t= mencegah browser memakai cache lama saat reconnect
    const src = `${BASE_URL}/api/fd/${line}/${stationId}/stream?t=${nonce}`;

    // Tutup dengan tombol Esc
    useEffect(() => {
        const onKey = (e) => e.key === "Escape" && onClose();
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [onClose]);

    // Saat modal ditutup, putuskan koneksi stream secara eksplisit
    useEffect(() => {
        const img = imgRef.current;
        return () => { if (img) img.src = ""; };
    }, []);
    const reconnect = () => {
        setStatus("loading");
        setNonce(Date.now());
    };

    const badge = {
        loading: { text: "Menghubungkan…", color: "#888" },
        live: { text: "● LIVE", color: "#d93025" },
        error: { text: "Terputus", color: "#a32d2d" },
    }[status];

    return (
        <div
            onClick={onClose}
            style={{
                position: "fixed", inset: 0, background: "rgba(0,0,0,0.78)",
                display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
            }}
        >
            <div
                onClick={(e) => e.stopPropagation()}
                style={{ background: "white", borderRadius: 14, padding: 20, width: "min(820px, 95vw)" }}
            >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                    <div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: "#1a1a1a" }}>
                            Live · L{line} ST{stationId}{name ? ` — ${name}` : ""}
                        </div>
                        <div style={{ fontSize: 12, fontWeight: 600, color: badge.color, marginTop: 2 }}>
                            {badge.text}
                        </div>
                    </div>
                    <button
                        onClick={onClose}
                        style={{
                            border: "none", background: "#f0f0f0", borderRadius: 8,
                            width: 32, height: 32, cursor: "pointer", fontSize: 18, color: "#555",
                        }}
                    >×</button>
                </div>

                <div style={{ position: "relative", background: "#000", borderRadius: 8, minHeight: 240 }}>
                    <img
                        ref={imgRef}
                        key={nonce}
                        src={src}
                        alt={`Live L${line} ST${stationId}`}
                        onLoad={() => setStatus("live")}
                        onError={() => setStatus("error")}
                        style={{
                            width: "100%", maxHeight: "70vh", objectFit: "contain",
                            display: status === "error" ? "none" : "block", borderRadius: 8,
                        }}
                    />
                    {status === "error" && (
                        <div style={{
                            height: 240, display: "flex", flexDirection: "column",
                            alignItems: "center", justifyContent: "center", gap: 10, color: "#fff",
                        }}>
                            <span style={{ fontSize: 13 }}>
                                Stream tidak tersedia. FD mungkin offline atau belum mengirim heartbeat.
                            </span>
                            <button
                                onClick={reconnect}
                                style={{
                                    padding: "6px 14px", borderRadius: 8, border: "none",
                                    background: "#378add", color: "#fff", cursor: "pointer", fontSize: 13,
                                }}
                            >
                                Coba lagi
                            </button>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}