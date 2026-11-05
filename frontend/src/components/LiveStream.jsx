import React, { useEffect, useRef, useState } from "react";
import { BASE_URL } from "../config";

const MONO = `"JetBrains Mono", monospace`;
const LAYOUT_POLL_MS = 3000;

export default function LiveStream({ line, stationId, name, onClose }) {
    const imgRef = useRef(null);
    const [status, setStatus] = useState("loading");
    const [nonce, setNonce] = useState(() => Date.now());
    const [layout, setLayout] = useState(null);
    const [focus, setFocus] = useState(null);

    const streamPath = focus != null ? `stream/${focus}` : "stream";
    const src = `${BASE_URL}/api/fd/${line}/${stationId}/${streamPath}?t=${nonce}`;

    useEffect(() => {
        const onKey = (e) => e.key === "Escape" && onClose();
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [onClose]);

    useEffect(() => {
        return () => { if (imgRef.current) imgRef.current.src = ""; };
    }, []);

    useEffect(() => {
        let alive = true;
        const ctrl = new AbortController();
        const load = async () => {
            try {
                const r = await fetch(`${BASE_URL}/api/fd/${line}/${stationId}/layout`, { signal: ctrl.signal, credentials: "include" });
                if (!r.ok) throw new Error(String(r.status));
                const j = await r.json();
                if (alive) setLayout(j);
            } catch (e) {
                if (alive && e.name !== "AbortError") setLayout(null);
            }
        };
        load();
        const t = setInterval(load, LAYOUT_POLL_MS);
        return () => { alive = false; ctrl.abort(); clearInterval(t); };
    }, [line, stationId]);

    const tiles = layout?.tiles ?? [];

    const select = (slot) => {
        if (slot === focus) return;
        if (imgRef.current) imgRef.current.src = "";
        setStatus("loading");
        setFocus(slot);
        setNonce(Date.now());
    };

    useEffect(() => {
        if (focus != null && layout && !tiles.some((t) => t.slot === focus)) select(null);
    }, [layout]);

    const reconnect = () => {
        setStatus("loading");
        setNonce(Date.now());
    };

    const badge = {
        loading: { text: "CONNECTING...", color: "#64748B" },
        live: { text: "● LIVE", color: "#F58025" },
        error: { text: "DISCONNECTED", color: "#DC2626" },
    }[status];

    const frameStyle = { position: "relative", background: "#000", borderRadius: 6, minHeight: 240 };
    const imgStyle = { width: "100%", maxHeight: "70vh", objectFit: "contain", display: status === "error" ? "none" : "block", borderRadius: 6 };

    const pill = (active) => ({
        padding: "4px 12px", borderRadius: 4, fontSize: 11, fontWeight: 600, cursor: "pointer",
        fontFamily: MONO, textTransform: "uppercase", letterSpacing: "0.05em",
        border: "1px solid #005A9C",
        background: active ? "#005A9C" : "#E0F2FE",
        color: active ? "#FFF" : "#005A9C",
    });

    return (
        <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(13,17,23,0.85)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
            <div onClick={(e) => e.stopPropagation()} style={{ background: "#FFF", borderRadius: 6, padding: 20, width: "min(820px, 95vw)", fontFamily: '"Inter", sans-serif' }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
                    <div>
                        <div style={{ fontSize: 14, fontWeight: 700, color: "#231F20", fontFamily: MONO }}>
                            LIVE FEED · L{line} ST{stationId}{name ? ` — ${name}` : ""}
                        </div>
                        <div style={{ fontSize: 11, fontWeight: 600, color: badge.color, marginTop: 4, fontFamily: MONO, letterSpacing: "0.1em" }}>
                            {badge.text}
                        </div>
                    </div>
                    <button onClick={onClose} style={{ border: "1px solid #E2E8F0", background: "#F4F6F8", borderRadius: 4, width: 28, height: 28, cursor: "pointer", fontSize: 16, color: "#64748B" }}>×</button>
                </div>

                {tiles.length > 1 && (
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
                        <button onClick={() => select(null)} style={pill(focus == null)}>ALL</button>
                        {tiles.map((t) => (
                            <button key={t.slot} onClick={() => select(t.slot)} style={pill(focus === t.slot)}>S{t.slot}</button>
                        ))}
                    </div>
                )}

                <div style={frameStyle}>
                    <img ref={imgRef} key={`${focus ?? "all"}-${nonce}`} src={src} alt={`Live L${line} ST${stationId}`} onLoad={() => setStatus("live")} onError={() => setStatus("error")} style={imgStyle} />
                    {status === "error" && (
                        <div style={{ height: 240, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10, color: "#FFF" }}>
                            <span style={{ fontSize: 12, fontFamily: MONO }}>Stream unavailable. FD offline or no heartbeat.</span>
                            <button onClick={reconnect} style={{ padding: "6px 14px", borderRadius: 4, border: "none", background: "#005A9C", color: "#FFF", cursor: "pointer", fontSize: 12, fontWeight: 600, fontFamily: MONO }}>RECONNECT</button>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}