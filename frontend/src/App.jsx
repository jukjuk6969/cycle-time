/**
 * App.jsx — CT Monitor dashboard shell.
 * Palette: Astra blue #005A9C · orange #F7941D · ink #231F20
 */
import React, { useState } from "react";
import {
  ChakraProvider, Box, Flex, Text, Button, Image, HStack, VStack,
} from "@chakra-ui/react";

import { system } from "./theme";
import StationConnectivity, { useDbHealth } from "./components/StationConnectivity";
import { useSocket } from "./hooks/useSocket";
import StationDetail from "./components/StationDetail";
import AnomalyList from "./components/AnomalyList";
import StationTimeline from "./components/StationTimeline";
import StationRegister from "./components/StationRegister";
import logoGif from "./assets/logo.png";
import { AuthProvider } from "./hooks/useAuth";
import { ThemeModeProvider } from "./hooks/useThemeMode";
import ThemeToggle from "./components/ThemeToggle";
import AuthGate from "./components/AuthGate";
import UserMenu from "./components/UserMenu";
import LineGroups from "./components/LineGroups";
import StationDrawer, { DRAWER_W, usePushLayout } from "./components/StationDrawer";

/* ── Palette ─────────────────────────────────────────────────────────── */
const P = {
  blue: "var(--ct-blue)", blueActive: "var(--ct-blue-active)",
  blue700: "var(--ct-blue-700)", blue200: "var(--ct-blue-200)", blue100: "var(--ct-blue-100)", blue50: "var(--ct-blue-50)",
  orange: "var(--ct-orange)", orange700: "var(--ct-orange-700)", orange100: "var(--ct-orange-100)", orange50: "var(--ct-orange-50)",
  black: "var(--ct-black)",
  ink600: "var(--ct-ink-600)", ink500: "var(--ct-ink-500)", ink300: "var(--ct-ink-300)", ink200: "var(--ct-ink-200)",
  white: "var(--ct-surface)", canvas: "var(--ct-canvas)",
};

/* ── Type / space / size ─────────────────────────────────────────────── */
const SANS = `"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
const MONO = `"JetBrains Mono", "SF Mono", ui-monospace, "Cascadia Code", Consolas, monospace`;

const FS = { micro: "10px", tiny: "11px", cap: "12px", med: "15px" };
const SP = { sm: "5px", md: "8px", lg: "13px", xl: "21px" };
const RADII = { xs: "2px", md: "4px", lg: "6px" };
const SZ = { header: "72px", action: "30px", pill: "26px", logo: "32px" };
const PX = { base: SP.lg, md: SP.xl };
const PY = { base: SP.lg, md: SP.xl };
const FOCUS_BLUE = { boxShadow: `0 0 0 3px ${P.blue100}` };

/* ── Keyframes ───────────────────────────────────────────────────────── */
const KEYFRAMES = `
@keyframes astra-pulse      { 0%{box-shadow:0 0 0 0 rgba(var(--ct-blue-rgb),.35)} 70%{box-shadow:0 0 0 5px rgba(var(--ct-blue-rgb),0)} 100%{box-shadow:0 0 0 0 rgba(var(--ct-blue-rgb),0)} }
@keyframes astra-pulse-warn { 0%{box-shadow:0 0 0 0 rgba(var(--ct-orange-rgb),.4)} 70%{box-shadow:0 0 0 5px rgba(var(--ct-orange-rgb),0)} 100%{box-shadow:0 0 0 0 rgba(var(--ct-orange-rgb),0)} }
@keyframes astra-slide      { from{opacity:0;transform:translateY(-6px)} to{opacity:1;transform:translateY(0)} }
@keyframes astra-fade       { from{opacity:0} to{opacity:1} }
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.001ms!important;animation-iteration-count:1!important;transition-duration:.001ms!important}}
`;

const stKey = (s) => `${s.line}-${s.station_id}`;

/* ── App ─────────────────────────────────────────────────────────────── */
export default function App() {
  return (
    <ThemeModeProvider>
      <AuthProvider>
        <ChakraProvider value={system}>
          <AuthGate>
            <Dashboard />
          </AuthGate>
        </ChakraProvider>
      </AuthProvider>
    </ThemeModeProvider>
  );
}

function Dashboard() {
  const {
    summary, heartbeats, recentCycles, connected,
    fetchHistory, fetchHourly, fetchStats, exportCSV,
  } = useSocket();

  const [page, setPage] = useState("dashboard");
  const [selectedKey, setSelectedKey] = useState(null);
  const dbOk = useDbHealth(connected);
  const push = usePushLayout();

  const selected = summary.find((s) => stKey(s) === selectedKey) || null;

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
            "linear-gradient(to right, var(--ct-grid) 1px, transparent 1px), linear-gradient(to bottom, var(--ct-grid) 1px, transparent 1px)",
          backgroundSize: "32px 32px",
          animation: "astra-fade 900ms ease-out",
        },
      }}
    >
      <style>{KEYFRAMES}</style>
      <Header connected={connected} onOpenRegister={() => setPage("station-register")} />

      <Box maxW="1800px" mx="auto" px={PX} py={PY} position="relative" zIndex={1}>
        <StationGrid
          summary={summary}
          heartbeats={heartbeats}
          connected={connected}
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
function Header({ connected, onOpenRegister }) {
  return (
    <Flex
      h={{ base: "auto", md: SZ.header }} minH={{ md: SZ.header }}
      px={PX} py={{ base: SP.lg, md: 0 }}
      bg={P.white} borderBottomWidth="1px" borderColor={P.ink200}
      align={{ base: "stretch", md: "center" }} justify="space-between"
      gap={SP.md} direction={{ base: "column", md: "row" }}
      boxShadow="0 1px 2px rgba(0,0,0,.04)"
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
        <ConnectionPill connected={connected} />
        <ThemeToggle size={SZ.action} />
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
function StationGrid({ summary, heartbeats, connected, onSelect }) {
  return (
    <Box mb={SP.lg}>
      {summary.length === 0 ? (
        <EmptyStationState connected={connected} />
      ) : (
        <LineGroups
          stations={summary}
          heartbeats={heartbeats}
          onSelect={(station) => onSelect(stKey(station))}
        />
      )}
    </Box>
  );
}

function EmptyStationState({ connected }) {
  const t = connected
    ? { dot: P.blue, pulse: "astra-pulse", text: "Fetching station data…", chip: "Syncing", bg: P.blue50, fg: P.blue700, bd: P.blue100 }
    : { dot: P.orange, pulse: "astra-pulse-warn", text: "Awaiting backend connection…", chip: "Offline", bg: P.orange50, fg: P.orange700, bd: P.orange100 };

  return (
    <Box
      py={16} textAlign="center" bg={P.white}
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
        borderRadius={RADII.lg} boxShadow="0 1px 3px rgba(0,0,0,.04)"
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
        boxShadow="0 1px 2px rgba(0,0,0,.04)"
      >
        <BrandBlock subtitle="Station Register" />
        <HStack gap={SP.md}>
          <ThemeToggle size={SZ.action} />
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
        </HStack>
      </Flex>
      <Box maxW="1800px" mx="auto" p={PX} style={{ animation: "astra-slide 400ms ease" }}>
        <StationRegister onBack={onBack} />
      </Box>
    </Box>
  );
}