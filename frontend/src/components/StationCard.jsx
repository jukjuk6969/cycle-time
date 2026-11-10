import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Box, Flex, Text, VStack, Button } from "@chakra-ui/react";
import LiveStream from "./LiveStream";
import axios from "axios";
import { BASE_URL } from "../config";

const MONO = `"JetBrains Mono", "SF Mono", ui-monospace, Consolas, monospace`;

/* ── Warna status (palet Astra) ──────────────────────────────────────── */
const STATE_COLOR = {
  IDLE: { text: "var(--ct-ink-500)", border: "var(--ct-ink-300)", dot: "var(--ct-ink-400)", tint: "rgba(var(--ct-slate-rgb),.07)" },
  RUNNING: { text: "var(--ct-blue)", border: "var(--ct-blue)", dot: "var(--ct-blue)", tint: "rgba(var(--ct-blue-rgb),.07)" },
  TRANSIT: { text: "var(--ct-orange-text)", border: "var(--ct-orange-strong)", dot: "var(--ct-orange-strong)", tint: "rgba(var(--ct-orange-rgb),.08)" },
  COMPLETE: { text: "var(--ct-blue)", border: "var(--ct-blue)", dot: "var(--ct-blue)", tint: "rgba(var(--ct-blue-rgb),.07)" },
  TIMEOUT: { text: "var(--ct-red-text)", border: "var(--ct-red)", dot: "var(--ct-red)", tint: "rgba(var(--ct-red-rgb),.07)" },
  FAILED: { text: "var(--ct-red-text)", border: "var(--ct-red)", dot: "var(--ct-red)", tint: "rgba(var(--ct-red-rgb),.07)" },
  DISCARD: { text: "var(--ct-red-text)", border: "var(--ct-red)", dot: "var(--ct-red)", tint: "rgba(var(--ct-red-rgb),.07)" },
  CAMERA_OFFLINE: { text: "var(--ct-purple-text)", border: "var(--ct-purple)", dot: "var(--ct-purple)", tint: "rgba(var(--ct-purple-rgb),.07)" },
  OFFLINE: { text: "var(--ct-ink-400)", border: "var(--ct-ink-200)", dot: "var(--ct-ink-300)", tint: "rgba(var(--ct-slate-rgb),.07)" },
  GOD_MODE: { text: "var(--ct-blue)", border: "var(--ct-blue)", dot: "var(--ct-blue)", tint: "rgba(var(--ct-blue-rgb),.07)" },
  SAVING_MODE: { text: "var(--ct-orange-text)", border: "var(--ct-orange-strong)", dot: "var(--ct-orange-strong)", tint: "rgba(var(--ct-orange-rgb),.08)" },
};

const STATE_LABEL = {
  IDLE: "Idle", RUNNING: "Running", TRANSIT: "Transit", COMPLETE: "Complete",
  TIMEOUT: "Timeout", FAILED: "Failed", DISCARD: "Discard", CAMERA_OFFLINE: "Camera Offline",
  OFFLINE: "Offline", SAVING_MODE: "Saving Mode", GOD_MODE: "Full Mode",
};

const ACTIVE = ["RUNNING", "TRANSIT"];
const STALE_MS = 5000;
const TABULAR = { fontVariantNumeric: "tabular-nums" };
const EASE = "cubic-bezier(.2,.8,.2,1)";

/* ── Keyframes (disuntik sekali ke <head>) ───────────────────────────── */
const CARD_KEYFRAMES = `
@keyframes ctm-rise  { from{opacity:0;transform:translateY(8px) scale(.985)} to{opacity:1;transform:translateY(0) scale(1)} }
@keyframes ctm-ping  { 0%{transform:scale(1);opacity:.55} 75%,100%{transform:scale(2.6);opacity:0} }
@keyframes ctm-alert { 0%,100%{box-shadow:0 0 0 0 rgba(var(--ct-red-rgb),0)} 50%{box-shadow:0 0 0 3px rgba(var(--ct-red-rgb),.28)} }
@keyframes ctm-sheen { 0%{transform:translateX(-100%)} 100%{transform:translateX(220%)} }
@keyframes ctm-flash { 0%{background-color:rgba(var(--ct-blue-rgb),.16)} 100%{background-color:transparent} }
@keyframes ctm-blink { 0%,100%{opacity:1} 50%{opacity:.35} }
@keyframes ctm-pop   { from{opacity:0;transform:translateY(-3px)} to{opacity:1;transform:translateY(0)} }
`;

function ensureKeyframes() {
  if (typeof document === "undefined") return;
  if (document.getElementById("ctm-card-keyframes")) return;
  const el = document.createElement("style");
  el.id = "ctm-card-keyframes";
  el.textContent = CARD_KEYFRAMES;
  document.head.appendChild(el);
}
ensureKeyframes();

function fmt(sec) {
  if (sec == null) return "—";
  return `${Number(sec).toFixed(1)}s`;
}

/* Angka yang berkedip halus saat nilainya berubah */
function FlashValue({ value, children, ...props }) {
  const prev = useRef(value);
  const [flash, setFlash] = useState(false);

  useEffect(() => {
    if (prev.current === value) return undefined;
    prev.current = value;
    setFlash(true);
    const t = setTimeout(() => setFlash(false), 650);
    return () => clearTimeout(t);
  }, [value]);

  return (
    <Text
      px="3px" mx="-3px" borderRadius="3px"
      style={{ ...TABULAR, animation: flash ? "ctm-flash 650ms ease-out" : "none" }}
      {...props}
    >
      {children}
    </Text>
  );
}

function Label({ children }) {
  return (
    <Text fontSize="9px" color="var(--ct-ink-500)" fontWeight="600" letterSpacing="0.06em" textTransform="uppercase">
      {children}
    </Text>
  );
}

export default function StationCard({ station, heartbeat, selected, onClick }) {
  const [, tick] = useState(0);
  const [liveOpen, setLiveOpen] = useState(false);
  const closeLive = useCallback(() => setLiveOpen(false), []);

  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const savingMode = station?.saving === true;
  const [modeBusy, setModeBusy] = useState(false);
  const [waitingGod, setWaitingGod] = useState(false);
  const [modeError, setModeError] = useState("");

  const toggleMode = async (e) => {
    e.stopPropagation();
    const toSaving = !savingMode;
    if (toSaving && !window.confirm(`Aktifkan Saving Mode untuk L${station.line} ST${station.station_id}?\nHeartbeat berhenti dan live stream diputus.`)) return;

    setModeBusy(true);
    setModeError("");
    try {
      await axios.put(`${BASE_URL}/api/station/${station.line}/${station.station_id}/saving-mode`, { enabled: toSaving });
      if (!toSaving) setWaitingGod(true);
    } catch (err) {
      setModeError(err.response?.data?.error || err.message || "Gagal mengubah mode.");
    } finally {
      setModeBusy(false);
    }
  };

  useEffect(() => { if (!savingMode) setWaitingGod(false); }, [savingMode]);
  useEffect(() => {
    if (!waitingGod) return undefined;
    const t = setTimeout(() => setWaitingGod(false), 30000);
    return () => clearTimeout(t);
  }, [waitingGod]);
  useEffect(() => {
    if (!modeError) return undefined;
    const t = setTimeout(() => setModeError(""), 5000);
    return () => clearTimeout(t);
  }, [modeError]);

  const age = heartbeat?.received ? Date.now() - heartbeat.received : Infinity;
  const stale = age > STALE_MS;
  const liveState = savingMode ? "SAVING_MODE" : stale ? "OFFLINE" : heartbeat?.state || "IDLE";
  const active = ACTIVE.includes(liveState);
  const modeKey = savingMode ? "SAVING_MODE" : "GOD_MODE";

  const elapsedShown = stale || heartbeat?.elapsed_sec == null ? null : heartbeat.elapsed_sec + (active ? (Date.now() - heartbeat.received) / 1000 : 0);
  const colors = STATE_COLOR[liveState] || STATE_COLOR.OFFLINE;

  const takt = station?.takt_time;
  const targetCT = station?.target_cycle_time || takt;
  const lastCT = station?.last_cycle_sec;

  const isOverTakt = Boolean(targetCT && lastCT && lastCT > targetCT);
  const shortLim = Number(station?.short_anomaly_sec) || 0;
  const isShort = Boolean(shortLim > 0 && lastCT && lastCT < shortLim);

  const overrun = Boolean(liveState === "RUNNING" && targetCT && elapsedShown != null && elapsedShown > targetCT);
  const crashed = liveState === "CAMERA_OFFLINE";
  const alert = liveState === "TIMEOUT" || liveState === "FAILED" || overrun || crashed;

  const stationLabel = station?.station_name?.match(/^(Station [^-]+)/)?.[1]?.trim() || `Station ${station?.station_id}`;
  const stationName = station?.station_name?.replace(/^Station [^-]+-\s*/, "") || "—";

  const timed = Boolean(targetCT && elapsedShown != null);
  const ratio = timed ? elapsedShown / targetCT : 0;
  const showBar = Boolean(targetCT);
  const barColor = ratio >= 1 ? "var(--ct-red)" : ratio >= 0.85 ? "var(--ct-orange-strong)" : "var(--ct-blue)";
  const barGradient =
    ratio >= 1 ? "linear-gradient(90deg,var(--ct-red),var(--ct-red-light))"
    : ratio >= 0.85 ? "linear-gradient(90deg,var(--ct-orange-strong),var(--ct-orange-light))"
    : "linear-gradient(90deg,var(--ct-blue),var(--ct-blue-light))";

  const handleKeyDown = (e) => {
    if (!onClick) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onClick(e);
    }
  };

  return (
    <Box
      role="button"
      tabIndex={0}
      onKeyDown={handleKeyDown}
      onClick={onClick}
      position="relative"
      overflow="hidden"
      bg="var(--ct-surface)"
      backgroundImage={`linear-gradient(180deg, ${colors.tint} 0%, transparent 42%)`}
      borderWidth="1px"
      borderColor={selected ? "var(--ct-blue)" : "var(--ct-ink-200)"}
      borderLeftWidth="4px"
      borderLeftColor={colors.border}
      borderRadius="6px"
      p={4}
      cursor="pointer"
      minW="0"
      opacity={liveState === "OFFLINE" ? 0.6 : 1}
      transition={`transform 200ms ${EASE}, box-shadow 200ms ${EASE}, border-color 200ms ease, opacity 300ms ease`}
      boxShadow={selected ? "0 0 0 1px var(--ct-blue), 0 8px 18px -6px rgba(var(--ct-blue-rgb),.25)" : "var(--ct-shadow-xs)"}
      style={{ animation: `ctm-rise 380ms ${EASE} both` }}
      _hover={{
        transform: "translateY(-2px)",
        boxShadow: selected
          ? "0 0 0 1px var(--ct-blue), 0 10px 22px -6px rgba(var(--ct-blue-rgb),.3)"
          : "var(--ct-shadow-md)",
        borderColor: selected ? "var(--ct-blue)" : "var(--ct-ink-300)",
      }}
      _active={{ transform: "translateY(0) scale(.995)" }}
      _focusVisible={{ outline: "2px solid", outlineColor: "var(--ct-blue)", outlineOffset: "2px" }}
    >
      {/* Cincin alert berdenyut */}
      {alert && (
        <Box
          position="absolute" inset="0" borderRadius="6px" pointerEvents="none"
          style={{ animation: "ctm-alert 1.6s ease-in-out infinite" }}
          aria-hidden="true"
        />
      )}

      {/* Header */}
      <Flex justify="space-between" align="flex-start" mb={3} gap={2}>
        <Box minW="0">
          <Text fontSize="10px" color="var(--ct-ink-500)" fontWeight="600" mb={1} letterSpacing="0.05em" textTransform="uppercase">
            {stationLabel}
          </Text>
          <Text fontSize="14px" fontWeight="700" color="var(--ct-black)" lineHeight="1.2" noOfLines={1}>
            {stationName}
          </Text>
        </Box>

        {/* Status badge */}
        <Flex
          key={liveState}
          align="center" gap={1.5} px={2} py={1} flexShrink={0}
          bg="var(--ct-surface)" borderRadius="full" border="1px solid" borderColor={colors.border}
          style={{ animation: "ctm-pop 260ms ease-out" }}
        >
          <Box position="relative" w="6px" h="6px">
            {(active || alert) && (
              <Box
                position="absolute" inset="0" borderRadius="full" bg={colors.dot}
                style={{ animation: "ctm-ping 1.6s cubic-bezier(0,0,.2,1) infinite" }}
                aria-hidden="true"
              />
            )}
            <Box position="relative" w="6px" h="6px" borderRadius="full" bg={colors.dot} />
          </Box>
          <Text fontSize="10px" fontWeight="700" color={colors.text} fontFamily={MONO} letterSpacing="0.02em">
            {STATE_LABEL[liveState] || liveState}
          </Text>
        </Flex>
      </Flex>

      {/* Metrics */}
      <Flex gap={5} mb={4} align="flex-end">
        <VStack align="start" gap={0.5}>
          <Label>Last CT</Label>
          <FlashValue
            value={lastCT} fontSize="17px" fontWeight="700" fontFamily={MONO} lineHeight="1.1"
            color={isOverTakt ? "var(--ct-red)" : isShort ? "var(--ct-orange-strong)" : "var(--ct-black)"}
          >
            {fmt(lastCT)}
          </FlashValue>
        </VStack>

        <VStack align="start" gap={0.5}>
          <Label>Avg Today</Label>
          <FlashValue value={station?.avg_ct_today} fontSize="15px" fontWeight="600" fontFamily={MONO} lineHeight="1.15" color="var(--ct-ink-600)">
            {fmt(station?.avg_ct_today)}
          </FlashValue>
        </VStack>

        <VStack align="start" gap={0.5}>
          <Label>Cycles</Label>
          <FlashValue value={station?.cycles_today ?? 0} fontSize="15px" fontWeight="600" fontFamily={MONO} lineHeight="1.15" color="var(--ct-ink-600)">
            {station?.cycles_today ?? 0}
          </FlashValue>
        </VStack>
      </Flex>

      {/* Progress bar */}
      {showBar && (
        <Box mb={4}>
          <Flex justify="space-between" align="center" mb={1.5}>
            <Label>Elapsed vs Target</Label>
            <Text
              fontSize="10px" color={ratio >= 1 ? "var(--ct-red)" : "var(--ct-ink-600)"}
              fontWeight={ratio >= 1 ? "700" : "600"} fontFamily={MONO} style={TABULAR}
            >
              {`${fmt(elapsedShown)} / ${fmt(targetCT)}`}
            </Text>
          </Flex>
          <Box bg="var(--ct-ink-100)" borderRadius="full" h="6px" overflow="hidden" boxShadow="inset 0 1px 1px rgba(0,0,0,.05)">
            <Box
              position="relative" overflow="hidden" h="100%" borderRadius="full"
              width={`${Math.min(ratio * 100, 100)}%`}
              style={{ background: barGradient, backgroundColor: barColor }}
              transition="width 0.6s cubic-bezier(.4,0,.2,1), background 0.3s ease"
            >
              {active && ratio > 0 && (
                <Box
                  position="absolute" top="0" bottom="0" left="0" w="45%"
                  style={{
                    background: "linear-gradient(90deg, transparent, rgba(255,255,255,.55), transparent)",
                    animation: "ctm-sheen 1.8s ease-in-out infinite",
                  }}
                  aria-hidden="true"
                />
              )}
            </Box>
          </Box>
        </Box>
      )}

      {/* Actions */}
      <Flex justify="space-between" align="center" pt={2} borderTop="1px solid var(--ct-ink-100)">
        <Button
          h="26px" px="10px" fontSize="10px" fontWeight="700"
          variant="outline" borderColor={colors.border} color={colors.text}
          borderRadius="full"
          transition={`background-color 150ms, transform 150ms ${EASE}`}
          _hover={{ bg: colors.tint, transform: "translateY(-1px)" }}
          _active={{ transform: "scale(.97)" }}
          disabled={modeBusy || waitingGod || (liveState === "OFFLINE" && !savingMode)}
          onClick={toggleMode} onKeyDown={(e) => e.stopPropagation()}
        >
          <Box
            w="6px" h="6px" borderRadius="full" bg={colors.dot} mr={1.5}
            style={{ animation: modeBusy || waitingGod ? "ctm-blink 1s ease-in-out infinite" : "none" }}
          />
          {modeBusy ? "PROCESSING..." : waitingGod ? "WAITING" : STATE_LABEL[modeKey].toUpperCase()}
        </Button>

        <Button
          h="26px" px="10px" fontSize="10px" fontWeight="700" fontFamily={MONO}
          variant="outline" borderColor="var(--ct-ink-200)" color="var(--ct-ink-600)"
          borderRadius="full"
          transition={`background-color 150ms, border-color 150ms, transform 150ms ${EASE}`}
          _hover={{ borderColor: "var(--ct-ink-300)", bg: "var(--ct-ink-50)", transform: "translateY(-1px)" }}
          _active={{ transform: "scale(.97)" }}
          disabled={liveState === "OFFLINE" || liveState === "SAVING_MODE"}
          onClick={(e) => { e.stopPropagation(); setLiveOpen(true); }} onKeyDown={(e) => e.stopPropagation()}
        >
          <Box as="span" color="var(--ct-red)" mr={1.5} style={{ animation: "ctm-blink 1.4s ease-in-out infinite" }}>●</Box> LIVE
        </Button>
      </Flex>

      {modeError && (
        <Text fontSize="10px" color="var(--ct-red)" mt={2} fontFamily={MONO} style={{ animation: "ctm-pop 240ms ease-out" }}>
          {modeError}
        </Text>
      )}

      {liveOpen && createPortal(
        <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
          <LiveStream line={station?.line} stationId={station?.station_id} name={stationName !== "—" ? stationName : ""} onClose={closeLive} />
        </div>,
        document.body
      )}
    </Box>
  );
}