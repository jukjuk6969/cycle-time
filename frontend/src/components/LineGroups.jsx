import React, { useMemo, useState } from "react";
import { Box, Flex, Text, SimpleGrid } from "@chakra-ui/react";
import StationCard from "./StationCard";

const MONO = `"JetBrains Mono", "SF Mono", ui-monospace, Consolas, monospace`;
const STALE_MS = 5000;
const PROBLEM = ["TIMEOUT", "FAILED", "CAMERA_OFFLINE"];

// Sesuaikan dengan bentuk state heartbeat di aplikasi kamu.
const hbKey = (s) => `${s.line}-${s.station_id}`;

// Status live satu station (logika sama dengan StationCard).
function liveStateOf(station, hb) {
  if (station?.saving === true) return "SAVING_MODE";
  const age = hb?.received ? Date.now() - hb.received : Infinity;
  if (age > STALE_MS) return "OFFLINE";
  return hb?.state || "IDLE";
}

// Kelompokkan station per line, urut numerik, station diurut per station_id.
export function groupByLine(stations = []) {
  const map = new Map();
  stations.forEach((s) => {
    if (!map.has(s.line)) map.set(s.line, []);
    map.get(s.line).push(s);
  });
  return [...map.entries()]
    .sort(([a], [b]) => Number(a) - Number(b))
    .map(([line, items]) => ({
      line,
      stations: items.sort((a, b) => Number(a.station_id) - Number(b.station_id)),
    }));
}

function LineHeader({ line, summary, open, onToggle, panelId }) {
  const { total, problem, offline, running } = summary;
  const tone = problem > 0 ? "var(--ct-red)" : offline === total ? "var(--ct-ink-400)" : "var(--ct-blue)";

  return (
    <Flex
      as="button"
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      aria-controls={panelId}
      w="100%"
      align="center"
      justify="space-between"
      gap={3}
      px={4}
      py={3}
      bg="var(--ct-surface)"
      borderWidth="1px"
      borderColor="var(--ct-ink-200)"
      borderLeftWidth="4px"
      borderLeftColor={tone}
      borderRadius="4px"
      textAlign="left"
      cursor="pointer"
      _hover={{ bg: "var(--ct-ink-50)" }}
      _focusVisible={{ outline: "2px solid var(--ct-blue)", outlineOffset: "2px" }}
    >
      <Flex align="baseline" gap={3} minW={0}>
        <Text fontSize="15px" fontWeight="700" color="var(--ct-black)">
          Line {line}
        </Text>
        <Text fontSize="12px" color="var(--ct-ink-500)">
          {total} station
        </Text>
      </Flex>

      <Flex align="center" gap={4} fontFamily={MONO} fontSize="11px" fontWeight="600">
        {problem > 0 && <Text color="var(--ct-red)">{problem} bermasalah</Text>}
        {offline > 0 && <Text color="var(--ct-ink-400)">{offline} offline</Text>}
        <Text color="var(--ct-blue)">
          {running}/{total} aktif
        </Text>
        <Text color="var(--ct-ink-500)" aria-hidden="true">
          {open ? "▾" : "▸"}
        </Text>
      </Flex>
    </Flex>
  );
}

function LineGroup({ line, stations, heartbeats, selectedKey, onSelect, open, onToggle }) {
  const states = stations.map((s) => liveStateOf(s, heartbeats[hbKey(s)]));
  const summary = {
    total: stations.length,
    problem: states.filter((x) => PROBLEM.includes(x)).length,
    offline: states.filter((x) => x === "OFFLINE").length,
    running: states.filter((x) => x === "RUNNING" || x === "TRANSIT").length,
  };
  const panelId = `line-panel-${line}`;

  return (
    <Box as="section" aria-label={`Line ${line}`}>
      <LineHeader line={line} summary={summary} open={open} onToggle={onToggle} panelId={panelId} />
      {open && (
        <SimpleGrid
        id={panelId}
        templateColumns="repeat(3, minmax(0, 1fr))"
        gap={3}
        mt={3}
      >
          {stations.map((s) => (
            <StationCard
              key={hbKey(s)}
              station={s}
              heartbeat={heartbeats[hbKey(s)]}
              selected={selectedKey === hbKey(s)}
              onClick={() => onSelect?.(s)}
            />
          ))}
        </SimpleGrid>
      )}
    </Box>
  );
}

/**
 * Pengganti daftar flat StationCard.
 * props:
 *  - stations:   array station (punya .line dan .station_id)
 *  - heartbeats: objek { "line-station_id": heartbeat }
 *  - selectedKey / onSelect: seleksi station seperti sebelumnya
 */
export default function LineGroups({ stations, heartbeats = {}, selectedKey, onSelect }) {
  const groups = useMemo(() => groupByLine(stations), [stations]);

  // Line yang diciutkan; default semua terbuka.
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem("collapsedLines") || "[]"));
    } catch {
      return new Set();
    }
  });

  const toggle = (line) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      next.has(line) ? next.delete(line) : next.add(line);
      try {
        localStorage.setItem("collapsedLines", JSON.stringify([...next]));
      } catch {
        /* abaikan */
      }
      return next;
    });

  if (groups.length === 0) {
    return (
      <Text fontSize="13px" color="var(--ct-ink-500)">
        Belum ada station terdaftar.
      </Text>
    );
  }

  return (
    <Flex direction="column" gap={6}>
      {groups.map((g) => (
        <LineGroup
          key={g.line}
          line={g.line}
          stations={g.stations}
          heartbeats={heartbeats}
          selectedKey={selectedKey}
          onSelect={onSelect}
          open={!collapsed.has(g.line)}
          onToggle={() => toggle(g.line)}
        />
      ))}
    </Flex>
  );
}