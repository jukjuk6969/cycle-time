/**
 * App.jsx — CT Monitor dashboard shell.
 * Palette: Astra blue #005A9C · orange #F7941D · ink #231F20
 */

import React, { useState, useRef, useEffect } from "react";
import {
  ChakraProvider, Box, Flex, Text, Button, Image,
  SimpleGrid, HStack, VStack,
} from "@chakra-ui/react";

import { system } from "./theme";
import StationConnectivity, { useDbHealth } from "./components/StationConnectivity";
import { useSocket } from "./hooks/useSocket";
import StationCard from "./components/StationCard";
import StationDetail from "./components/StationDetail";
import AnomalyList from "./components/AnomalyList";
import StationTimeline from "./components/StationTimeline";
import StationRegister from "./components/StationRegister";
import logoGif from "./assets/logo.png";
import { AuthProvider } from "./hooks/useAuth";
import AuthGate from "./components/AuthGate";
import UserMenu from "./components/UserMenu";
import LineGroups from "./components/LineGroups";
import StationDrawer, { DRAWER_W, usePushLayout } from "./components/StationDrawer";

/* ── Palette ─────────────────────────────────────────────────────────── */
const P = {
  blue: "#005A9C", blueHover: "#004880", blueActive: "#003A6B",
  blue700: "#004A82", blue200: "#99BFDD", blue100: "#C5DAEC", blue50: "#EDF4FA",
  orange: "#F7941D", orangeHover: "#DE7E0A", orange700: "#C5660A",
  orange200: "#FAD1A0", orange100: "#FCE4C5", orange50: "#FEF4E5",
  black: "#231F20",
  ink700: "#334155", ink600: "#475569", ink500: "#64748B",
  ink400: "#94A3B8", ink300: "#CBD5E1", ink200: "#E2E8F0",
  ink100: "#F1F5F9", ink50: "#F8FAFC",
  white: "#FFFFFF", canvas: "#F4F6F8",
};

/* ── Type / space / size ─────────────────────────────────────────────── */
const SANS = `"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
const MONO = `"JetBrains Mono", "SF Mono", ui-monospace, "Cascadia Code", Consolas, monospace`;

const FS = { micro: "10px", tiny: "11px", cap: "12px", body: "13px", med: "15px" };
const SP = { xs: "3px", sm: "5px", md: "8px", lg: "13px", xl: "21px" };
const RADII = { xs: "2px", sm: "3px", md: "4px", lg: "6px" };
const SZ = {
  header: "72px", action: "30px", pill: "26px",
  metricValue: "20px", metricLabel: "11px", metricMinW: "78px",
  divider: "24px", logo: "32px",
};
const PX = { base: SP.lg, md: SP.xl };
const PY = { base: SP.lg, md: SP.xl };
const FOCUS_BLUE = { boxShadow: `0 0 0 3px ${P.blue100}` };
const FOCUS_ORANGE = { boxShadow: `0 0 0 3px ${P.orange100}` };

/* ── Keyframes ───────────────────────────────────────────────────────── */
const KEYFRAMES = `
@keyframes astra-pulse      { 0%{box-shadow:0 0 0 0 rgba(0,90,156,.35)} 70%{box-shadow:0 0 0 5px rgba(0,90,156,0)} 100%{box-shadow:0 0 0 0 rgba(0,90,156,0)} }
@keyframes astra-pulse-warn { 0%{box-shadow:0 0 0 0 rgba(247,148,29,.4)} 70%{box-shadow:0 0 0 5px rgba(247,148,29,0)} 100%{box-shadow:0 0 0 0 rgba(247,148,29,0)} }
@keyframes astra-flash      { 0%{background-color:rgba(0,90,156,.14)} 100%{background-color:transparent} }
@keyframes astra-slide      { from{opacity:0;transform:translateY(-6px)} to{opacity:1;transform:translateY(0)} }
@keyframes astra-fade       { from{opacity:0} to{opacity:1} }
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.001ms!important;animation-iteration-count:1!important;transition-duration:.001ms!important}}
`;

const stKey = (s) => `${s.line}-${s.station_id}`;

/* ── App ─────────────────────────────────────────────────────────────── */
export default function App() {
  return (
    <AuthProvider>
      <ChakraProvider value={system}>
        <AuthGate>
          <Dashboard />
        </AuthGate>
      </ChakraProvider>
    </AuthProvider>
  );
}

function Dashboard() {
  const {
    summary, heartbeats, recentCycles, connected,
    fetchHistory, fetchHourly, fetchStats, exportCSV,
  } = useSocket();

  const [page, setPage] = useState("dashboard");
  const dbOk = useDbHealth(connected);
  const push = usePushLayout();
  const [selectedKey, setSelectedKey] = useState(null);

  const selected = summary.find((s) => stKey(s) === selectedKey) || null;

  const totalCycles = summary.reduce((s, st) => s + (st.cycles_today || 0), 0);
  const avgCT = summary.length
    ? summary.reduce((s, st) => s + (st.avg_ct_today || 0), 0) /
        (summary.filter((s) => s.avg_ct_today).length || 1)
    : 0;
  const overTakt = summary.filter(
    (s) => s.last_cycle_sec && s.takt_time && s.last_cycle_sec > s.takt_time
  ).length;

  if (page === "station-register") {
    return <StationRegisterPage onBack={() => setPage("dashboard")} />;
  }

  return (
    <Box
      minH="100vh" bg={P.canvas} fontFamily={SANS} position="relative"
      style={{
        paddingRight: push && selected ? DRAWER_W : 0,
        transition: "padding-right 220ms cubic-bezier(.2,.8,.2,1)",
      }}
      sx={{
        "&::before": {
          content: '""', position: "absolute", inset: 0, pointerEvents: "none",
          backgroundImage:
            "linear-gradient(to right, rgba(0,90,156,.025) 1px, transparent 1px), linear-gradient(to bottom, rgba(0,90,156,.025) 1px, transparent 1px)",
          backgroundSize: "32px 32px",
          animation: "astra-fade 900ms ease-out",
        },
      }}
    >
      <style>{KEYFRAMES}</style>

      <Header
        totalCycles={totalCycles}
        avgCT={avgCT}
        overTakt={overTakt}
        connected={connected}
        onOpenRegister={() => setPage("station-register")}
      />

      <Box maxW="1800px" mx="auto" px={PX} py={PY} position="relative" zIndex={1}>
        <StationGrid
          summary={summary}
          heartbeats={heartbeats}
          connected={connected}
          selectedKey={selectedKey}
          onSelect={(k) => setSelectedKey(k === selectedKey ? null : k)}
        />

<Box mb={SP.lg}>
  <AnomalyList
    summary={summary}
    line={selected?.line ?? ""}
    stationId={selected?.station_id ?? ""}
  />
</Box>
<StationDrawer
  open={!!selected}
  onClose={() => setSelectedKey(null)}
  title={selected ? `Station Inspect · L${selected.line} ST${selected.station_id}` : ""}
  scrollKey={selectedKey}
  push={push}
>
  {selected && (
    <SelectedStationDetail
      station={selected}
      heartbeat={heartbeats[stKey(selected)]}
      connected={connected}
      dbOk={dbOk}
      fetchHistory={fetchHistory}
      fetchHourly={fetchHourly}
      fetchStats={fetchStats}
      exportCSV={exportCSV}
      recentCycles={recentCycles}
    />
  )}
</StationDrawer>
      </Box>
    </Box>
  );
}

/* ── Header ──────────────────────────────────────────────────────────── */
function Header({ totalCycles, avgCT, overTakt, connected, onOpenRegister }) {
  return (
    <Flex
      h={{ base: "auto", md: SZ.header }} minH={{ md: SZ.header }}
      px={PX} py={{ base: SP.lg, md: 0 }}
      bg={P.white} borderBottomWidth="1px" borderColor={P.ink200}
      align={{ base: "stretch", md: "center" }} justify="space-between"
      gap={SP.md} direction={{ base: "column", md: "row" }}
      boxShadow="0 1px 2px rgba(35,31,32,.02)"
      position="relative" zIndex={10}
    >
      <BrandBlock subtitle="Final Assembly" />

      <Flex
        align="center" justify={{ base: "space-between", md: "flex-end" }}
        gap={{ base: SP.md, md: SP.xl }} flexWrap="wrap" minW="0"
      >
        <Button
          variant="outline" onClick={onOpenRegister}
          h={SZ.action} px={SP.lg}
          fontSize={FS.tiny} fontWeight="700" fontFamily={MONO}
          letterSpacing="0.08em" textTransform="uppercase"
          borderRadius={RADII.md} borderColor={P.ink200} color={P.ink600} bg={P.white}
          flexShrink={0} transition="background-color 120ms, border-color 120ms, color 120ms"
          _hover={{ bg: P.blue50, borderColor: P.blue200, color: P.blue }}
          _active={{ bg: P.blue100, color: P.blueActive }}
          _focusVisible={FOCUS_BLUE}
        >
          Station Register
        </Button>

        <HStack gap={SP.md} align="center" flexShrink={0}>
          <Metric value={totalCycles} label="Total cycles" />
          <VDiv />
          <Metric
            value={avgCT > 0 ? `${avgCT.toFixed(1)}s` : "—"}
            label="Avg cycle time"
            tone={avgCT > 0 ? "ink" : "muted"}
          />
          <VDiv />
          <Metric
            value={overTakt}
            label="Over takt"
            tone={overTakt > 0 ? "orange" : "ink"}
          />
        </HStack>

        <ConnectionPill connected={connected} />
        <UserMenu />
      </Flex>
    </Flex>
  );
}

function BrandBlock({ subtitle }) {
  return (
    <HStack gap={SP.md} align="center" minW="0">
      <Image src={logoGif} alt="CT Monitor" h={SZ.logo} w="auto" objectFit="contain" flexShrink={0} />
      <VStack align="flex-start" gap="2px" minW="0">
        <Text fontSize={FS.med} fontWeight="700" color={P.black} lineHeight="1.15" letterSpacing="-0.01em">
          CT Monitor
        </Text>
        <Text
          fontSize={FS.micro} color={P.ink500} fontFamily={MONO}
          letterSpacing="0.1em" textTransform="uppercase" lineHeight="1.2"
          whiteSpace="nowrap" overflow="hidden" textOverflow="ellipsis"
        >
          {subtitle}
        </Text>
      </VStack>
    </HStack>
  );
}

function VDiv() {
  return <Box w="1px" h={SZ.divider} bg={P.ink200} alignSelf="center" flexShrink={0} aria-hidden="true" />;
}

/* ── Metric ──────────────────────────────────────────────────────────── */
function Metric({ value, label, tone = "ink" }) {
  const prevRef = useRef(value);
  const [flash, setFlash] = useState(false);

  useEffect(() => {
    if (prevRef.current === value) return;
    setFlash(true);
    const t = setTimeout(() => setFlash(false), 600);
    prevRef.current = value;
    return () => clearTimeout(t);
  }, [value]);

  const color =
    tone === "orange" ? P.orange : tone === "muted" ? P.ink400 : P.black;

  return (
    <VStack gap="1px" minW={SZ.metricMinW} align="flex-start">
      <Text
        fontSize={SZ.metricValue} fontWeight="700" lineHeight="1.1"
        color={color} fontFamily={MONO} letterSpacing="-0.02em"
        px="3px" mx="-3px" borderRadius="2px"
        style={{ animation: flash ? "astra-flash 600ms ease-out" : "none" }}
      >
        {value}
      </Text>
      <Text
        fontSize={SZ.metricLabel} color={P.ink500} fontFamily={MONO}
        letterSpacing="0.08em" textTransform="uppercase"
        whiteSpace="nowrap" lineHeight="1.2"
      >
        {label}
      </Text>
    </VStack>
  );
}

/* ── Connection pill ─────────────────────────────────────────────────── */
function ConnectionPill({ connected }) {
  const t = connected
    ? { dot: P.blue, bg: P.blue50, bd: P.blue100, fg: P.blue700, pulse: "astra-pulse", copy: "SYS · ONLINE", aria: "System online" }
    : { dot: P.orange, bg: P.orange50, bd: P.orange100, fg: P.orange700, pulse: "astra-pulse-warn", copy: "SYS · OFFLINE", aria: "System offline" };

  return (
    <HStack
      gap={SP.sm} px={SP.md} h={SZ.pill} borderRadius={RADII.md}
      bg={t.bg} border="1px solid" borderColor={t.bd} flexShrink={0}
      role="status" aria-label={t.aria}
    >
      <Box
        w="6px" h="6px" borderRadius="full" bg={t.dot}
        style={{ animation: `${t.pulse} 2s infinite` }} aria-hidden="true"
      />
      <Text fontSize={FS.micro} color={t.fg} fontWeight="700" fontFamily={MONO} letterSpacing="0.06em" lineHeight="1">
        {t.copy}
      </Text>
    </HStack>
  );
}

/* ── Station grid ────────────────────────────────────────────────────── */
function StationGrid({ summary, heartbeats, connected, selectedKey, onSelect }) {
  if (summary.length === 0) {
    return (
      <Box mb={SP.lg}>
        <EmptyStationState connected={connected} />
      </Box>
    );
  }

  return (
    <Box mb={SP.lg}>
      <LineGroups
        stations={summary}
        heartbeats={heartbeats}
        selectedKey={selectedKey}
        onSelect={(station) => onSelect(stKey(station))}
      />
    </Box>
  );
}

function EmptyStationState({ connected }) {
  const t = connected
    ? { dot: P.blue, pulse: "astra-pulse", text: "Fetching station data…", chip: "Syncing", bg: P.blue50, fg: P.blue700, bd: P.blue100 }
    : { dot: P.orange, pulse: "astra-pulse-warn", text: "Awaiting backend connection…", chip: "Offline", bg: P.orange50, fg: P.orange700, bd: P.orange100 };

  return (
    <Box
      gridColumn="1 / -1" py={16} textAlign="center" bg={P.white}
      borderRadius={RADII.lg} border="1px dashed" borderColor={P.ink300}
      style={{ animation: "astra-slide 400ms ease" }}
    >
      <VStack gap={SP.md}>
        <Box w="8px" h="8px" borderRadius="full" bg={t.dot} style={{ animation: `${t.pulse} 2s infinite` }} aria-hidden="true" />
        <Text fontSize={FS.cap} color={P.ink500} fontFamily={MONO} letterSpacing="0.05em">{t.text}</Text>
        <Box
          as="span" display="inline-flex" alignItems="center" h="18px" px={SP.md}
          fontSize={FS.micro} fontWeight="700" fontFamily={MONO}
          textTransform="uppercase" letterSpacing="0.07em" lineHeight="1"
          borderRadius={RADII.xs} bg={t.bg} color={t.fg} border="1px solid" borderColor={t.bd}
        >
          {t.chip}
        </Box>
      </VStack>
    </Box>
  );
}

/* ── Selected station detail ─────────────────────────────────────────── */
function SelectedStationDetail({
  station, heartbeat, connected, dbOk,
  fetchHistory, fetchHourly, fetchStats, exportCSV, recentCycles,
}) {
  return (
    <Box>
      <Box
        mb={SP.lg} p={SP.lg} bg={P.white}
        borderWidth="1px" borderColor={P.ink200}
        borderLeftWidth="3px" borderLeftColor={P.blue}
        borderRadius={RADII.lg} boxShadow="0 1px 3px rgba(35,31,32,.02)"
      >
        <HStack mb={SP.md} gap={SP.sm}>
          <Box w="6px" h="6px" bg={P.orange} flexShrink={0} aria-hidden="true" />
          <Text
            fontSize={FS.micro} fontWeight="700" color={P.ink500} fontFamily={MONO}
            letterSpacing="0.09em" textTransform="uppercase" lineHeight="1.2"
          >
            Connectivity Diagnostics
          </Text>
        </HStack>
        <StationConnectivity heartbeat={heartbeat} connected={connected} dbOk={dbOk} />
      </Box>

      <StationTimeline station={station} heartbeat={heartbeat} />

      <Box mt={SP.lg}>
        <StationDetail
          station={station}
          fetchHistory={fetchHistory}
          fetchHourly={fetchHourly}
          fetchStats={fetchStats}
          exportCSV={exportCSV}
          recentCycles={recentCycles}
        />
      </Box>
    </Box>
  );
}

/* ── Station register page ───────────────────────────────────────────── */
function StationRegisterPage({ onBack }) {
  return (
    <Box minH="100vh" bg={P.canvas} fontFamily={SANS}>
      <style>{KEYFRAMES}</style>
      <Flex
        h={SZ.header} px={PX} bg={P.white}
        borderBottomWidth="1px" borderColor={P.ink200}
        align="center" justify="space-between"
        boxShadow="0 1px 2px rgba(35,31,32,.02)"
      >
        <BrandBlock subtitle="Station Register" />
        <Button
          variant="outline" onClick={onBack}
          h={SZ.action} px={SP.lg}
          fontSize={FS.tiny} fontWeight="700" fontFamily={MONO}
          letterSpacing="0.08em" textTransform="uppercase"
          borderRadius={RADII.md} borderColor={P.ink200} color={P.ink600} bg={P.white}
          _hover={{ bg: P.blue50, borderColor: P.blue200, color: P.blue }}
          _focusVisible={FOCUS_BLUE}
        >
          Back to Dashboard
        </Button>
      </Flex>
      <Box maxW="1800px" mx="auto" p={PX} style={{ animation: "astra-slide 400ms ease" }}>
        <StationRegister onBack={onBack} />
      </Box>
    </Box>
  );
}
