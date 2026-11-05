import React from "react";
import { Box, Text } from "@chakra-ui/react";

const num = (v) => (Number(v) > 0 ? Number(v) : null);
const fmtS = (v) => `${Number(v).toFixed(v % 1 ? 1 : 0)}s`;

// Warna zona berdasarkan posisi waktu t
// zoneAt: tanpa roi
function zoneAt(t, { min, short, target }) {
    if (min != null && t < min) return { bg: "gray.400", name: "Diabaikan" };
    if (short != null && t < short) return { bg: "orange.400", name: "Short" };
    if (target != null && t < target) return { bg: "green.500", name: "Normal" };
    return { bg: "red.500", name: "Long" };
}

export default function TimelineBar({ elapsed, min, short, target, active }) {
    const lim = { min: num(min), short: num(short), target: num(target) };

    const marks = [
        { key: "min", label: "Min", v: lim.min },
        { key: "short", label: "Short", v: lim.short },
        { key: "target", label: "Target", v: lim.target },
    ]
        .filter((m) => m.v != null)
        .sort((a, b) => a.v - b.v);

    if (marks.length === 0)
        return (
            <Text fontSize="11px" color="red.500">
                Limit empty time: min={String(min)}, short={String(short)}, target={String(target)}
            </Text>
        );

    const max = marks[marks.length - 1].v * 1.35;
    const pct = (v) => Math.min(Math.max((v / max) * 100, 0), 100);

    // Segmen zona: dari 0, tiap batas, sampai max
    const points = [0, ...marks.map((m) => m.v), max];
    const segments = points.slice(0, -1).map((from, i) => {
        const to = points[i + 1];
        return { from, to, zone: zoneAt((from + to) / 2, lim) };
    });

    const hasDot = elapsed != null;
    const e = hasDot ? Math.max(elapsed, 0) : 0;
    const ePct = pct(e);
    const dotZone = zoneAt(e, lim);

    const renderSegments = (opacity) =>
        segments.map((s, i) => (
            <Box
                key={i}
                position="absolute"
                top="0"
                bottom="0"
                left={`${pct(s.from)}%`}
                width={`${pct(s.to) - pct(s.from)}%`}
                bg={s.zone.bg}
                opacity={opacity}
            />
        ));

    return (
        <Box position="relative" h="38px" mt={1}>
            {/* Garis dasar: zona redup */}
            <Box position="absolute" left="0" right="0" top="15px" h="8px" borderRadius="full" overflow="hidden">
                {renderSegments(0.3)}

                {/* Bagian yang sudah dilewati: zona pekat, dipotong sampai posisi elapsed */}
                <Box
                    position="absolute"
                    inset="0"
                    style={{
                        clipPath: `inset(0 ${100 - ePct}% 0 0)`,
                        transition: "clip-path 0.25s linear",
                    }}
                >
                    {renderSegments(1)}
                </Box>
            </Box>

            {/* Tanda batas + label (selang-seling atas/bawah) */}
            {marks.map((m, i) => {
                const above = i % 2 === 0;
                return (
                    <Box key={m.key} position="absolute" left={`${pct(m.v)}%`} top="0" bottom="0" w="0">
                        <Box position="absolute" left="-0.5px" top="13px" w="1px" h="12px" bg="gray.600" />
                        <Text
                            position="absolute"
                            left="0"
                            transform="translateX(-50%)"
                            {...(above ? { top: "-1px" } : { bottom: "-1px" })}
                            fontSize="9px"
                            lineHeight="1"
                            color="gray.500"
                            whiteSpace="nowrap"
                            style={{ fontVariantNumeric: "tabular-nums" }}
                        >
                            {m.label} {fmtS(m.v)}
                        </Text>
                    </Box>
                );
            })}
        </Box>
    );
}