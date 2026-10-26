import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Box, Flex, Text, HStack, VStack, Button } from "@chakra-ui/react";
import LiveStream from "./LiveStream";
import axios from "axios";
import { BASE_URL } from "../config";
/* =========================================================
   KEYFRAMES
   Disuntik sekali ke <head> (tanpa paket tambahan).
   Warna efek dibaca dari CSS variable --ctm-glow ("r,g,b")
   yang di-set per kartu sesuai state.
   ========================================================= */

const KEYFRAMES_ID = "station-card-keyframes";

const KEYFRAMES_CSS = `
@keyframes ctm-ping {
  0%   { transform: scale(1);   opacity: .55; }
  75%, 100% { transform: scale(2.6); opacity: 0; }
}
@keyframes ctm-sweep {
  0%   { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
@keyframes ctm-glow {
  0%, 100% { box-shadow: 0 0 0 0 rgba(var(--ctm-glow), 0), 0 0 0 0 rgba(var(--ctm-glow), 0); }
  50%      { box-shadow: 0 0 0 3px rgba(var(--ctm-glow), .16), 0 0 18px 0 rgba(var(--ctm-glow), .22); }
}
@keyframes ctm-alert {
  0%, 100% { box-shadow: 0 0 0 1px rgba(var(--ctm-glow), .35), 0 0 0 0 rgba(var(--ctm-glow), 0); }
  50%      { box-shadow: 0 0 0 2px rgba(var(--ctm-glow), .9),  0 0 22px 2px rgba(var(--ctm-glow), .35); }
}
@keyframes ctm-stripes {
  from { background-position: 0 0; }
  to   { background-position: 13px 0; }
}
@keyframes ctm-flash {
  0%   { background-color: rgba(var(--ctm-glow), .35); }
  100% { background-color: rgba(var(--ctm-glow), 0); }
}
@keyframes ctm-pop {
  0%   { transform: scale(.92); }
  60%  { transform: scale(1.06); }
  100% { transform: scale(1); }
}
@media (prefers-reduced-motion: reduce) {
  [data-ctm-motion] { animation: none !important; }
}
`;

if (typeof document !== "undefined" && !document.getElementById(KEYFRAMES_ID)) {
  const el = document.createElement("style");
  el.id = KEYFRAMES_ID;
  el.textContent = KEYFRAMES_CSS;
  document.head.appendChild(el);
}

/* =========================================================
   KONSTANTA
   ========================================================= */

const STATE_COLOR = {
  IDLE: { bg: "gray.100", text: "gray.600", dot: "gray.400", rgb: "156,163,175" },
  RUNNING: { bg: "green.50", text: "green.700", dot: "green.600", rgb: "34,197,94" },
  TRANSIT: { bg: "orange.50", text: "orange.700", dot: "orange.600", rgb: "249,115,22" },
  COMPLETE: { bg: "yellow.50", text: "yellow.700", dot: "yellow.600", rgb: "234,179,8" },
  TIMEOUT: { bg: "red.50", text: "red.700", dot: "red.600", rgb: "239,68,68" },
  FAILED: { bg: "red.50", text: "red.700", dot: "red.600", rgb: "239,68,68" },
  DISCARD: { bg: "red.50", text: "red.700", dot: "red.600", rgb: "239,68,68" },
  CAMERA_OFFLINE: { bg: "purple.50", text: "purple.700", dot: "purple.600", rgb: "106,27,154" },
  OFFLINE: { bg: "gray.100", text: "gray.600", dot: "gray.500", rgb: "156,163,175" },
  GOD_MODE: { bg: "blue.50", text: "blue.700", dot: "blue.600", rgb: "59,130,246" },
  SAVING_MODE: { bg: "orange.50", text: "orange.700", dot: "orange.600", rgb: "249,115,22" },
};

const STATE_LABEL = {
  IDLE: "Idle",
  RUNNING: "Running",
  TRANSIT: "Transit",
  COMPLETE: "Complete",
  TIMEOUT: "Timeout",
  FAILED: "Failed",
  DISCARD: "Discard",
  CAMERA_OFFLINE: "Camera Offline",
  OFFLINE: "Offline",
  SAVING_MODE: "Saving Mode",
  GOD_MODE: "God Mode",
};

const ACTIVE = ["RUNNING", "TRANSIT"];

const STALE_MS = 5000;
const RED_RGB = "239,68,68";
const TABULAR = { fontVariantNumeric: "tabular-nums" };

function fmt(sec) {
  if (sec == null) return "—";
  return `${Number(sec).toFixed(1)}s`;
}

/* Mengembalikan angka yang naik setiap kali `value` berubah.
   Perubahan pertama dari null/undefined (data baru tiba) diabaikan,
   supaya kartu tidak berkedip saat halaman baru dimuat. */
function useChangeKey(value) {
  const prev = useRef(value);
  const [key, setKey] = useState(0);

  useEffect(() => {
    if (prev.current !== value) {
      const initial = prev.current == null;
      prev.current = value;
      if (!initial) setKey((n) => n + 1);
    }
  }, [value]);

  return key;
}

/* =========================================================
   STATION CARD
   ========================================================= */

export default function StationCard({ station, heartbeat, selected, onClick }) {
  // Re-render tiap 250 ms:
  // elapsed mengalir & heartbeat yang hilang jadi OFFLINE
  const [, tick] = useState(0);
  const [liveOpen, setLiveOpen] = useState(false);
  const closeLive = useCallback(() => setLiveOpen(false), []);
  useEffect(() => {
    const t = setInterval(() => {
      tick((n) => n + 1);
    }, 1000);
    return () => clearInterval(t);
  }, []);

/* ---------- God Mode / Saving Mode ---------- */
const savingMode = station?.saving === true;   // God Mode = tidak saving
const [modeBusy, setModeBusy] = useState(false);
const [waitingGod, setWaitingGod] = useState(false);
const [modeError, setModeError] = useState("");

const toggleMode = async (e) => {
  e.stopPropagation();
  const toSaving = !savingMode;
  if (
    toSaving &&
    !window.confirm(
      `Aktifkan Saving Mode untuk L${station.line} ST${station.station_id}?\nHeartbeat berhenti dan live stream diputus.`
    )
  )
    return;

  setModeBusy(true);
  setModeError("");
  try {
    await axios.put(
      `${BASE_URL}/api/station/${station.line}/${station.station_id}/saving-mode`,
      { enabled: toSaving }
    );
    if (!toSaving) setWaitingGod(true);   // flag turun setelah FD hello
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
  const liveState = savingMode
    ? "SAVING_MODE"
    : stale
      ? "OFFLINE"
      : heartbeat?.state || "IDLE";
  const active = ACTIVE.includes(liveState);
  const modeKey = savingMode ? "SAVING_MODE" : "GOD_MODE";
  const modeColors = STATE_COLOR[modeKey];
  // elapsed = nilai terakhir dari FD + waktu sejak diterima
  // hanya berjalan saat state aktif
  const elapsedShown =
    stale || heartbeat?.elapsed_sec == null
      ? null
      : heartbeat.elapsed_sec +
      (active ? (Date.now() - heartbeat.received) / 1000 : 0);

  const colors = STATE_COLOR[liveState] || STATE_COLOR.OFFLINE;

  const takt = station?.takt_time;
  const targetCT = station?.target_cycle_time || takt;
  const lastCT = station?.last_cycle_sec;

  const isOverTakt = Boolean(targetCT && lastCT && lastCT > targetCT);
  const shortLim = Number(station?.short_anomaly_sec) || 0;
  const isShort = Boolean(shortLim > 0 && lastCT && lastCT < shortLim);

  // Siklus berjalan sudah melewati target
  const overrun = Boolean(
    liveState === "RUNNING" &&
    targetCT &&
    elapsedShown != null &&
    elapsedShown > targetCT
  );
  const crashed = liveState === "CAMERA_OFFLINE";
  const alert = liveState === "TIMEOUT" || liveState === "FAILED" || overrun || crashed;
  const glowRgb = overrun || liveState === "TIMEOUT" || liveState === "FAILED"
    ? RED_RGB
    : colors.rgb;

  const stationLabel =
    station?.station_name?.match(/^(Station [^-]+)/)?.[1]?.trim() ||
    `Station ${station?.station_id}`;

  const stationName =
    station?.station_name?.replace(/^Station [^-]+-\s*/, "") || "—";

  /* ---------- bar target: mengikuti waktu (elapsed / target) ---------- */
  const timed = Boolean(targetCT && elapsedShown != null);
  const flowing = timed && active; // waktu sedang berjalan
  const ratio = timed ? elapsedShown / targetCT : 0;
  const showBar = Boolean(targetCT);
  const barColor =
    ratio >= 1 ? "red.500" : ratio >= 0.85 ? "orange.400" : "green.500";

  /* ---------- pemicu efek saat data berubah ---------- */
  const stateKey = useChangeKey(liveState);
  const lastKey = useChangeKey(lastCT);
  const avgKey = useChangeKey(station?.avg_ct_today);
  const cycKey = useChangeKey(station?.cycles_today ?? 0);

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
      bg={selected ? "blue.50" : "white"}
      borderWidth="1px"
      borderColor={selected ? "blue.400" : "gray.200"}
      borderRadius="lg"
      p={4}
      cursor="pointer"
      minW="160px"
      opacity={liveState === "OFFLINE" ? 0.72 : 1}
      transition="all 0.15s ease"
      boxShadow={
        selected
          ? "0 0 0 1px var(--chakra-colors-blue-400), 0 4px 12px rgba(0,0,0,0.08)"
          : "sm"
      }
      _hover={{ boxShadow: "md", transform: "translateY(-1px)" }}
      _active={{ transform: "translateY(0)" }}
      _focusVisible={{
        outline: "2px solid",
        outlineColor: "blue.400",
        outlineOffset: "2px",
      }}
      style={{ "--ctm-glow": glowRgb }}
    >
      {/* =====================================================
          EFEK 1: cincin cahaya di sekeliling kartu
          - state aktif  : berdenyut pelan, warna state
          - alert        : berdenyut cepat, merah
          ===================================================== */}
      <Box
        aria-hidden
        position="absolute"
        inset="-1px"
        borderRadius="lg"
        pointerEvents="none"
        data-ctm-motion=""
        animation={
          alert
            ? "ctm-alert 1.2s ease-in-out infinite"
            : active
              ? "ctm-glow 2.4s ease-in-out infinite"
              : "none"
        }
      />

      {/* =====================================================
          EFEK 2: garis aksen atas
          Dibungkus wadah ber-radius sama dengan sudut dalam kartu
          (8px - border 1px = 7px) dan overflow hidden, sehingga
          garis mengikuti lengkung sudut dan tidak keluar kartu.
          Warna mengikuti state; bergerak (sweep) saat aktif/alert.
          ===================================================== */}
      <Box
        aria-hidden
        position="absolute"
        inset="0"
        borderRadius="7px"
        overflow="hidden"
        pointerEvents="none"
      >
        <Box
          h="3px"
          w="100%"
          data-ctm-motion=""
          style={{
            backgroundImage:
              active || alert
                ? `linear-gradient(90deg, rgba(${glowRgb},.25), rgba(${glowRgb},1), rgba(${glowRgb},.25))`
                : `linear-gradient(90deg, rgba(${glowRgb},.35), rgba(${glowRgb},.35))`,
            backgroundSize: "200% 100%",
            animation:
              active || alert
                ? `ctm-sweep ${alert ? "1.2s" : "2.4s"} linear infinite`
                : "none",
          }}
        />
      </Box>

      {/* =====================================================
          STATION LABEL
          ===================================================== */}
      <Text fontSize="11px" color="gray.500" mb={0.5} lineHeight="1.2">
        {stationLabel}
      </Text>

      <Text
        fontSize="sm"
        fontWeight="600"
        color="gray.800"
        mb={3}
        lineHeight="1.3"
        noOfLines={1}
      >
        {stationName}
      </Text>

      {/* =====================================================
          STATE
          EFEK 3: titik dengan cincin "ping" saat aktif
          EFEK 4: label "pop" sesaat ketika state berganti
          ===================================================== */}
      <Flex align="flex-start" justify="space-between" gap={2} mb={3}>
        <Flex
          key={stateKey}
          display="inline-flex"
          align="center"
          gap={1.5}
          bg={colors.bg}
          color={colors.text}
          borderRadius="full"
          px={2.5}
          py={1}
          fontSize="11px"
          fontWeight="600"
          lineHeight="1"
          transition="background 0.3s ease, color 0.3s ease"
          data-ctm-motion={stateKey > 0 ? "" : undefined}
          animation={stateKey > 0 ? "ctm-pop 0.35s ease-out" : undefined}
        >
          <Box position="relative" w="7px" h="7px" flexShrink={0}>
            {active && (
              <Box
                aria-hidden
                position="absolute"
                inset="0"
                borderRadius="full"
                bg={colors.dot}
                data-ctm-motion=""
                animation="ctm-ping 1.4s cubic-bezier(0, 0, 0.2, 1) infinite"
              />
            )}
            <Box
              position="relative"
              w="7px"
              h="7px"
              borderRadius="full"
              bg={colors.dot}
              transition="background 0.3s ease"
            />
          </Box>

          <Text as="span">{STATE_LABEL[liveState] || liveState}</Text>

          {elapsedShown != null && active && (
            <Text as="span" opacity={0.7} fontWeight="500" style={TABULAR}>
              {fmt(elapsedShown)}
            </Text>
          )}
        </Flex>

<VStack align="flex-end" gap={1}>
<Button
  h="21px"
  minW={0}
  px="8px"
  fontSize="11px"
  fontWeight="600"
  variant="subtle"
  bg={modeColors.bg}
  color={modeColors.text}
  _hover={{ opacity: 0.85 }}
  disabled={modeBusy || waitingGod || (liveState === "OFFLINE" && !savingMode)}
  title={
    liveState === "OFFLINE" && !savingMode
      ? "FD offline"
      : savingMode
        ? "Saving Mode aktif. Klik untuk kembali ke God Mode"
        : "God Mode aktif. Klik untuk masuk Saving Mode"
  }
  onClick={toggleMode}
  onKeyDown={(e) => e.stopPropagation()}
>
  <Box w="6px" h="6px" borderRadius="full" bg={modeColors.dot} flexShrink={0} />
  {modeBusy ? "…" : waitingGod ? "Menunggu FD…" : STATE_LABEL[modeKey]}
</Button>

  <Button
    h="21px"
    minW={0}
    px="8px"
    fontSize="11px"
    fontWeight="500"
    variant="subtle"
    colorPalette="red"
    disabled={liveState === "OFFLINE" || liveState === "SAVING_MODE"}
    title={
      liveState === "OFFLINE"
        ? "FD offline"
        : liveState === "SAVING_MODE"
          ? "Live tidak tersedia saat Saving Mode"
          : "Lihat kamera live"
    }
    onClick={(e) => {
      e.stopPropagation();
      setLiveOpen(true);
    }}
    onKeyDown={(e) => e.stopPropagation()}
  >
    ● Live
  </Button>

  {modeError && (
    <Text fontSize="10px" color="red.500" maxW="120px" textAlign="right">
      {modeError}
    </Text>
  )}
</VStack>
      </Flex>
      {/* =====================================================
          METRICS
          EFEK 5: nilai yang berubah menyala sebentar
          ===================================================== */}
      <HStack align="start" gap={3}>
        <Metric
          label="Last CT"
          valueColor={isOverTakt ? "red.500" : isShort ? "orange.500" : "gray.800"}
          value={fmt(lastCT)}
          flashKey={lastKey}
        />

        <Metric label="Avg today" value={fmt(station?.avg_ct_today)} flashKey={avgKey} />

        <Metric label="Cycles" value={station?.cycles_today ?? 0} flashKey={cycKey} />
      </HStack>

      {/* =====================================================
          TIME PROGRESS
          EFEK 6: bar mengikuti waktu berjalan (elapsed / target).
                  Hijau, oranye di 85%, merah di 100%.
                  Garis bergerak hanya saat waktu berjalan;
                  saat berhenti, bar diam di posisi terakhir.
          ===================================================== */}
      {showBar && (
        <Box mt={3}>
          <Flex justify="space-between" align="center" mb={1}>
            <Text fontSize="10px" color="gray.400">
              Elapsed vs Target
            </Text>

            <Text
              fontSize="10px"
              color={ratio >= 1 ? "red.500" : "gray.400"}
              fontWeight={ratio >= 1 ? "600" : "400"}
              style={TABULAR}
            >
              {`${fmt(elapsedShown)} / ${fmt(targetCT)}`}
            </Text>
          </Flex>

          <Box bg="gray.100" borderRadius="full" h="5px" overflow="hidden">
            <Box
              h="100%"
              borderRadius="full"
              bg={barColor}
              width={`${Math.min(ratio * 100, 100)}%`}
              transition="width 0.25s linear, background-color 0.3s ease"
              data-ctm-motion={flowing ? "" : undefined}
              style={
                flowing
                  ? {
                    backgroundImage:
                      "linear-gradient(135deg, rgba(255,255,255,.28) 25%, transparent 25%, transparent 50%, rgba(255,255,255,.28) 50%, rgba(255,255,255,.28) 75%, transparent 75%)",
                    backgroundSize: "13px 13px",
                    animation: "ctm-stripes 0.9s linear infinite",
                  }
                  : undefined
              }
            />
          </Box>
        </Box>
      )}
      {liveOpen &&
        createPortal(
          <div
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            <LiveStream
              line={station?.line}
              stationId={station?.station_id}
              name={stationName !== "—" ? stationName : ""}
              onClose={closeLive}
            />
          </div>,
          document.body
        )}
    </Box>
  );
}

/* =========================================================
   METRIC
   ========================================================= */

function Metric({ label, value, valueColor = "gray.800", flashKey = 0 }) {
  return (
    <VStack align="start" gap={0} minW="0">
      <Text fontSize="10px" color="gray.400" lineHeight="1.2" whiteSpace="nowrap">
        {label}
      </Text>

      <Text
        key={flashKey}
        fontSize="md"
        fontWeight="600"
        color={valueColor}
        lineHeight="1.4"
        whiteSpace="nowrap"
        borderRadius="sm"
        px="3px"
        mx="-3px"
        style={TABULAR}
        data-ctm-motion={flashKey > 0 ? "" : undefined}
        animation={flashKey > 0 ? "ctm-flash 0.9s ease-out" : undefined}
      >
        {value}
      </Text>
    </VStack>
  );
}