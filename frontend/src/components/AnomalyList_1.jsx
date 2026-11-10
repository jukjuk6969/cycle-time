/**
 * AnomalyList.jsx
 * Panel daftar video anomali: filter per stasiun, putar inline, unduh, hapus.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import {
  Box,
  Flex,
  Text,
  Button,
  HStack,
  Badge,
  Spinner,
  Table,
  Input,
  NativeSelect,
} from "@chakra-ui/react";
import VideoPlayer from "./VideoPlayer";
import { useAuth } from "../hooks/useAuth";
import {
  TYPE_UI, getAnomalyType, expectedList, hasExpected, isAnomalyRow, getRowUi,
} from "../hooks/useSocket";
const EMPTY_SUMMARY = [];
import { BASE_URL } from "../config";

const MONO = `"JetBrains Mono", "SF Mono", ui-monospace, Consolas, monospace`;
const SANS = `"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;

const SP = { xs: "3px", sm: "5px", md: "8px", lg: "13px", xl: "21px" };
const FS = { cap: "11px", body: "13px", title: "17px" };
const ROW_H = "34px";
const BTN_H = "21px";
const TABLE_MAX_H = "377px";

function fmt(sec) {
  if (sec == null || Number.isNaN(Number(sec))) return "—";
  const n = Number(sec);
  if (n < 60) return `${n.toFixed(2)} s`;
  const total = Math.round(n);
  return `${Math.floor(total / 60)}m ${total % 60}s`;
}

const TZ_RE = /(Z|[+-]\d{2}:?\d{2})$/;
const DATE_FMT = new Intl.DateTimeFormat("id-ID", {
  timeZone: "Asia/Jakarta",
  day: "2-digit", month: "short",
  hour: "2-digit", minute: "2-digit", second: "2-digit",
  hour12: false,
});
const dateCache = new Map();

function fmtDate(ts) {
  if (!ts) return "—";
  const key = String(ts);
  const hit = dateCache.get(key);
  if (hit !== undefined) return hit;
  const normalized = TZ_RE.test(key) ? key : key.replace(" ", "T") + "Z";
  const d = new Date(normalized);
  const out = Number.isNaN(d.getTime()) ? "—" : DATE_FMT.format(d);
  if (dateCache.size > 1000) dateCache.clear();
  dateCache.set(key, out);
  return out;
}
const num = { fontVariantNumeric: "tabular-nums", fontFamily: MONO };
// input datetime-local dianggap WIB -> ISO UTC untuk server
const wib = (s) => new Date(`${s}:00+07:00`).toISOString();

const FILTERS = {
  anomaly: { key: "anomaly", label: "Anomaly", palette: "red" },
  long: { key: "long", label: "Long", palette: "red" },
  short: { key: "short", label: "Short", palette: "orange" },
  normal: { key: "normal", label: "Normal", palette: "blue" }, // Diubah ke Biru Astra
  genesis: { key: "genesis", label: "Genesis", palette: "purple" },
  late: { key: "late", label: "Late", palette: "cyan" },
  expected: { key: "expected", label: "Skipped", palette: "yellow" },
  all: { key: "all", label: "All", palette: "blue" },
};
const TYPE_KEYS = ["anomaly", "long", "short", "normal"];
const FLAG_KEYS = ["expected", "genesis", "late"];
const ANOMALY_KEYS = ["anomaly", "long", "short"];
function matchType(v, k) {
  if (v.is_buffer)
    return k === "anomaly" ? !!v.is_anomaly : k === "normal" ? !v.is_anomaly : false;
  const t = getAnomalyType(v);
  if (k === "normal") return !isAnomalyRow(v);
  if (k === "anomaly") return isAnomalyRow(v);
  return getAnomalyType(v) === k.toUpperCase();
}
const PAGE_SIZE = 50;
const FETCH_LIMIT = PAGE_SIZE;

const COLUMNS = [
  { label: "Time", sortKey: "time" },
  { label: "Line", align: "center", sortKey: "line" },
  { label: "Station", align: "center", sortKey: "station" },
  { label: "Name" },
  { label: "Cycle time", align: "end", sortKey: "ct" },
  { label: "Size", align: "end", sortKey: "size" },
  { label: "Type" },
  { label: "Action", align: "end" },
];

const cellProps = {
  px: SP.lg,
  py: 0,
  fontSize: FS.body,
  verticalAlign: "middle",
  whiteSpace: "nowrap",
  fontFamily: MONO, // Data tabel pakai mono
};

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

function FBtn({ f, active, onSelect, ...rest }) {
  return (
    <Button
      {...smallBtn}
      aria-pressed={active}
      variant={active ? "solid" : "ghost"}
      colorPalette={active ? f.palette : "gray"}
      onClick={() => onSelect(f.key)}
      {...rest}
    >
      {f.label}
    </Button>
  );
}

function reasonOf(type, ct, limits) {
  if (!limits?.target || ct == null) return null;
  const c = Number(ct).toFixed(2);
  if (type === "LONG")
    return { short: `> ${limits.target} s`, full: `Long: CT ${c} s melebihi batas anomali ${limits.target} s` };
  if (type === "SHORT")
    return { short: `< ${limits.short} s`, full: `Short: CT ${c} s di bawah batas short ${limits.short} s` };
  return limits.short > 0
    ? { short: `${limits.short}–${limits.target} s`, full: `Normal: CT ${c} s berada antara batas short ${limits.short} s dan batas anomali ${limits.target} s` }
    : { short: `≤ ${limits.target} s`, full: `Normal: CT ${c} s tidak melebihi batas anomali ${limits.target} s` };
}

const AnomalyRow = React.memo(function AnomalyRow({ v, name, limits, isDeleting, onPlay, onDelete }) {
  const type = v.is_buffer
    ? (v.is_anomaly ? "LONG" : "OK")
    : getAnomalyType(v);
  const expected = v.is_buffer ? [] : expectedList(v);
  const ui = v.is_buffer ? TYPE_UI[type] : getRowUi(v);
  const anomaly = type !== "OK" || expected.length > 0;
  const reason = v.is_buffer ? null : reasonOf(type, v.cycle_time_sec, limits);
  const downloadHref = v.is_buffer
    ? `${BASE_URL}/api/fd/${v.line}/${v.station_id}/video/${v.video_type}/${encodeURIComponent(v.filename)}?download=1`
    : `${BASE_URL}/api/video/${encodeURIComponent(v.id)}/download`;
  return (
    <Table.Row
      h={ROW_H}
      bg={ui.bg}
      _hover={{ bg: ui.hover }}
      boxShadow={ui.bar ? `inset 3px 0 0 var(--chakra-colors-${ui.bar}-500)` : "none"}
      opacity={isDeleting ? 0.5 : 1}
    >
      <Table.Cell {...cellProps} color="#475569" style={num}>
        {fmtDate(v.recorded_at)}
      </Table.Cell>

      <Table.Cell {...cellProps} textAlign="center" color="#231F20" style={num}>
        {v.line}
      </Table.Cell>

      <Table.Cell {...cellProps} textAlign="center" color="#231F20" style={num}>
        {v.station_id}
      </Table.Cell>

      <Table.Cell {...cellProps} color={name ? "#231F20" : "#94A3B8"} fontFamily={SANS} fontWeight="500">
        {name || "—"}
      </Table.Cell>

      <Table.Cell
        {...cellProps}
        textAlign="end"
        fontWeight={anomaly ? "700" : "500"}
        color={anomaly ? ui.text : "#231F20"}
        style={num}
      >
        {fmt(v.cycle_time_sec)}
        {reason && (
          <Text as="span" ml={SP.sm} fontSize="10px" fontWeight="400" color="#64748B" fontFamily={MONO}>
            {reason.short}
          </Text>
        )}
      </Table.Cell>

      <Table.Cell {...cellProps} textAlign="end" color="#64748B" style={num}>
        {v.size_mb != null ? `${Number(v.size_mb).toFixed(1)} MB` : "—"}
      </Table.Cell>

      <Table.Cell {...cellProps}>
        <HStack gap={SP.xs}>
          {!anomaly ? (
            <Badge colorPalette="blue" variant="subtle" fontSize="9px" fontWeight="700" fontFamily={MONO} textTransform="uppercase" h={BTN_H} px={SP.md} borderRadius="2px">
              Normal
            </Badge>
          ) : (
            <>
              <Badge colorPalette="red" variant="solid" fontSize="9px" fontWeight="700" fontFamily={MONO} textTransform="uppercase" h={BTN_H} px={SP.md} borderRadius="2px">
                Anomaly
              </Badge>
              {!v.is_buffer && type !== "OK" && (
                <Badge
                  colorPalette={type === "SHORT" ? "orange" : "red"}
                  variant="subtle"
                  fontSize="9px" fontWeight="700" fontFamily={MONO} textTransform="uppercase"
                  h={BTN_H}
                  px={SP.md}
                  borderRadius="2px"
                >
                  {type === "SHORT" ? "Short" : "Long"}
                </Badge>
              )}
              {expected.length > 0 && (
                <Badge
                  colorPalette="yellow"
                  variant="subtle"
                  fontSize="9px" fontWeight="700" fontFamily={MONO} textTransform="uppercase"
                  h={BTN_H}
                  px={SP.md}
                  borderRadius="2px"
                  maxW="233px"
                  overflow="hidden"
                  textOverflow="ellipsis"
                  title={v.anomaly_label || `Skipped: ${expected.join(", ")}`}
                >
                  Skipped: {expected.join(", ")}
                </Badge>
              )}
            </>
          )}
          {v.is_genesis && (
            <Badge colorPalette="purple" variant="subtle" fontSize="9px" fontWeight="700" fontFamily={MONO} textTransform="uppercase" h={BTN_H} px={SP.md} borderRadius="2px">
              Genesis
            </Badge>
          )}
          {v.is_buffer && (
            <Badge
              colorPalette={v.pending ? "orange" : "red"}
              variant="solid" fontSize="9px" fontWeight="700" fontFamily={MONO} textTransform="uppercase" h={BTN_H} px={SP.md} borderRadius="2px"
              title={v.pending ? "Ada di FD, cycle-nya masih antre dikirim ke server" : "Ada di FD tetapi tidak ada di server maupun antrean kirim"}
            >
              {v.pending ? "BUFFER · WAIT" : "BUFFER"}
            </Badge>
          )}
          {!v.is_buffer && v.is_buffered && (
            <Badge colorPalette="cyan" variant="subtle" fontSize="9px" fontWeight="700" fontFamily={MONO} textTransform="uppercase" h={BTN_H} px={SP.md} borderRadius="2px"
              title="Cycle ini sempat terlambat dikirim ke server">
              Late
            </Badge>
          )}
        </HStack>
      </Table.Cell>

      <Table.Cell {...cellProps}>
        <HStack gap={SP.xs} justify="flex-end">
          <Button {...smallBtn} variant="subtle" colorPalette="blue" onClick={() => onPlay(v)}>
            Play
          </Button>

          <Button {...smallBtn} variant="outline" asChild>
            <a href={downloadHref} download={v.filename}>
              Download
            </a>
          </Button>

          {anomaly && !v.is_buffer && onDelete && (
            <Button
              {...smallBtn}
              variant="ghost"
              colorPalette="red"
              onClick={() => onDelete(v)}
              disabled={isDeleting}
            >
              {isDeleting ? <Spinner size="xs" /> : "Delete"}
            </Button>
          )}
        </HStack>
      </Table.Cell>
    </Table.Row>
  );
});

function AnomalyList({ summary = EMPTY_SUMMARY, line = "", stationId = "" }) {
  const { isAdmin } = useAuth();
  const [videos, setVideos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(null);
  const [selectedVideo, setSelectedVideo] = useState(null);
  const [filterStation, setFilterStation] = useState("");
  const [filters, setFilters] = useState(["anomaly"]);
  const [range, setRange] = useState({ from: "", to: "" });

  const toggleFilter = useCallback((key) => {
    if (key === "all") return setFilters([]);
    setFilters((prev) => {
      const on = prev.includes(key);

      if (key === "expected") {
        if (on) return prev.filter((k) => k !== "expected");
        return [...prev.filter((k) => k !== "normal"), "expected"];
      }

      if (key === "long" || key === "short") {
        if (on) return prev.filter((k) => k !== key);
        const base = prev.filter((k) => k !== "normal");
        return base.includes("anomaly") ? [...base, key] : [...base, "anomaly", key];
      }

      if (key === "normal") {
        if (on) return prev.filter((k) => k !== "normal");
        return [...prev.filter((k) => !ANOMALY_KEYS.includes(k) && k !== "expected"), "normal"];
      }

      return on ? prev.filter((k) => k !== key) : [...prev, key];
    });
  }, []);
  
  const [sort, setSort] = useState({ key: "time", dir: "desc" });
  const [total, setTotal] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [renderCount, setRenderCount] = useState(PAGE_SIZE);

  const onSort = (key) =>
    setSort((s) => (s.key === key
      ? { key, dir: s.dir === "desc" ? "asc" : "desc" }
      : { key, dir: key === "time" ? "desc" : "asc" }));

  const serverFilters = useMemo(() => {
    const hasNormal = filters.includes("normal");
    const hasAnom = filters.some((k) => ANOMALY_KEYS.includes(k));
    const subs = filters.filter((k) => k === "long" || k === "short");
    const p = {};
    if (hasNormal && !hasAnom) p.anomaly = "false";
    else if (hasAnom) p.anomaly = "true";
    if (subs.length) p.atype = subs.map((s) => s.toUpperCase()).join(",");
    if (filters.includes("genesis")) p.genesis = "true";
    if (filters.includes("expected")) p.expected = "true";
    if (filters.includes("late")) p.late = "true";
    return p;
  }, [filters]);

  const parentControlled = (line !== "" && line != null) || (stationId !== "" && stationId != null);
  const baseParams = useMemo(() => {
    let qLine = line, qStation = stationId;
    if (!parentControlled && filterStation) [qLine, qStation] = filterStation.split("-");
    const p = { ...serverFilters, sort: sort.key, dir: sort.dir, limit: FETCH_LIMIT };
    if (range.from) p.from = wib(range.from);
    if (range.to) p.to = wib(range.to);
    if (qLine !== "" && qLine != null) p.line = qLine;
    if (qStation !== "" && qStation != null) p.station_id = qStation;
    return p;
  }, [serverFilters, sort, filterStation, line, stationId, parentControlled, range]);

  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [baseParams]);

  const queryParams = useMemo(() => ({ ...baseParams, offset: (page - 1) * PAGE_SIZE }), [baseParams, page]);
  const reqId = useRef(0);

  const summaryRef = useRef(summary);
  summaryRef.current = summary;

  const loadVideos = useCallback(async () => {
    const id = ++reqId.current;
    try {
      setLoading(true);
      setError("");
      const res = await axios.get(`${BASE_URL}/api/videos`, { params: queryParams });
      if (id !== reqId.current) return;
      setVideos(Array.isArray(res.data) ? res.data : []);
      setTotal(Number(res.headers["x-total-count"]) || 0);
    } catch (err) {
      if (id !== reqId.current) return;
      console.error("Gagal fetch videos:", err.message);
      setVideos([]);
      setTotal(0);
      setError(err.response?.data?.error || "Daftar video tidak dapat dimuat. Periksa koneksi ke server.");
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  }, [queryParams]);

  const dbLoaded = useMemo(() => videos.filter((v) => !v.is_buffer).length, [videos]);

  const loadMore = async () => {
    const id = reqId.current;
    try {
      setLoadingMore(true);
      const res = await axios.get(`${BASE_URL}/api/videos`, {
        params: { ...queryParams, offset: dbLoaded },
      });
      if (id !== reqId.current) return;
      const rows = Array.isArray(res.data) ? res.data : [];
      if (rows.length === 0) {
        setTotal(dbLoaded);
        return;
      }
      setVideos((prev) => {
        const k = (v) => `${v.line}-${v.station_id}-${v.id}`;
        const seen = new Set(prev.filter((v) => !v.is_buffer).map(k));
        return [...prev, ...rows.filter((v) => !seen.has(k(v)))];
      });
      setRenderCount((c) => c + PAGE_SIZE);
    } catch (e) {
      alert("Gagal memuat data: " + (e.response?.data?.error || e.message));
    } finally {
      setLoadingMore(false);
    }
  };

  useEffect(() => { loadVideos(); }, [loadVideos]);

  const handleDelete = useCallback(async (video) => {
    if (!window.confirm(`Delete this anomaly videos?\n\n${video.filename}\n\nTindakan ini tidak bisa dibatalkan.`)) return;
    try {
      setDeleting(video.id);
      await axios.delete(`${BASE_URL}/api/video/${encodeURIComponent(video.id)}`);
      setVideos((prev) => prev.filter((v) => v.id !== video.id));
      setTotal((t) => Math.max(0, t - 1));
    } catch (e) {
      alert("Failed to delete: " + (e.response?.data?.error || e.message));
    } finally {
      setDeleting(null);
    }
  }, []);

  const stationNames = useMemo(() => {
    const map = new Map();
    summary.forEach((s) => {
      map.set(`${Number(s.line)}-${Number(s.station_id)}`, s.station_name || s.name || "");
    });
    return map;
  }, [summary]);

  const stationLimits = useMemo(() => {
    const map = new Map();
    summary.forEach((s) =>
      map.set(`${Number(s.line)}-${Number(s.station_id)}`, {
        target: Number(s.target_cycle_time) || null,
        short: Number(s.short_anomaly_sec) || 0,
      })
    );
    return map;
  }, [summary]);

  const visible = useMemo(() => {
    const subs = filters.filter((k) => k === "long" || k === "short");
    const types = filters.filter((k) => TYPE_KEYS.includes(k) && !(k === "anomaly" && subs.length));
    const flags = filters.filter((k) => FLAG_KEYS.includes(k));
    return videos.filter((v) => {
      if (types.length && !types.some((k) => matchType(v, k))) return false;
      if (flags.includes("genesis") && !v.is_genesis) return false;
      if (flags.includes("expected") && (v.is_buffer || !hasExpected(v))) return false;
      if (flags.includes("late") && (v.is_buffer || !v.is_buffered)) return false;
      return true;
    });
  }, [videos, filters]);

  const bufferCount = useMemo(() => visible.filter((v) => v.is_buffer).length, [visible]);
  useEffect(() => setRenderCount(PAGE_SIZE), [queryParams]);

  const shown = useMemo(() => visible.slice(0, renderCount), [visible, renderCount]);

  const onTableScroll = (e) => {
    const el = e.currentTarget;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 120)
      setRenderCount((c) => (c < visible.length ? c + PAGE_SIZE : c));
  };

  const filterText = filters.length ? filters.map((k) => FILTERS[k].label).join(" + ") : "";
  const title = filters.length ? `${filterText} Videos` : "All Video";
  const rangeText = range.from || range.to
    ? ` · ${range.from ? range.from.replace("T", " ") : "awal"} → ${range.to ? range.to.replace("T", " ") : "sekarang"}`
    : " · hari ini";
  const subtitle = `${shown.length} dari ${total + bufferCount} video` + (bufferCount ? ` · ${bufferCount} buffer` : "") + rangeText;

  const stateRow = (content) => (
    <Table.Row>
      <Table.Cell colSpan={COLUMNS.length} p={0}>
        <Flex h="144px" direction="column" align="center" justify="center" gap={SP.md}>
          {content}
        </Flex>
      </Table.Cell>
    </Table.Row>
  );

  return (
    <Box bg="#FFFFFF" borderWidth="1px" borderColor="#E2E8F0" borderRadius="6px" overflow="hidden" mt={SP.lg}>
      {/* Header */}
      <Flex px={SP.xl} py={SP.lg} align="center" justify="space-between" gap={SP.lg} wrap="wrap" borderBottomWidth="1px" borderColor="#E2E8F0">
        <Box minW="0">
          <Text fontSize={FS.title} lineHeight="21px" fontWeight="700" color="#231F20" fontFamily={SANS} letterSpacing="-0.01em">
            {title}
          </Text>
          <Text fontSize="10px" lineHeight="13px" mt={SP.xs} color="#64748B" fontFamily={MONO} textTransform="uppercase" letterSpacing="0.05em">
            {subtitle}
          </Text>
        </Box>

        <HStack gap={SP.md} wrap="wrap">
          {!parentControlled && (
            <NativeSelect.Root w="233px" size="xs">
              <NativeSelect.Field
                aria-label="Filter stasiun"
                value={filterStation}
                onChange={(e) => setFilterStation(e.target.value)}
                h={BTN_H}
                fontSize="11px"
                fontFamily={MONO}
                py={0}
                borderRadius="4px"
                borderColor="#E2E8F0"
              >
                <option value="">Semua stasiun</option>
                {summary.map((s) => (
                  <option key={`${s.line}-${s.station_id}`} value={`${s.line}-${s.station_id}`}>
                    L{s.line} ST{s.station_id} — {s.station_name}
                  </option>
                ))}
              </NativeSelect.Field>
              <NativeSelect.Indicator />
            </NativeSelect.Root>
          )}

          <HStack gap={SP.md} align="center" wrap="wrap">
            {/* Grup Anomaly */}
            <Flex direction="column" gap={SP.xs} bg="#F8FAFC" p={SP.xs} borderRadius="4px" border="1px solid #E2E8F0" role="group" aria-label="Filter anomali">
              <FBtn f={FILTERS.anomaly} active={filters.includes("anomaly")} onSelect={toggleFilter} />
              <HStack gap={SP.xs}>
                {["long", "short"].map((k) => (
                  <FBtn key={k} f={FILTERS[k]} active={filters.includes(k)} onSelect={toggleFilter} flex="1" />
                ))}
              </HStack>
            </Flex>
            
            {/* Grup Rentang Waktu */}
            <HStack gap={SP.xs} bg="#F8FAFC" p={SP.xs} borderRadius="4px" border="1px solid #E2E8F0" role="group" aria-label="Rentang waktu">
              <Button {...smallBtn} variant={!range.from && !range.to ? "solid" : "ghost"}
                colorPalette={!range.from && !range.to ? "blue" : "gray"}
                onClick={() => setRange({ from: "", to: "" })}>
                Hari ini
              </Button>
              <Input type="datetime-local" size="xs" h={BTN_H} w="165px" fontSize="11px" fontFamily={MONO} bg="#FFFFFF" borderRadius="2px" borderColor="#E2E8F0"
                aria-label="Dari" value={range.from}
                onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))} />
              <Text fontSize="10px" color="#64748B" fontFamily={MONO}>→</Text>
              <Input type="datetime-local" size="xs" h={BTN_H} w="165px" fontSize="11px" fontFamily={MONO} bg="#FFFFFF" borderRadius="2px" borderColor="#E2E8F0"
                aria-label="Sampai" value={range.to}
                onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))} />
            </HStack>

            {/* Grup Normal */}
            <HStack gap={SP.xs} bg="#F8FAFC" p={SP.xs} borderRadius="4px" border="1px solid #E2E8F0" role="group" aria-label="Filter normal">
              <FBtn f={FILTERS.normal} active={filters.includes("normal")} onSelect={toggleFilter} px={SP.lg} />
            </HStack>
            
            {/* Grup Penanda */}
            <HStack gap={SP.xs} bg="#F8FAFC" p={SP.xs} borderRadius="4px" border="1px solid #E2E8F0" role="group" aria-label="Filter penanda">
              {["expected", "genesis", "late"].map((k) => (
                <FBtn key={k} f={FILTERS[k]} active={filters.includes(k)} onSelect={toggleFilter} px={SP.lg} />
              ))}
            </HStack>
            
            {/* Grup All */}
            <HStack gap={SP.xs} bg="#F8FAFC" p={SP.xs} borderRadius="4px" border="1px solid #E2E8F0">
              <FBtn f={FILTERS.all} active={filters.length === 0} onSelect={toggleFilter} px={SP.lg} />
            </HStack>
          </HStack>

          <Button {...smallBtn} variant="outline" onClick={loadVideos} disabled={loading} borderColor="#E2E8F0" color="#475569" _hover={{ bg: "#F1F5F9" }}>
            {loading ? <Spinner size="xs" color="#005A9C" /> : "Refresh"}
          </Button>
        </HStack>
      </Flex>

      {/* Tabel */}
      <Box overflow="auto" maxH={TABLE_MAX_H} onScroll={onTableScroll}>
        <Table.Root size="sm" stickyHeader interactive>
          <Table.Header>
            <Table.Row bg="#F8FAFC" h={ROW_H}>
              {COLUMNS.map((c) => (
                <Table.ColumnHeader
                  key={c.label}
                  textAlign={c.align}
                  bg="#F8FAFC"
                  color={sort.key === c.sortKey ? "#231F20" : "#475569"}
                  fontWeight="700"
                  fontSize="10px"
                  fontFamily={MONO}
                  textTransform="uppercase"
                  letterSpacing="0.05em"
                  px={SP.lg}
                  py={0}
                  whiteSpace="nowrap"
                  cursor={c.sortKey ? "pointer" : "default"}
                  userSelect="none"
                  onClick={c.sortKey ? () => onSort(c.sortKey) : undefined}
                  aria-sort={sort.key === c.sortKey ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
                  borderBottom="1px solid #E2E8F0"
                >
                  {c.label}
                  {sort.key === c.sortKey && (sort.dir === "asc" ? " ▲" : " ▼")}
                </Table.ColumnHeader>
              ))}
            </Table.Row>
          </Table.Header>

          <Table.Body>
            {loading && visible.length === 0
              ? stateRow(<Spinner size="sm" color="#005A9C" />)
              : error
                ? stateRow(
                  <>
                    <Text fontSize="12px" color="#DC2626" fontFamily={MONO}>
                      {error}
                    </Text>
                    <Button {...smallBtn} variant="outline" onClick={loadVideos}>
                      Coba lagi
                    </Button>
                  </>
                )
                : visible.length === 0 ? stateRow(
                  <Text fontSize="12px" color="#64748B" fontFamily={MONO}>
                    Belum ada video{filterText ? ` ${filterText}` : ""} yang tersimpan.
                  </Text>
                )
                  : shown.map((v) => (
                    <AnomalyRow
                      key={`${v.is_buffer ? "b" : "d"}-${v.line}-${v.station_id}-${v.id}`}
                      v={v}
                      name={stationNames.get(`${Number(v.line)}-${Number(v.station_id)}`)}
                      limits={stationLimits.get(`${Number(v.line)}-${Number(v.station_id)}`)}
                      isDeleting={deleting === v.id}
                      onPlay={setSelectedVideo}
                      onDelete={isAdmin ? handleDelete : undefined}
                    />
                  ))}
          </Table.Body>
        </Table.Root>
      </Box>
      
      {(renderCount < visible.length || dbLoaded < total) && (
        <Flex px={SP.xl} py={SP.md} justify="space-between" align="center" borderTopWidth="1px" borderColor="#E2E8F0">
          <Text fontSize="10px" color="#64748B" fontFamily={MONO}>
            Menampilkan {shown.length} dari {visible.length} dimuat
            {total > dbLoaded ? ` (total ${total})` : ""}
          </Text>
          {renderCount >= visible.length && dbLoaded < total && (
            <Button {...smallBtn} variant="outline" onClick={loadMore} disabled={loadingMore}>
              {loadingMore ? <Spinner size="xs" color="#005A9C" /> : `Muat ${Math.min(FETCH_LIMIT, total - dbLoaded)} berikutnya`}
            </Button>
          )}
        </Flex>
      )}
      
      {/* Video Player Modal */}
      {selectedVideo && (
        <VideoPlayer video={selectedVideo} onClose={() => setSelectedVideo(null)} />
      )}
    </Box>
  );
}
export default React.memo(AnomalyList);