/**
 * StationDetail.jsx
 * Single station detail panel: settings (takt, ROI timeout), filters,
 * stats summary, cycle-time chart, and cycle history.
 */

import React, { useEffect, useState, useCallback, useRef } from "react";

import axios from "axios";
import {
  Box, Flex, Grid, Text, Button, Input, SimpleGrid, HStack, Badge,
  Table, Spinner, NativeSelect,
} from "@chakra-ui/react";
import { ANOMALY_STYLE, getAnomalyType, expectedList, getRowUi, isAbort } from "../hooks/useSocket";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ResponsiveContainer,
} from "recharts";

import { BASE_URL } from "../config";

/*
 * Golden-ratio scale (φ ≈ 1.618), consistent with AnomalyList.
 */
const SP = { xs: "3px", sm: "5px", md: "8px", lg: "13px", xl: "21px" };
const FS = { cap: "11px", body: "13px", title: "17px", stat: "18px" };
const ROW_H = "34px";
const BTN_H = "21px";
const PANEL_H = "233px";

const MONO = `"JetBrains Mono", "SF Mono", ui-monospace, Consolas, monospace`;
const SANS = `"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;

// Astra Visteon palette
const C = {
  brand: "#005A9C",
  anomaly: "#DC2626",
  grid: "#E2E8F0",
  takt: "#94A3B8",
  axis: "#64748B",
  skipped: "#F58025",
  ink: "#0F172A",
  muted: "#64748B",
  faint: "#94A3B8",
  surface: "#FAFBFC",
  rule: "#E2E8F0",
};

const num = { fontVariantNumeric: "tabular-nums", fontFamily: MONO };

/* =========================================================
   WIB TIME HELPERS
   ========================================================= */

const WIB = 7 * 3600 * 1000;

const todayWIB = () => new Date(Date.now() + WIB).toISOString().slice(0, 10);

const daysAgoWIB = (n) =>
  new Date(Date.now() + WIB - n * 86400000).toISOString().slice(0, 10);

// WIB date → API params. `to` is exclusive, so add one day.
const toApiRange = ({ shift, from, to }) => {
  const o = {};
  if (shift) o.shift = shift;
  if (from) o.from = new Date(`${from}T00:00:00+07:00`).toISOString();
  if (to) {
    o.to = new Date(
      new Date(`${to}T00:00:00+07:00`).getTime() + 86400000
    ).toISOString();
  }
  return o;
};

function fmt(sec) {
  return sec == null ? "—" : `${Number(sec).toFixed(2)}s`;
}

function tsToTime(ts) {
  if (!ts) return "—";
  try {
    const s = String(ts);
    const normalized = /(Z|[+-]\d{2}:?\d{2})$/.test(s) ? s : s.replace(" ", "T") + "Z";
    const d = new Date(normalized);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleTimeString("en-GB", {
      timeZone: "Asia/Jakarta",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return "—";
  }
}

/* =========================================================
   HOOK: inline numeric edit (Takt, ROI timeout, Min CT, Short limit)
   ========================================================= */

function useInlineEdit({ current, label, save, allowZero = false }) {
  const [editing, setEditing] = useState(false);
  const [input, setInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const start = () => {
    setInput(current != null ? Number(current).toString() : "");
    setError("");
    setSuccess("");
    setEditing(true);
  };

  const cancel = () => {
    if (saving) return;
    setEditing(false);
    setInput("");
    setError("");
  };

  const reset = useCallback(() => {
    setEditing(false);
    setInput("");
    setSaving(false);
    setError("");
    setSuccess("");
  }, []);

  const submit = async () => {
    setError("");
    setSuccess("");

    const value = Number(input);

    if (!input.trim()) {
      setError(`${label} is required.`);
      return;
    }
    if (!Number.isFinite(value) || value < 0 || (!allowZero && value === 0)) {
      setError(
        allowZero
          ? `${label} must be 0 or greater (0 = disabled).`
          : `${label} must be a number greater than 0.`
      );
      return;
    }

    setSaving(true);
    try {
      await save(value);
      setSuccess(`${label} updated.`);
      setEditing(false);
      setInput("");
    } catch (err) {
      console.error(`Failed to update ${label}:`, err);
      setError(
        err.response?.data?.error ||
        err.response?.data?.message ||
        err.message ||
        `Failed to update ${label.toLowerCase()}.`
      );
    } finally {
      setSaving(false);
    }
  };

  // Auto-dismiss success message
  useEffect(() => {
    if (!success) return undefined;
    const t = setTimeout(() => setSuccess(""), 4000);
    return () => clearTimeout(t);
  }, [success]);

  return { editing, input, setInput, saving, error, success, start, cancel, submit, reset };
}

/* =========================================================
   SHARED STYLES
   ========================================================= */

const smallBtn = {
  h: BTN_H,
  minW: 0,
  px: SP.md,
  fontSize: "10px",
  fontWeight: "700",
  fontFamily: MONO,
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  borderRadius: "4px",
};

const sectionLabel = {
  fontSize: "9px",
  fontWeight: "700",
  color: C.muted,
  fontFamily: MONO,
  textTransform: "uppercase",
  letterSpacing: "0.08em",
};

const rule = { borderColor: C.rule };

/* =========================================================
   SMALL COMPONENTS
   ========================================================= */

function InlineSetting({ label, valueText, edit, extra }) {
  return (
    <Box minW="0">
      <Text {...sectionLabel} mb={SP.sm}>
        {label}
      </Text>

      <HStack gap={SP.md} wrap="wrap" align="center">
        {!edit.editing ? (
          <>
            <Text fontSize={FS.body} fontWeight="700" color={C.ink} style={num} lineHeight="1.2">
              {valueText}
            </Text>
            <Button {...smallBtn} variant="subtle" colorPalette="blue" onClick={edit.start}>
              Edit
            </Button>
            {extra}
          </>
        ) : (
          <>
            <Input
              type="number"
              min="0.01"
              step="0.01"
              autoFocus
              aria-label={label}
              value={edit.input}
              onChange={(e) => edit.setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") edit.submit();
                if (e.key === "Escape") edit.cancel();
              }}
              disabled={edit.saving}
              w="81px"
              h={BTN_H}
              px={SP.md}
              fontSize={FS.body}
              fontFamily={MONO}
              style={num}
              borderColor={edit.error ? "red.400" : C.brand}
              borderRadius="4px"
              _focus={{ boxShadow: `0 0 0 1px ${C.brand}`, borderColor: C.brand }}
            />
            <Text fontSize={FS.cap} color={C.muted} fontFamily={MONO}>s</Text>
            <Button
              {...smallBtn}
              colorPalette="green"
              onClick={edit.submit}
              disabled={edit.saving}
            >
              {edit.saving ? <Spinner size="xs" /> : "Save"}
            </Button>
            <Button {...smallBtn} variant="outline" onClick={edit.cancel} disabled={edit.saving}>
              Cancel
            </Button>
          </>
        )}
      </HStack>
    </Box>
  );
}

function FilterBar({ filters, onChange }) {
  const isToday =
    !filters.shift && filters.from === todayWIB() && filters.to === todayWIB();
  const is7Days =
    !filters.shift && filters.from === daysAgoWIB(6) && filters.to === todayWIB();

  const dateProps = {
    type: "date",
    h: BTN_H,
    w: "auto",
    px: SP.md,
    fontSize: "11px",
    fontFamily: MONO,
    borderRadius: "4px",
    borderColor: C.rule,
  };

  return (
    <Flex gap={SP.md} wrap="wrap" align="center">
      <NativeSelect.Root w="189px" size="xs">
        <NativeSelect.Field
          aria-label="Shift filter"
          value={filters.shift || ""}
          onChange={(e) => onChange({ ...filters, shift: e.target.value, from: "", to: "" })}
          h={BTN_H}
          fontSize="11px"
          fontFamily={MONO}
          py={0}
          borderRadius="4px"
          borderColor={C.rule}
        >
          <option value="">All shifts</option>
          <option value="1">Shift 1 (19.00–07.00)</option>
          <option value="2">Shift 2 (07.00–19.00)</option>
        </NativeSelect.Field>
        <NativeSelect.Indicator />
      </NativeSelect.Root>

      <Input
        {...dateProps}
        aria-label="From date"
        value={filters.from || ""}
        max={filters.to || todayWIB()}
        onChange={(e) => onChange({ ...filters, from: e.target.value, shift: "" })}
      />

      <Text fontSize="10px" color={C.faint} fontFamily={MONO}>to</Text>

      <Input
        {...dateProps}
        aria-label="To date"
        value={filters.to || ""}
        min={filters.from || ""}
        max={todayWIB()}
        onChange={(e) => onChange({ ...filters, to: e.target.value, shift: "" })}
      />

      <HStack gap={SP.xs}>
        <Button
          {...smallBtn}
          variant={isToday ? "solid" : "outline"}
          colorPalette={isToday ? "blue" : "gray"}
          aria-pressed={isToday}
          onClick={() => onChange({ shift: "", from: todayWIB(), to: todayWIB() })}
        >
          Today
        </Button>

        <Button
          {...smallBtn}
          variant={is7Days ? "solid" : "outline"}
          colorPalette={is7Days ? "blue" : "gray"}
          aria-pressed={is7Days}
          onClick={() => onChange({ shift: "", from: daysAgoWIB(6), to: todayWIB() })}
        >
          7 days
        </Button>

        <Button
          {...smallBtn}
          variant="ghost"
          color={C.muted}
          onClick={() => onChange({ shift: "", from: "", to: "" })}
        >
          Reset
        </Button>
      </HStack>
    </Flex>
  );
}

/* Stat cell: hairline-separated grid, not standalone cards */
function StatCell({ label, value, sub, color = C.ink }) {
  return (
    <Box
      px={SP.lg}
      py={SP.md}
      borderRightWidth="1px"
      borderBottomWidth="1px"
      {...rule}
      mr="-1px"
      mb="-1px"
      minW="0"
    >
      <Text
        fontSize="9px"
        color={C.muted}
        lineHeight="13px"
        mb={SP.xs}
        fontFamily={MONO}
        textTransform="uppercase"
        letterSpacing="0.08em"
        whiteSpace="nowrap"
        overflow="hidden"
        textOverflow="ellipsis"
      >
        {label}
      </Text>
      <Text
        fontSize={FS.stat}
        fontWeight="700"
        color={color}
        lineHeight="21px"
        style={num}
        whiteSpace="nowrap"
      >
        {value}
      </Text>
      {sub && (
        <Text fontSize="9px" color={C.faint} lineHeight="13px" mt={SP.xs} fontFamily={MONO}>
          {sub}
        </Text>
      )}
    </Box>
  );
}

function Panel({ title, meta, children }) {
  return (
    <Box minW="0">
      <Flex align="center" justify="space-between" mb={SP.md} gap={SP.md}>
        <HStack gap={SP.md} align="center" minW="0">
          <Box w="3px" h="12px" bg={C.brand} borderRadius="1px" flexShrink={0} />
          <Text
            fontSize="11px"
            fontWeight="700"
            color={C.ink}
            fontFamily={MONO}
            textTransform="uppercase"
            letterSpacing="0.08em"
            whiteSpace="nowrap"
            overflow="hidden"
            textOverflow="ellipsis"
          >
            {title}
          </Text>
        </HStack>
        {meta && (
          <Text fontSize="10px" color={C.muted} fontFamily={MONO} whiteSpace="nowrap">
            {meta}
          </Text>
        )}
      </Flex>
      {children}
    </Box>
  );
}

function EmptyNote({ children }) {
  return (
    <Flex h="100%" align="center" justify="center" px={SP.xl}>
      <Text fontSize="12px" color={C.muted} textAlign="center" fontFamily={MONO}>
        {children}
      </Text>
    </Flex>
  );
}

/* =========================================================
   MAIN COMPONENT
   ========================================================= */

export default function StationDetail({
  station,
  fetchHistory,
  fetchHourly,
  fetchStats,
  exportCSV,
  recentCycles = [],
}) {
  const [history, setHistory] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  const line = station?.line;
  const stationId = station?.station_id;
  const stationKey = `${line}-${stationId}`;

  const lastSeenRef = useRef(null);
  const historyRef = useRef([]);
  historyRef.current = history;

  const reqId = useRef(0);
  const acRef = useRef(null);
  const [filters, setFilters] = useState({
    shift: "",
    from: todayWIB(),
    to: todayWIB(),
  });

  /* ---------- station settings ---------- */

  const takt = station?.takt_time;
  const targetCT = station?.target_cycle_time || takt;

  const [roiLocal, setRoiLocal] = useState(null);
  const roiTimeout = roiLocal ?? station?.roi_timeout_sec;

  const taktEdit = useInlineEdit({
    current: takt,
    label: "Takt time",
    save: (value) =>
      axios.put(`${BASE_URL}/api/station/${line}/${stationId}/takt-time`, {
        takt_time: value,
      }),
  });

  const roiEdit = useInlineEdit({
    current: roiTimeout,
    label: "ROI timeout",
    save: async (value) => {
      await axios.put(`${BASE_URL}/api/station/${line}/${stationId}/roi-timeout`, {
        roi_timeout_sec: value,
      });
      setRoiLocal(value);
    },
  });

  const [minLocal, setMinLocal] = useState(null);
  const minCycle = minLocal ?? station?.min_cycle_sec;

  const minEdit = useInlineEdit({
    current: minCycle,
    label: "Min CT",
    save: async (value) => {
      await axios.put(`${BASE_URL}/api/station/${line}/${stationId}/min-cycle`, {
        min_cycle_sec: value,
      });
      setMinLocal(value);
    },
  });

  const [shortLocal, setShortLocal] = useState(null);
  const shortLimit = shortLocal ?? station?.short_anomaly_sec;

  const shortEdit = useInlineEdit({
    current: shortLimit,
    label: "Short limit",
    allowZero: true,
    save: async (value) => {
      await axios.put(`${BASE_URL}/api/station/${line}/${stationId}/short-anomaly`, {
        short_anomaly_sec: value,
      });
      setShortLocal(value);
    },
  });

  useEffect(() => {
    setRoiLocal(null);
    setMinLocal(null);
    taktEdit.reset();
    roiEdit.reset();
    minEdit.reset();
    // eslint-disable-next-line
  }, [stationKey]);

  /* ---------- God Mode / Saving Mode ---------- */
  const savingMode = station?.saving === true;
  const [modeBusy, setModeBusy] = useState(false);
  const [waitingGod, setWaitingGod] = useState(false);
  const [modeError, setModeError] = useState("");

  const setMode = async (toSaving) => {
    if (toSaving && !window.confirm(`Enable Saving Mode for L${line} ST${stationId}?\nHeartbeat will stop and live stream will be disconnected.`)) return;

    setModeBusy(true);
    setModeError("");
    try {
      await axios.put(`${BASE_URL}/api/station/${line}/${stationId}/saving-mode`, { enabled: toSaving });
      if (!toSaving) setWaitingGod(true);
    } catch (err) {
      setModeError(err.response?.data?.error || err.message || "Failed to change mode.");
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
    setModeBusy(false);
    setWaitingGod(false);
    setModeError("");
  }, [stationKey]);

  useEffect(() => { setMinLocal(null); }, [station?.min_cycle_sec]);
  useEffect(() => { setShortLocal(null); }, [station?.short_anomaly_sec]);
  useEffect(() => { setRoiLocal(null); }, [station?.roi_timeout_sec]);

  /* ---------- load data ---------- */
const loadAll = useCallback(
  async (flt) => {
    if (stationId == null) return;
    const id = ++reqId.current;

    acRef.current?.abort();                 // batalkan request sebelumnya
    const ac = new AbortController();
    acRef.current = ac;

    setLoading(true);
    try {
      const q = toApiRange(flt);
      const [hist, st] = await Promise.all([
        fetchHistory(line, stationId, 100, q, { signal: ac.signal }),
        fetchStats(line, stationId, q, { signal: ac.signal }),
      ]);
      if (id !== reqId.current) return;
      setHistory(hist.slice(-100));
      setStats(st);
    } catch (e) {
      if (isAbort(e) || id !== reqId.current) return;
      console.error("Failed to load station data:", e);
      setHistory([]);
      setStats(null);
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  },
  [line, stationId, fetchHistory, fetchStats]
);

  useEffect(() => {
    loadAll(filters);
    // eslint-disable-next-line
  }, [stationKey]);

  const handleFilter = (newFilters) => {
    setFilters(newFilters);
    loadAll(newFilters);
  };

  /* ---------- realtime cycles ---------- */
  useEffect(() => {
    const l = recentCycles.find((c) => c.line === line && c.station_id === stationId);
    lastSeenRef.current = l?.end_timestamp ?? null;
    // eslint-disable-next-line
  }, [stationKey]);

  useEffect(() => {
    const latest = recentCycles.find((c) => c.line === line && c.station_id === stationId);
    if (!latest) return;
    if (latest.end_timestamp === lastSeenRef.current) return;
    lastSeenRef.current = latest.end_timestamp;

    const iso = new Date(latest.end_timestamp * 1000).toISOString();
    if (historyRef.current.some((h) => h.end_timestamp === iso)) return;

    const ct = latest.cycle_time_sec;
    const aType = getAnomalyType(latest);
    const skipped = latest.skipped_expected || null;
    const isAnom = aType !== "OK" || skipped ? 1 : 0;

    setHistory((prev) => [
      ...prev.slice(-99),
      {
        cycle_count: latest.cycle_count,
        cycle_time_sec: ct,
        end_timestamp: iso,
        is_anomaly: isAnom,
        anomaly_type: aType,
        operator_id: latest.operator_id,
        skipped_expected: skipped,
      },
    ]);

    setStats((prev) => {
      if (!prev) return prev;
      const total = (prev.total_cycles || 0) + 1;
      const over = (prev.over_takt_count || 0) + isAnom;
      return {
        ...prev,
        total_cycles: total,
        over_takt_count: over,
        short_count: (prev.short_count || 0) + (aType === "SHORT" ? 1 : 0),
        long_count: (prev.long_count || 0) + (aType === "LONG" ? 1 : 0),
        expected_count: (prev.expected_count || 0) + (skipped ? 1 : 0),
        efficiency_pct: Math.round(((total - over) / total) * 100),
        avg_ct: prev.avg_ct != null ? (prev.avg_ct * (total - 1) + ct) / total : ct,
        min_ct: prev.min_ct != null ? Math.min(prev.min_ct, ct) : ct,
        max_ct: prev.max_ct != null ? Math.max(prev.max_ct, ct) : ct,
      };
    });
  }, [recentCycles, line, stationId, targetCT]);

  if (!station) return null;

  /* ---------- derived data ---------- */
  const chartData = history.map((h, i) => ({
    idx: i + 1,
    ct: Number(h.cycle_time_sec?.toFixed(2)),
    time: tsToTime(h.end_timestamp),
    type: getAnomalyType(h),
    anomaly: h.is_anomaly ? Number(h.cycle_time_sec?.toFixed(2)) : null,
  }));

  const rows = [...history].reverse();

  const refs = [takt, targetCT, Number(shortLimit) > 0 ? shortLimit : null]
    .filter((n) => n != null && Number.isFinite(Number(n)))
    .map(Number);
  const yDomain = [
    (min) => Math.max(0, Math.floor(Math.min(min, ...refs) * 0.85)),
    (max) => Math.ceil(Math.max(max, ...refs) * 1.1),
  ];
  const sameTarget = targetCT != null && Number(targetCT) === Number(takt);

  const eff = stats?.efficiency_pct;
  const effColor = eff == null ? C.ink : eff >= 90 ? C.brand : eff >= 70 ? C.skipped : C.anomaly;

  const longCount = stats?.long_count ?? stats?.over_takt_count ?? 0;
  const shortCount = stats?.short_count || 0;
  const skippedCount = stats?.expected_count || 0;

  const statItems = stats ? [
    { label: "Total cycles", value: stats.total_cycles || 0 },
    { label: "Avg CT", value: fmt(stats.avg_ct) },
    { label: "Min CT", value: fmt(stats.min_ct), color: C.brand },
    { label: "Max CT", value: fmt(stats.max_ct), color: C.anomaly },
    { label: "Std deviation", value: stats.stddev_ct != null ? `${Number(stats.stddev_ct).toFixed(2)}s` : "—" },
    { label: "Over target", value: longCount, color: longCount > 0 ? C.anomaly : C.ink },
    { label: "Short", value: shortCount, color: shortCount > 0 ? C.skipped : C.ink },
    { label: "Skipped", value: skippedCount, color: skippedCount > 0 ? "#CA8A04" : C.ink },
    { label: "Efficiency", value: eff != null ? `${eff}%` : "—", color: effColor, sub: "cycles in target" },
  ] : [];

  const initialLoading = loading && !stats && history.length === 0;

  const messages = [
    { text: taktEdit.error, color: C.anomaly },
    { text: roiEdit.error, color: C.anomaly },
    { text: minEdit.error, color: C.anomaly },
    { text: shortEdit.error, color: C.anomaly },
    { text: taktEdit.success, color: C.brand },
    { text: roiEdit.success, color: C.brand },
    { text: minEdit.success, color: C.brand },
    { text: shortEdit.success, color: C.brand },
  ].filter((m) => m.text);

  const settings = [
    { key: "takt", el: <InlineSetting label="Takt time" valueText={fmt(takt)} edit={taktEdit} extra={
        targetCT && Number(targetCT) !== Number(takt) ? (
          <HStack gap={SP.sm} align="center">
            <Text {...sectionLabel} color={C.faint}>Target</Text>
            <Text fontSize={FS.body} fontWeight="700" color={C.anomaly} style={num}>{fmt(targetCT)}</Text>
          </HStack>
        ) : null
      } /> },
    { key: "roi", el: <InlineSetting label="ROI timeout" valueText={fmt(roiTimeout)} edit={roiEdit} /> },
    { key: "min", el: <InlineSetting label="Min CT" valueText={fmt(minCycle)} edit={minEdit} /> },
    { key: "short", el: <InlineSetting label="Short limit" valueText={Number(shortLimit) > 0 ? fmt(shortLimit) : "Disabled"} edit={shortEdit} /> },
  ];

  return (
    <Box
      bg="#FFFFFF"
      borderWidth="1px"
      borderColor={C.rule}
      borderTopWidth="2px"
      borderTopColor={C.brand}
      borderRadius="6px"
      overflow="hidden"
    >
      {/* ===================================================
          HEADER
          =================================================== */}
      <Box px={SP.xl} pt={SP.lg} pb={SP.lg}>
        <Flex justify="space-between" align="flex-start" gap={SP.lg} wrap="wrap">
          <Box minW="0">
            <Text fontSize={FS.title} lineHeight="21px" fontWeight="700" color={C.ink} fontFamily={SANS} letterSpacing="-0.01em">
              {station.station_name || `Station ${stationId}`}
            </Text>
            <Text fontSize="10px" lineHeight="13px" mt={SP.xs} color={C.muted} fontFamily={MONO} textTransform="uppercase" letterSpacing="0.08em">
              Line {line} · Station {stationId}
            </Text>
          </Box>

          <Button
            {...smallBtn}
            variant="subtle"
            colorPalette="blue"
            onClick={() => exportCSV(line, stationId, toApiRange(filters))}
          >
            Download CSV
          </Button>
        </Flex>

        {/* ---- SETTINGS STRIP ---- */}
        <Box mt={SP.lg} borderWidth="1px" {...rule} borderRadius="6px" bg={C.surface} overflow="hidden">
          <Flex wrap="wrap">
            {settings.map((s, i) => (
              <Box
                key={s.key}
                flex="1 1 200px"
                minW="0"
                px={SP.lg}
                py={SP.md}
                borderRightWidth={i < settings.length - 1 ? "1px" : 0}
                {...rule}
              >
                {s.el}
              </Box>
            ))}
          </Flex>
        </Box>

        {messages.map((m) => (
          <Flex key={m.text} mt={SP.sm} align="center" gap={SP.sm}>
            <Box w="3px" h="11px" bg={m.color} borderRadius="1px" flexShrink={0} />
            <Text fontSize="11px" color={m.color} fontFamily={MONO} fontWeight="500">
              {m.text}
            </Text>
          </Flex>
        ))}
      </Box>

      {/* ===================================================
          FILTER TOOLBAR
          =================================================== */}
      <Box px={SP.xl} py={SP.md} bg="#F4F6F8" borderTopWidth="1px" borderBottomWidth="1px" {...rule}>
        <FilterBar filters={filters} onChange={handleFilter} />
      </Box>

      {/* ===================================================
          BODY
          =================================================== */}
      <Box p={SP.xl}>
        {initialLoading ? (
          <Flex justify="center" align="center" h="233px" gap={SP.lg}>
            <Spinner size="sm" color={C.brand} />
            <Text color={C.muted} fontSize="12px" fontFamily={MONO} textTransform="uppercase" letterSpacing="0.1em">
              Loading data…
            </Text>
          </Flex>
        ) : (
          <Box opacity={loading ? 0.55 : 1} transition="opacity 0.2s ease" pointerEvents={loading ? "none" : "auto"}>
            {/* ---------- STATS ---------- */}
            {stats && (
              <Box borderWidth="1px" {...rule} borderRadius="6px" overflow="hidden" mb={SP.xl}>
                <SimpleGrid columns={{ base: 2, sm: 3 }} gap={0}>
                  {statItems.map((s) => (
                    <StatCell key={s.label} {...s} />
                  ))}
                </SimpleGrid>
              </Box>
            )}

            {/* ---------- CHART : HISTORY = 1.618 : 1 ---------- */}
            <Grid templateColumns="1fr" gap={SP.xl}>
              <Panel title="Cycle time" meta={`${history.length} cycles`}>
                <Box h={PANEL_H} borderWidth="1px" {...rule} borderRadius="6px" py={SP.md} pr={SP.md}>
                  {chartData.length === 0 ? (
                    <EmptyNote>No cycles in this range.</EmptyNote>
                  ) : (
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={chartData} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke={C.grid} />
                        <XAxis
                          dataKey="idx"
                          tick={{ fontSize: 10, fill: C.axis, fontFamily: MONO }}
                          tickLine={false}
                          interval={chartData.length <= 10 ? 0 : Math.floor(chartData.length / 10)}
                        />
                        <YAxis
                          tick={{ fontSize: 10, fill: C.axis, fontFamily: MONO }}
                          tickLine={false}
                          axisLine={false}
                          unit="s"
                          domain={yDomain}
                          allowDecimals={false}
                        />
                        <Tooltip
                          contentStyle={{ fontSize: 11, fontFamily: MONO, borderRadius: 4, border: `1px solid ${C.rule}`, boxShadow: "0 4px 12px rgba(0,0,0,0.08)", background: "#FFFFFF" }}
                          formatter={(v) => [`${v}s`, "CT"]}
                          labelFormatter={(i) => `Cycle #${i}, ${chartData[i - 1]?.time || ""}`}
                        />
                        {takt && (
                          <ReferenceLine
                            y={Number(takt)}
                            stroke={C.takt}
                            strokeDasharray="4 2"
                            label={{ value: sameTarget ? "Takt = Target" : "Takt", position: "insideBottomRight", fontSize: 10, fill: C.takt, fontFamily: MONO }}
                          />
                        )}
                        {targetCT && !sameTarget && (
                          <ReferenceLine
                            y={Number(targetCT)}
                            stroke={C.anomaly}
                            strokeDasharray="4 2"
                            label={{ value: "Target", position: "insideTopRight", fontSize: 10, fill: C.anomaly, fontFamily: MONO }}
                          />
                        )}
                        {Number(shortLimit) > 0 && (
                          <ReferenceLine
                            y={Number(shortLimit)}
                            stroke={ANOMALY_STYLE.SHORT.color}
                            strokeDasharray="4 2"
                            label={{ value: "Short", position: "insideBottomLeft", fontSize: 10, fill: ANOMALY_STYLE.SHORT.color, fontFamily: MONO }}
                          />
                        )}
                        <Line
                          type="monotone"
                          dataKey="ct"
                          stroke={C.brand}
                          strokeWidth={1.5}
                          isAnimationActive={false}
                          dot={(props) => {
                            const d = chartData[props.index];
                            return (
                              <circle
                                key={props.index}
                                cx={props.cx}
                                cy={props.cy}
                                r={d?.anomaly ? 4 : 2}
                                fill={d?.anomaly ? (d.type !== "OK" ? ANOMALY_STYLE[d.type].color : C.skipped) : C.brand}
                              />
                            );
                          }}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  )}
                </Box>
              </Panel>

              {/* ---------- HISTORY ---------- */}
              <Panel title="Recent cycles">
                <Box h={PANEL_H} overflow="auto" borderWidth="1px" {...rule} borderRadius="6px">
                  {rows.length === 0 ? (
                    <EmptyNote>No cycles in this range.</EmptyNote>
                  ) : (
                    <Table.Root size="sm" stickyHeader interactive>
                      <Table.Header>
                        <Table.Row bg="#F8FAFC" h={ROW_H}>
                          {[
                            { h: "#", align: "end" },
                            { h: "Time" },
                            { h: "CT", align: "end" },
                            { h: "Operator" },
                            { h: "Status" },
                          ].map((c) => (
                            <Table.ColumnHeader
                              key={c.h}
                              textAlign={c.align}
                              bg="#F8FAFC"
                              color="#475569"
                              fontWeight="700"
                              fontSize="10px"
                              fontFamily={MONO}
                              textTransform="uppercase"
                              letterSpacing="0.05em"
                              px={SP.lg}
                              py={0}
                              whiteSpace="nowrap"
                              borderBottom="1px solid #E2E8F0"
                            >
                              {c.h}
                            </Table.ColumnHeader>
                          ))}
                        </Table.Row>
                      </Table.Header>

                      <Table.Body>
                        {rows.map((row, i) => {
                          const type = getAnomalyType(row);
                          const skipped = expectedList(row);
                          const ui = getRowUi(row);
                          const anomaly = type !== "OK" || skipped.length > 0;
                          const cell = { px: SP.lg, py: 0, fontSize: "12px", fontFamily: MONO, verticalAlign: "middle", whiteSpace: "nowrap" };

                          return (
                            <Table.Row
                              key={`${row.end_timestamp}-${i}`}
                              h={ROW_H}
                              bg={ui.bg}
                              _hover={{ bg: ui.hover }}
                              boxShadow={ui.bar ? `inset 3px 0 0 var(--chakra-colors-${ui.bar}-500)` : "none"}
                            >
                              <Table.Cell {...cell} textAlign="end" color={C.faint} style={num}>
                                {history.length - i}
                              </Table.Cell>

                              <Table.Cell {...cell} color="#475569" style={num}>
                                {tsToTime(row.end_timestamp)}
                              </Table.Cell>

                              <Table.Cell
                                {...cell}
                                textAlign="end"
                                color={anomaly ? ui.text : C.ink}
                                fontWeight={anomaly ? "700" : "500"}
                                style={num}
                              >
                                {fmt(row.cycle_time_sec)}
                              </Table.Cell>

                              <Table.Cell {...cell} color={C.muted}>
                                {row.operator_id || "—"}
                              </Table.Cell>

                              <Table.Cell {...cell}>
                                <HStack gap={SP.xs}>
                                  <Badge
                                    colorPalette={anomaly && type === "OK" ? "red" : ui.palette}
                                    variant="subtle"
                                    fontSize="9px"
                                    fontWeight="700"
                                    fontFamily={MONO}
                                    textTransform="uppercase"
                                    letterSpacing="0.05em"
                                    h="18px"
                                    px={SP.md}
                                    borderRadius="2px"
                                  >
                                    {!anomaly ? "Normal" : type === "SHORT" ? "Short" : "Anomaly"}
                                  </Badge>
                                  {skipped.length > 0 && (
                                    <Badge
                                      colorPalette="yellow"
                                      variant="subtle"
                                      fontSize="9px"
                                      fontWeight="700"
                                      fontFamily={MONO}
                                      textTransform="uppercase"
                                      h="18px"
                                      px={SP.md}
                                      borderRadius="2px"
                                      maxW="144px"
                                      overflow="hidden"
                                      textOverflow="ellipsis"
                                      title={row.anomaly_label || `Skipped: ${skipped.join(", ")}`}
                                    >
                                      Skipped: {skipped.join(", ")}
                                    </Badge>
                                  )}
                                </HStack>
                              </Table.Cell>
                            </Table.Row>
                          );
                        })}
                      </Table.Body>
                    </Table.Root>
                  )}
                </Box>
              </Panel>
            </Grid>
          </Box>
        )}
      </Box>
    </Box>
  );
}