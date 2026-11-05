import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Box, Flex, Text, HStack, VStack, Button, Switch } from "@chakra-ui/react";
import LiveStream from "./LiveStream";
import axios from "axios";
import { BASE_URL } from "../config";

const MONO = `"JetBrains Mono", "SF Mono", ui-monospace, Consolas, monospace`;

// Palet Warna Astra Visteon (Dipertegas untuk Dashboard Industri)
const STATE_COLOR = {
  IDLE: { text: "#64748B", border: "#CBD5E1", dot: "#94A3B8" },
  RUNNING: { text: "#005A9C", border: "#005A9C", dot: "#005A9C" }, // Astra Blue
  TRANSIT: { text: "#C2410C", border: "#F58025", dot: "#F58025" }, // Astra Orange
  COMPLETE: { text: "#005A9C", border: "#005A9C", dot: "#005A9C" },
  TIMEOUT: { text: "#B91C1C", border: "#DC2626", dot: "#DC2626" },
  FAILED: { text: "#B91C1C", border: "#DC2626", dot: "#DC2626" },
  DISCARD: { text: "#B91C1C", border: "#DC2626", dot: "#DC2626" },
  CAMERA_OFFLINE: { text: "#6B21A8", border: "#9333EA", dot: "#9333EA" },
  OFFLINE: { text: "#94A3B8", border: "#E2E8F0", dot: "#CBD5E1" },
  GOD_MODE: { text: "#005A9C", border: "#005A9C", dot: "#005A9C" },
  SAVING_MODE: { text: "#C2410C", border: "#F58025", dot: "#F58025" },
};

const STATE_LABEL = {
  IDLE: "Idle", RUNNING: "Running", TRANSIT: "Transit", COMPLETE: "Complete",
  TIMEOUT: "Timeout", FAILED: "Failed", DISCARD: "Discard", CAMERA_OFFLINE: "Camera Offline",
  OFFLINE: "Offline", SAVING_MODE: "Saving Mode", GOD_MODE: "Full Mode",
};

const ACTIVE = ["RUNNING", "TRANSIT"];
const STALE_MS = 5000;
const TABULAR = { fontVariantNumeric: "tabular-nums" };

function fmt(sec) {
  if (sec == null) return "—";
  return `${Number(sec).toFixed(1)}s`;
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
  const barColor = ratio >= 1 ? "#DC2626" : ratio >= 0.85 ? "#F58025" : "#005A9C";

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
      bg="white"
      borderWidth="1px" 
      borderColor={selected ? "#005A9C" : "#E2E8F0"}
      borderLeftWidth="4px"
      borderLeftColor={colors.border}
      borderRadius="4px" 
      p={4} 
      cursor="pointer" 
      minW="0"
      opacity={liveState === "OFFLINE" ? 0.6 : 1}
      transition="all 0.2s ease"
      boxShadow={selected ? "0 0 0 1px #005A9C, 0 4px 6px -1px rgba(0,0,0,0.05)" : "0 1px 2px rgba(0,0,0,0.02)"}
      _hover={{ boxShadow: "0 4px 12px rgba(0,0,0,0.06)", borderColor: selected ? "#005A9C" : "#CBD5E1" }}
      _focusVisible={{ outline: "2px solid", outlineColor: "#005A9C", outlineOffset: "2px" }}
    >
      {/* Header */}
      <Flex justify="space-between" align="flex-start" mb={3}>
        <Box>
          <Text fontSize="10px" color="#64748B" fontWeight="600" mb={1} letterSpacing="0.05em" textTransform="uppercase">
            {stationLabel}
          </Text>
          <Text fontSize="14px" fontWeight="700" color="#231F20" lineHeight="1.2" noOfLines={1}>
            {stationName}
          </Text>
        </Box>
        
        {/* Status Badge */}
        <Flex align="center" gap={1.5} px={2} py={1} bg="#F8FAFC" borderRadius="4px" border="1px solid #E2E8F0">
          <Box w="6px" h="6px" borderRadius="full" bg={colors.dot} />
          <Text fontSize="10px" fontWeight="700" color={colors.text} fontFamily={MONO} letterSpacing="0.02em">
            {STATE_LABEL[liveState] || liveState}
          </Text>
        </Flex>
      </Flex>

      {/* Metrics Grid */}
      <Flex gap={4} mb={4} align="flex-end">
        <VStack align="start" gap={0.5}>
          <Text fontSize="9px" color="#64748B" fontWeight="600" letterSpacing="0.05em">LAST CT</Text>
          <Text fontSize="15px" fontWeight="700" fontFamily={MONO} style={TABULAR} 
            color={isOverTakt ? "#DC2626" : isShort ? "#F58025" : "#231F20"}>
            {fmt(lastCT)}
          </Text>
        </VStack>
        
        <VStack align="start" gap={0.5}>
          <Text fontSize="9px" color="#64748B" fontWeight="600" letterSpacing="0.05em">AVG TODAY</Text>
          <Text fontSize="15px" fontWeight="600" fontFamily={MONO} style={TABULAR} color="#475569">
            {fmt(station?.avg_ct_today)}
          </Text>
        </VStack>

        <VStack align="start" gap={0.5}>
          <Text fontSize="9px" color="#64748B" fontWeight="600" letterSpacing="0.05em">CYCLES</Text>
          <Text fontSize="15px" fontWeight="600" fontFamily={MONO} style={TABULAR} color="#475569">
            {station?.cycles_today ?? 0}
          </Text>
        </VStack>
      </Flex>

      {/* Progress Bar */}
      {showBar && (
        <Box mb={4}>
          <Flex justify="space-between" align="center" mb={1.5}>
            <Text fontSize="9px" color="#64748B" fontWeight="600" letterSpacing="0.05em">ELAPSED VS TARGET</Text>
            <Text fontSize="10px" color={ratio >= 1 ? "#DC2626" : "#475569"} fontWeight={ratio >= 1 ? "700" : "600"} fontFamily={MONO} style={TABULAR}>
              {`${fmt(elapsedShown)} / ${fmt(targetCT)}`}
            </Text>
          </Flex>
          <Box bg="#F1F5F9" borderRadius="2px" h="6px" overflow="hidden">
            <Box h="100%" bg={barColor} width={`${Math.min(ratio * 100, 100)}%`} transition="width 0.3s ease, background-color 0.3s ease" />
          </Box>
        </Box>
      )}

      {/* Actions */}
      <Flex justify="space-between" align="center" pt={2} borderTop="1px solid #F1F5F9">
        <Button 
          h="24px" px="10px" fontSize="10px" fontWeight="700" 
          variant="outline" borderColor={colors.border} color={colors.text}
          _hover={{ bg: "#F8FAFC" }}
          disabled={modeBusy || waitingGod || (liveState === "OFFLINE" && !savingMode)}
          onClick={toggleMode} onKeyDown={(e) => e.stopPropagation()}
        >
          <Box w="6px" h="6px" borderRadius="full" bg={colors.dot} mr={1.5} />
          {modeBusy ? "PROCESSING..." : waitingGod ? "WAITING" : STATE_LABEL[modeKey].toUpperCase()}
        </Button>

        <Button 
          h="24px" px="10px" fontSize="10px" fontWeight="700" fontFamily={MONO}
          variant="outline" borderColor="#E2E8F0" color="#475569"
          _hover={{ borderColor: "#CBD5E1", bg: "#F8FAFC" }}
          disabled={liveState === "OFFLINE" || liveState === "SAVING_MODE"}
          onClick={(e) => { e.stopPropagation(); setLiveOpen(true); }} onKeyDown={(e) => e.stopPropagation()}
        >
          <Box as="span" color="#DC2626" mr={1.5}>●</Box> LIVE
        </Button>
      </Flex>

      {modeError && <Text fontSize="10px" color="#DC2626" mt={2} fontFamily={MONO}>{modeError}</Text>}

      {liveOpen && createPortal(
        <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
          <LiveStream line={station?.line} stationId={station?.station_id} name={stationName !== "—" ? stationName : ""} onClose={closeLive} />
        </div>,
        document.body
      )}
    </Box>
  );
}