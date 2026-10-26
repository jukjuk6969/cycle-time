import React, { useEffect, useState } from "react";
import { Box, Text } from "@chakra-ui/react";

import cameraImg from "../assets/connectivity/camera.png";
import fieldDeviceImg from "../assets/connectivity/field-device.png";
import backendImg from "../assets/connectivity/backend.png";
import databaseImg from "../assets/connectivity/database.png";

import { BASE_URL } from "../config";

const COLOR = {
    ok: "#38A169",
    slow: "#DD6B20",
    down: "#E53E3E",
    unknown: "#A0AEC0",
};

const STATUS_TEXT = {
    ok: "Normal",
    slow: "Lambat",
    down: "Putus",
    unknown: "Tidak diketahui",
};

const SEVERITY = { ok: 0, unknown: 1, slow: 2, down: 3 };

const SLOW_MS = 3000;
const DOWN_MS = 10000;

// ==============================
// SKALA GOLDEN RATIO (φ = 1.618)
// Ukuran memakai deret Fibonacci / pangkat φ
//   canvas  610 × 89   (89 = 144 / φ)
//   ikon    55
//   label   13  (55 / φ³)
//   caption  8  (13 / φ)
//   jarak    8  (Fibonacci)
//   lengkung 13 (Fibonacci)
// ==============================

const PHI = 1.618;
const VIEW_W = 610;
const VIEW_H = 89;
const ICON = 55;
const HALO = 31;
const GAP = 8;
const SAG = 13;
const FONT_LABEL = 13;
const FONT_CAPTION = 8;
const CY = 33.5;
const LABEL_Y = CY + ICON / 2 + 16;

// Bagian bawah (teks peringatan) memakai ritme φ
const FONT_NOTE = 13;
const NOTE_LINE = PHI;

// Node berjarak rata dengan margin kiri = kanan
const STEP = 160;
const X0 = 65;

const NODES = [
    { key: "cam", label: "Kamera", image: cameraImg, links: ["camFd"] },
    { key: "fd", label: "Field Device", image: fieldDeviceImg, links: ["camFd", "fdBe"] },
    { key: "be", label: "Backend", image: backendImg, links: ["fdBe", "beDb"] },
    { key: "db", label: "Database", image: databaseImg, links: ["beDb"] },
].map((n, i) => ({ ...n, x: X0 + i * STEP, y: CY }));

// Jembatan diturunkan dari posisi node + jarak GAP
const BRIDGE_KEYS = ["camFd", "fdBe", "beDb"];
const BRIDGES = BRIDGE_KEYS.map((statusKey, i) => ({
    statusKey,
    x1: NODES[i].x + ICON / 2 + GAP,
    x2: NODES[i + 1].x - ICON / 2 - GAP,
    y: CY,
}));

// ==============================
// DATABASE HEALTH
// ==============================

export function useDbHealth(enabled = true, intervalMs = 5000) {
    const [dbOk, setDbOk] = useState(null);

    useEffect(() => {
        if (!enabled) return undefined;
        let alive = true;

        const ping = async () => {
            try {
                const r = await fetch(`${BASE_URL}/api/health`);
                const j = await r.json();
                if (alive) setDbOk(r.ok && j.db === true);
            } catch {
                if (alive) setDbOk(null);
            }
        };

        ping();
        const id = setInterval(ping, intervalMs);
        return () => {
            alive = false;
            clearInterval(id);
        };
    }, [enabled, intervalMs]);

    return dbOk;
}

// ==============================
// HITUNG STATUS
// ==============================

export function computeLinks({ heartbeat, connected, dbOk, now = Date.now() }) {
    const out = { camFd: "unknown", fdBe: "unknown", beDb: "unknown", ageSec: null };

    if (!connected) return out;

    // Backend → Database
    out.beDb = dbOk === true ? "ok" : dbOk === false ? "down" : "unknown";

    // Field Device → Backend
    if (!heartbeat?.received) {
        out.fdBe = "down";
        return out;
    }

    const age = now - heartbeat.received;
    out.ageSec = Math.round(age / 1000);
    out.fdBe = age > DOWN_MS ? "down" : age > SLOW_MS ? "slow" : "ok";

    // Jika FD tidak mengirim heartbeat, kamera tidak dapat dipastikan.
    if (out.fdBe === "down") return out;

    // Camera → Field Device
    out.camFd = heartbeat.state === "CAMERA_OFFLINE" ? "down" : "ok";

    return out;
}

const worst = (links, keys) =>
    keys.reduce((a, k) => (SEVERITY[links[k]] > SEVERITY[a] ? links[k] : a), "ok");

// ==============================
// BRIDGE — kurva lembut, bukan garis lurus
// ==============================

function Bridge({ x1, x2, y, status, caption }) {
    const color = COLOR[status];
    const mid = (x1 + x2) / 2;
    const d = `M ${x1} ${y} Q ${mid} ${y - SAG * 2} ${x2} ${y}`;
    const apexY = y - SAG; // titik puncak kurva (t = 0.5)
    const flowing = status === "ok" || status === "slow";

    return (
        <g>
            {/* jalur dasar */}
            {status !== "down" && (
                <path d={d} fill="none" stroke={color} strokeWidth="1" opacity="0.22" strokeLinecap="round" />
            )}

            {/* jalur utama */}
            <path
                d={d}
                fill="none"
                stroke={color}
                strokeWidth={status === "down" ? 1.8 : 2}
                strokeLinecap="round"
                pathLength="100"
                strokeDasharray={
                    status === "down" ? "38 24 38" : status === "unknown" ? "1 7" : "3 9"
                }
                className={flowing ? `ctc-flow ctc-${status}` : undefined}
            />

            {/* tanda putus */}
            {status === "down" && (
                <g stroke={color} strokeWidth="1.8" strokeLinecap="round">
                    <line x1={mid - 3.5} y1={apexY - 3.5} x2={mid + 3.5} y2={apexY + 3.5} />
                    <line x1={mid + 3.5} y1={apexY - 3.5} x2={mid - 3.5} y2={apexY + 3.5} />
                </g>
            )}

            {/* caption status */}
            <text
                x={mid}
                y={y + FONT_CAPTION + 9}
                textAnchor="middle"
                fontSize={FONT_CAPTION}
                fontWeight="500"
                fill={color}
            >
                {caption}
            </text>
        </g>
    );
}

// ==============================
// COMPONENT
// ==============================

export default function StationConnectivity({ heartbeat, connected, dbOk }) {
    const [now, setNow] = useState(Date.now());

    useEffect(() => {
        const id = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(id);
    }, []);

    const links = computeLinks({ heartbeat, connected, dbOk, now });

    const captionFor = (key) =>
        key === "fdBe" && links.ageSec != null && links.fdBe !== "down"
            ? `${STATUS_TEXT[links[key]]} · ${links.ageSec} dtk`
            : STATUS_TEXT[links[key]];

    return (
        <Box w="100%" pt={2} overflow="hidden">
            <style>{`
                @keyframes ctc-dash { to { stroke-dashoffset: -12; } }
                @keyframes ctc-breathe {
                    0%, 100% { opacity: .10; }
                    50%      { opacity: .22; }
                }
                .ctc-flow { animation: ctc-dash 1.1s linear infinite; }
                .ctc-slow { animation-duration: ${(1.1 * PHI * PHI).toFixed(2)}s; }
                .ctc-halo { animation: ctc-breathe ${(PHI * PHI * PHI).toFixed(2)}s ease-in-out infinite; }
                @media (prefers-reduced-motion: reduce) {
                    .ctc-flow, .ctc-halo { animation: none; }
                }
            `}</style>

            <svg
                viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
                width="100%"
                role="img"
                aria-label="Status koneksi kamera, field device, backend, dan database"
                preserveAspectRatio="xMidYMid meet"
                style={{ fontFamily: "inherit" }}
            >
                {BRIDGES.map((b) => (
                    <Bridge
                        key={b.statusKey}
                        x1={b.x1}
                        x2={b.x2}
                        y={b.y}
                        status={links[b.statusKey]}
                        caption={captionFor(b.statusKey)}
                    />
                ))}

                {NODES.map((node) => {
                    const st = worst(links, node.links);
                    return (
                        <g key={node.key}>
                            {/* halo lembut mengikuti status */}
                            <circle
                                cx={node.x}
                                cy={node.y}
                                r={HALO}
                                fill={COLOR[st]}
                                opacity="0.12"
                                className={st === "ok" ? "ctc-halo" : undefined}
                            />
                            <image
                                href={node.image}
                                x={node.x - ICON / 2}
                                y={node.y - ICON / 2}
                                width={ICON}
                                height={ICON}
                                preserveAspectRatio="xMidYMid meet"
                            />
                            <text
                                x={node.x}
                                y={LABEL_Y}
                                textAnchor="middle"
                                fontSize={FONT_LABEL}
                                fontWeight="500"
                                fill="currentColor"
                                opacity="0.78"
                            >
                                {node.label}
                            </text>
                        </g>
                    );
                })}
            </svg>

            {!connected && (
                <Text
                    fontSize={`${FONT_NOTE}px`}
                    lineHeight={NOTE_LINE}
                    color="red.500"
                    mt={2}
                    maxW="34em"
                >
                    Dashboard terputus dari backend. Status lain tidak bisa dipastikan.
                </Text>
            )}
        </Box>
    );
}