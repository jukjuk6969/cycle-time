/**
 * AnomalyList.jsx
 *
 * Anomaly video list panel: filter by station, play inline, download, delete.
 *
 * Design system — Astra Visteon Indonesia
 *   Blue   #005A9C  → normal state, primary action, secondary emphasis
 *   Orange #F7941D  → anomaly, warning, destructive accent
 *   Black  #231F20  → typography, ink surfaces
 *
 * Layout principles:
 *   - Spacing scale follows the original document (3 / 5 / 8 / 13 / 21) — not inflated.
 *   - No decorative labels on top of filter groups (avoids AI-slop & oversized headers).
 *   - Filter groups stay as in the original: thin `ink50` box + `ink200` border,
 *     with content-driven height (Anomaly 2-row, others 1-row, center-aligned).
 *   - All features, handlers, and effects remain identical to the previous version.
 *   - Typography scaled +2px per token for improved legibility.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import {
  Box,
  Flex,
  Text,
  Button,
  HStack,
  Spinner,
  Table,
  Input,
  NativeSelect,
} from "@chakra-ui/react";
import VideoPlayer from "./VideoPlayer";
import { useAuth } from "../hooks/useAuth";
import {
  getAnomalyType,
  expectedList,
  hasExpected,
  isAnomalyRow,
} from "../hooks/useSocket";
import { BASE_URL } from "../config";

const EMPTY_SUMMARY = [];

/* ============================================================================
 * 1. PALETTE — Astra Visteon Indonesia
 * ==========================================================================*/

const P = {
  /* Brand blue */
  blue: "#005A9C",
  blueHover: "#004880",
  blueActive: "#003A6B",
  blue700: "#004A82",
  blue200: "#99BFDD",
  blue100: "#C5DAEC",
  blue50: "#EDF4FA",

  /* Brand orange */
  orange: "#F7941D",
  orangeHover: "#DE7E0A",
  orangeActive: "#C5660A",
  orange700: "#C5660A",
  orange200: "#FAD1A0",
  orange100: "#FCE4C5",
  orange50: "#FEF4E5",

  /* Ink scale */
  black: "#231F20",
  ink900: "#0F172A",
  ink800: "#1E293B",
  ink700: "#334155",
  ink600: "#475569",
  ink500: "#64748B",
  ink400: "#94A3B8",
  ink300: "#CBD5E1",
  ink200: "#E2E8F0",
  ink100: "#F1F5F9",
  ink50: "#F8FAFC",
  white: "#FFFFFF",
};

/* ============================================================================
 * 2. FOUNDATIONS — spacing / typography / radius / elevation
 *
 *    Spacing values intentionally match the original document to preserve
 *    proportions. Font sizes are +2px over the original for legibility.
 * ==========================================================================*/

const MONO = `"JetBrains Mono", "SF Mono", ui-monospace, Consolas, monospace`;
const SANS = `"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;

const SP = {
  xs: "3px",
  sm: "5px",
  md: "8px",
  lg: "13px",
  xl: "21px",
};

const FS = {
  micro: "11px",   // badge
  tiny: "12px",    // column header, subtitle, small button
  cap: "13px",     // select, date input
  body: "15px",    // cell body
  title: "19px",   // panel title
};

const RADII = {
  xs: "2px",
  sm: "3px",
  md: "4px",
  lg: "6px",
};

const BTN_H = "21px";
const ROW_H = "34px";
const TABLE_MAX_H = "377px";

const FOCUS_BLUE = { boxShadow: `0 0 0 3px ${P.blue100}` };
const FOCUS_ORANGE = { boxShadow: `0 0 0 3px ${P.orange100}` };

/* ============================================================================
 * 3. FORMATTING UTILITIES
 * ==========================================================================*/

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
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
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

/* datetime-local input is treated as WIB → ISO UTC for the server. */
const wib = (s) => new Date(`${s}:00+07:00`).toISOString();

/* ============================================================================
 * 4. FILTER DEFINITIONS
 *
 *    `tone` is resolved to Astra colors inside FilterButton. Chakra
 *    colorPalette is not used, to keep the brand palette consistent.
 * ==========================================================================*/

const FILTERS = {
  anomaly: { key: "anomaly", label: "Anomaly", tone: "orange" },
  long: { key: "long", label: "Long", tone: "orange" },
  short: { key: "short", label: "Short", tone: "orange" },
  normal: { key: "normal", label: "Normal", tone: "blue" },
  genesis: { key: "genesis", label: "Genesis", tone: "ink" },
  late: { key: "late", label: "Late", tone: "blue" },
  expected: { key: "expected", label: "Skipped", tone: "orange" },
  all: { key: "all", label: "All", tone: "blue" },
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

/* ============================================================================
 * 5. TABLE COLUMNS
 * ==========================================================================*/

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
  fontFamily: MONO,
};

const smallBtn = {
  h: BTN_H,
  minW: 0,
  px: SP.md,
  fontSize: FS.tiny,
  fontWeight: "700",
  fontFamily: MONO,
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  borderRadius: RADII.sm,
};

/* ============================================================================
 * 6. TONES — Astra palette mapping for active buttons & badges
 * ==========================================================================*/

const TONE = {
  orange: { bg: P.orange, bgHover: P.orangeHover, fg: P.white },
  blue: { bg: P.blue, bgHover: P.blueHover, fg: P.white },
  ink: { bg: P.black, bgHover: P.ink800, fg: P.white },
};

const BADGE_TONE = {
  blue: {
    subtle: { bg: P.blue50, fg: P.blue, bd: P.blue100 },
    solid: { bg: P.blue, fg: P.white, bd: P.blue },
    outline: { bg: "transparent", fg: P.blue, bd: P.blue200 },
  },
  orange: {
    subtle: { bg: P.orange50, fg: P.orange700, bd: P.orange100 },
    solid: { bg: P.orange, fg: P.white, bd: P.orange },
    outline: { bg: "transparent", fg: P.orange700, bd: P.orange200 },
  },
  ink: {
    subtle: { bg: P.ink100, fg: P.ink700, bd: P.ink200 },
    solid: { bg: P.black, fg: P.white, bd: P.black },
    outline: { bg: "transparent", fg: P.black, bd: P.ink300 },
  },
};

/* ============================================================================
 * 7. ATOM — AstraBadge
 * ==========================================================================*/

function AstraBadge({ tone = "blue", variant = "subtle", title, children, ...rest }) {
  const group = BADGE_TONE[tone] || BADGE_TONE.blue;
  const style = group[variant] || group.subtle;
  return (
    <Box
      as="span"
      display="inline-flex"
      alignItems="center"
      h={BTN_H}
      px={SP.md}
      fontSize={FS.micro}
      fontWeight="700"
      fontFamily={MONO}
      textTransform="uppercase"
      letterSpacing="0.06em"
      lineHeight="1"
      borderRadius={RADII.xs}
      bg={style.bg}
      color={style.fg}
      border="1px solid"
      borderColor={style.bd}
      title={title}
      {...rest}
    >
      {children}
    </Box>
  );
}

/* ============================================================================
 * 8. ATOM — FilterButton
 * ==========================================================================*/

function FilterButton({ filter, active, onSelect, ...rest }) {
  const t = TONE[filter.tone] || TONE.blue;
  return (
    <Button
      {...smallBtn}
      aria-pressed={active}
      variant="ghost"
      bg={active ? t.bg : "transparent"}
      color={active ? t.fg : P.ink600}
      border="1px solid"
      borderColor={active ? t.bg : "transparent"}
      _hover={
        active
          ? { bg: t.bgHover }
          : { bg: P.ink100, color: P.ink800 }
      }
      _focusVisible={filter.tone === "orange" ? FOCUS_ORANGE : FOCUS_BLUE}
      onClick={() => onSelect(filter.key)}
      {...rest}
    >
      {filter.label}
    </Button>
  );
}

/* ============================================================================
 * 9. ATOM — Cluster containers
 *
 *    Follows the original structure: `ink50` + `ink200` border, no labels.
 * ==========================================================================*/

const clusterStyle = {
  bg: P.ink50,
  p: SP.xs,
  borderRadius: RADII.md,
  border: "1px solid",
  borderColor: P.ink200,
};

function RowCluster({ children, ...rest }) {
  return (
    <HStack gap={SP.xs} {...clusterStyle} role="group" {...rest}>
      {children}
    </HStack>
  );
}

function ColumnCluster({ children, ...rest }) {
  return (
    <Flex direction="column" gap={SP.xs} {...clusterStyle} role="group" {...rest}>
      {children}
    </Flex>
  );
}

/* ============================================================================
 * 10. REASON HELPER
 * ==========================================================================*/

function reasonOf(type, ct, limits) {
  if (!limits?.target || ct == null) return null;
  const c = Number(ct).toFixed(2);
  if (type === "LONG")
    return {
      short: `> ${limits.target} s`,
      full: `Long: CT ${c} s exceeds anomaly limit ${limits.target} s`,
    };
  if (type === "SHORT")
    return {
      short: `< ${limits.short} s`,
      full: `Short: CT ${c} s is below short limit ${limits.short} s`,
    };
  return limits.short > 0
    ? {
        short: `${limits.short}–${limits.target} s`,
        full: `Normal: CT ${c} s is between short limit ${limits.short} s and anomaly limit ${limits.target} s`,
      }
    : {
        short: `≤ ${limits.target} s`,
        full: `Normal: CT ${c} s does not exceed anomaly limit ${limits.target} s`,
      };
}

/* ============================================================================
 * 11. ROW STYLE — Astra-mapped, data-driven
 *
 *     Priority order: anomaly → expected → genesis → late → buffer → normal.
 * ==========================================================================*/

function computeRowStyle(v) {
  const t = v.is_buffer ? (v.is_anomaly ? "LONG" : "OK") : getAnomalyType(v);
  const expected = !v.is_buffer && hasExpected(v);
  const anomaly = t !== "OK" || expected;

  if (anomaly)
    return { bg: P.orange50, hover: P.orange100, text: P.orange700, accent: P.orange };
  if (v.is_genesis)
    return { bg: P.ink50, hover: P.ink100, text: P.black, accent: P.black };
  if (v.is_buffer)
    return { bg: P.ink50, hover: P.ink100, text: P.black, accent: null };
  return { bg: P.white, hover: P.ink50, text: P.black, accent: null };
}

/* ============================================================================
 * 12. CELL — CycleTimeCell
 * ==========================================================================*/

function CycleTimeCell({ v, type, limits, anomaly }) {
  const reason = v.is_buffer ? null : reasonOf(type, v.cycle_time_sec, limits);
  const toneColor =
    type === "SHORT"
      ? P.orange700
      : type === "LONG"
      ? P.orange
      : P.black;
  return (
    <>
      <span style={{ color: anomaly ? toneColor : P.black, fontWeight: anomaly ? 700 : 500 }}>
        {fmt(v.cycle_time_sec)}
      </span>
      {reason && (
        <Text
          as="span"
          ml={SP.sm}
          fontSize={FS.tiny}
          fontWeight="400"
          color={P.ink500}
          fontFamily={MONO}
          letterSpacing="0"
        >
          {reason.short}
        </Text>
      )}
    </>
  );
}

/* ============================================================================
 * 13. CELL — TypeBadgeStack
 * ==========================================================================*/

function TypeBadgeStack({ v, type, anomaly, expected }) {
  return (
    <HStack gap={SP.xs}>
      {!anomaly ? (
        <AstraBadge tone="blue" variant="subtle">
          Normal
        </AstraBadge>
      ) : (
        <>
          <AstraBadge tone="orange" variant="solid">
            Anomaly
          </AstraBadge>
          {!v.is_buffer && type !== "OK" && (
            <AstraBadge
              tone="orange"
              variant={type === "SHORT" ? "outline" : "subtle"}
            >
              {type === "SHORT" ? "Short" : "Long"}
            </AstraBadge>
          )}
          {expected.length > 0 && (
            <AstraBadge
              tone="orange"
              variant="subtle"
              maxW="233px"
              overflow="hidden"
              textOverflow="ellipsis"
              title={v.anomaly_label || `Skipped: ${expected.join(", ")}`}
            >
              Skipped: {expected.join(", ")}
            </AstraBadge>
          )}
        </>
      )}

      {v.is_genesis && (
        <AstraBadge tone="ink" variant="subtle">
          Genesis
        </AstraBadge>
      )}

      {v.is_buffer && (
        <AstraBadge
          tone="orange"
          variant={v.pending ? "outline" : "solid"}
          title={
            v.pending
              ? "On FD, the cycle is still queued to be sent to the server"
              : "On FD but not on the server nor in the send queue"
          }
        >
          {v.pending ? "Buffer · Wait" : "Buffer"}
        </AstraBadge>
      )}

      {!v.is_buffer && v.is_buffered && (
        <AstraBadge
          tone="blue"
          variant="subtle"
          title="This cycle was delayed being sent to the server"
        >
          Late
        </AstraBadge>
      )}
    </HStack>
  );
}

/* ============================================================================
 * 14. CELL — RowActions
 * ==========================================================================*/

function RowActions({ v, anomaly, isDeleting, onPlay, onDelete }) {
  const downloadHref = v.is_buffer
    ? `${BASE_URL}/api/fd/${v.line}/${v.station_id}/video/${v.video_type}/${encodeURIComponent(v.filename)}?download=1`
    : `${BASE_URL}/api/video/${encodeURIComponent(v.id)}/download`;

  return (
    <HStack gap={SP.xs} justify="flex-end">
      <Button
        {...smallBtn}
        variant="ghost"
        bg={P.blue50}
        color={P.blue}
        border="1px solid transparent"
        _hover={{ bg: P.blue100, borderColor: P.blue200 }}
        _focusVisible={FOCUS_BLUE}
        onClick={() => onPlay(v)}
      >
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
          color={P.orange700}
          bg="transparent"
          _hover={{ bg: P.orange50 }}
          _focusVisible={FOCUS_ORANGE}
          onClick={() => onDelete(v)}
          disabled={isDeleting}
        >
          {isDeleting ? <Spinner size="xs" color={P.orange} /> : "Delete"}
        </Button>
      )}
    </HStack>
  );
}

/* ============================================================================
 * 15. ROW — AnomalyRow
 * ==========================================================================*/

const AnomalyRow = React.memo(function AnomalyRow({
  v,
  name,
  limits,
  isDeleting,
  onPlay,
  onDelete,
}) {
  const type = v.is_buffer ? (v.is_anomaly ? "LONG" : "OK") : getAnomalyType(v);
  const expected = v.is_buffer ? [] : expectedList(v);
  const anomaly = type !== "OK" || expected.length > 0;
  const row = computeRowStyle(v);

  return (
    <Table.Row
      h={ROW_H}
      bg={row.bg}
      _hover={{ bg: row.hover }}
      boxShadow={row.accent ? `inset 3px 0 0 ${row.accent}` : "none"}
      opacity={isDeleting ? 0.5 : 1}
      transition="background-color 120ms ease, opacity 160ms ease"
    >
      <Table.Cell {...cellProps} color={P.ink500} style={num}>
        {fmtDate(v.recorded_at)}
      </Table.Cell>

      <Table.Cell {...cellProps} textAlign="center" color={P.black} style={num} fontWeight="600">
        {v.line}
      </Table.Cell>

      <Table.Cell {...cellProps} textAlign="center" color={P.black} style={num} fontWeight="600">
        {v.station_id}
      </Table.Cell>

      <Table.Cell
        {...cellProps}
        color={name ? P.black : P.ink400}
        fontFamily={SANS}
        fontWeight="500"
      >
        {name || "—"}
      </Table.Cell>

      <Table.Cell {...cellProps} textAlign="end" style={num}>
        <CycleTimeCell v={v} type={type} limits={limits} anomaly={anomaly} />
      </Table.Cell>

      <Table.Cell {...cellProps} textAlign="end" color={P.ink500} style={num}>
        {v.size_mb != null ? `${Number(v.size_mb).toFixed(1)} MB` : "—"}
      </Table.Cell>

      <Table.Cell {...cellProps}>
        <TypeBadgeStack v={v} type={type} anomaly={anomaly} expected={expected} />
      </Table.Cell>

      <Table.Cell {...cellProps}>
        <RowActions
          v={v}
          anomaly={anomaly}
          isDeleting={isDeleting}
          onPlay={onPlay}
          onDelete={onDelete}
        />
      </Table.Cell>
    </Table.Row>
  );
});

/* ============================================================================
 * 16. HEADER CELL — sortable
 * ==========================================================================*/

function ColumnHeader({ col, sort, onSort }) {
  const active = sort.key === col.sortKey;
  const clickable = Boolean(col.sortKey);
  const justify =
    col.align === "end"
      ? "flex-end"
      : col.align === "center"
      ? "center"
      : "flex-start";

  return (
    <Table.ColumnHeader
      textAlign={col.align}
      bg={P.ink50}
      color={active ? P.black : P.ink600}
      fontWeight="700"
      fontSize={FS.tiny}
      fontFamily={MONO}
      textTransform="uppercase"
      letterSpacing="0.06em"
      px={SP.lg}
      py={0}
      whiteSpace="nowrap"
      cursor={clickable ? "pointer" : "default"}
      userSelect="none"
      onClick={clickable ? () => onSort(col.sortKey) : undefined}
      aria-sort={
        active
          ? sort.dir === "asc"
            ? "ascending"
            : "descending"
          : undefined
      }
      borderBottom={`1px solid ${P.ink200}`}
      transition="color 120ms ease"
      _hover={clickable ? { color: P.blue } : undefined}
    >
      <HStack gap="3px" justify={justify}>
        <span>{col.label}</span>
        {active && (
          <Box as="span" fontSize="9px" color={P.blue} lineHeight="1" aria-hidden="true">
            {sort.dir === "asc" ? "▲" : "▼"}
          </Box>
        )}
      </HStack>
    </Table.ColumnHeader>
  );
}

/* ============================================================================
 * 17. HEADER — Title block
 * ==========================================================================*/

function PanelTitleBlock({ title, subtitle }) {
  return (
    <Box minW="0">
      <Text
        fontSize={FS.title}
        lineHeight="24px"
        fontWeight="700"
        color={P.black}
        fontFamily={SANS}
        letterSpacing="-0.01em"
      >
        {title}
      </Text>
      <Text
        fontSize={FS.tiny}
        lineHeight="15px"
        mt={SP.xs}
        color={P.ink500}
        fontFamily={MONO}
        textTransform="uppercase"
        letterSpacing="0.05em"
      >
        {subtitle}
      </Text>
    </Box>
  );
}

/* ============================================================================
 * 18. HEADER — Station selector
 * ==========================================================================*/

function StationSelector({ value, onChange, summary }) {
  return (
    <NativeSelect.Root w="233px" size="xs">
      <NativeSelect.Field
        aria-label="Station filter"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        h={BTN_H}
        fontSize={FS.cap}
        fontFamily={MONO}
        py={0}
        borderRadius={RADII.md}
        borderColor={P.ink200}
        bg={P.white}
        color={P.black}
        _focus={{ borderColor: P.blue, boxShadow: `0 0 0 1px ${P.blue}` }}
      >
        <option value="">All stations</option>
        {summary.map((s) => (
          <option key={`${s.line}-${s.station_id}`} value={`${s.line}-${s.station_id}`}>
            L{s.line} ST{s.station_id} — {s.station_name}
          </option>
        ))}
      </NativeSelect.Field>
      <NativeSelect.Indicator color={P.ink500} />
    </NativeSelect.Root>
  );
}

/* ============================================================================
 * 19. HEADER — Time range cluster
 * ==========================================================================*/

function TimeRangeFilter({ range, setRange }) {
  const todayActive = !range.from && !range.to;
  return (
    <RowCluster align="center" aria-label="Time range">
      <Button
        {...smallBtn}
        variant="ghost"
        bg={todayActive ? P.blue : "transparent"}
        color={todayActive ? P.white : P.ink600}
        border="1px solid"
        borderColor={todayActive ? P.blue : "transparent"}
        _hover={
          todayActive
            ? { bg: P.blueHover }
            : { bg: P.ink100, color: P.ink800 }
        }
        _focusVisible={FOCUS_BLUE}
        onClick={() => setRange({ from: "", to: "" })}
      >
        Today
      </Button>

      <Input
        type="datetime-local"
        h={BTN_H}
        w="165px"
        fontSize={FS.cap}
        fontFamily={MONO}
        bg={P.white}
        borderRadius={RADII.xs}
        borderColor={P.ink200}
        color={P.black}
        aria-label="From"
        value={range.from}
        onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))}
        _focus={{ borderColor: P.blue, boxShadow: `0 0 0 1px ${P.blue}` }}
      />

      <Text fontSize={FS.tiny} color={P.ink400} fontFamily={MONO} userSelect="none">
        →
      </Text>

      <Input
        type="datetime-local"
        h={BTN_H}
        w="165px"
        fontSize={FS.cap}
        fontFamily={MONO}
        bg={P.white}
        borderRadius={RADII.xs}
        borderColor={P.ink200}
        color={P.black}
        aria-label="To"
        value={range.to}
        onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))}
        _focus={{ borderColor: P.blue, boxShadow: `0 0 0 1px ${P.blue}` }}
      />
    </RowCluster>
  );
}

/* ============================================================================
 * 20. HEADER — Filter bar
 * ==========================================================================*/

function FilterBar({
  filters,
  toggleFilter,
  range,
  setRange,
  parentControlled,
  filterStation,
  setFilterStation,
  summary,
  loading,
  onRefresh,
}) {
  return (
    <HStack gap={SP.md} wrap="wrap">
      {!parentControlled && (
        <StationSelector
          value={filterStation}
          onChange={setFilterStation}
          summary={summary}
        />
      )}

      <HStack gap={SP.md} align="center" wrap="wrap">
        {/* Time range cluster */}
        <TimeRangeFilter range={range} setRange={setRange} />

        {/* Anomaly cluster (2-row: Anomaly / Long+Short) */}
        <ColumnCluster aria-label="Anomaly filter">
          <FilterButton
            filter={FILTERS.anomaly}
            active={filters.includes("anomaly")}
            onSelect={toggleFilter}
          />
          <HStack gap={SP.xs}>
            {["long", "short"].map((k) => (
              <FilterButton
                key={k}
                filter={FILTERS[k]}
                active={filters.includes(k)}
                onSelect={toggleFilter}
                flex="1"
              />
            ))}
          </HStack>
        </ColumnCluster>

        {/* Normal cluster */}
        <RowCluster aria-label="Normal filter">
          <FilterButton
            filter={FILTERS.normal}
            active={filters.includes("normal")}
            onSelect={toggleFilter}
            px={SP.lg}
          />
        </RowCluster>

        {/* Marker cluster */}
        <RowCluster aria-label="Marker filter">
          {["expected", "genesis", "late"].map((k) => (
            <FilterButton
              key={k}
              filter={FILTERS[k]}
              active={filters.includes(k)}
              onSelect={toggleFilter}
              px={SP.lg}
            />
          ))}
        </RowCluster>

        {/* All cluster */}
        <RowCluster aria-label="Show all">
          <FilterButton
            filter={FILTERS.all}
            active={filters.length === 0}
            onSelect={toggleFilter}
            px={SP.lg}
          />
        </RowCluster>
      </HStack>

      <Button
        {...smallBtn}
        variant="outline"
        onClick={onRefresh}
        disabled={loading}
        borderColor={P.ink200}
        color={P.ink600}
        bg={P.white}
        _hover={{ bg: P.blue50, borderColor: P.blue200, color: P.blue }}
        _focusVisible={FOCUS_BLUE}
      >
        {loading ? <Spinner size="xs" color={P.blue} /> : "Refresh"}
      </Button>
    </HStack>
  );
}

/* ============================================================================
 * 21. TABLE — state rows
 * ==========================================================================*/

function TableStateRow({ children }) {
  return (
    <Table.Row>
      <Table.Cell colSpan={COLUMNS.length} p={0} border="none">
        <Flex
          h="144px"
          direction="column"
          align="center"
          justify="center"
          gap={SP.md}
          bg={P.ink50}
        >
          {children}
        </Flex>
      </Table.Cell>
    </Table.Row>
  );
}

/* ============================================================================
 * 22. TABLE — body (loading / error / empty / rows)
 * ==========================================================================*/

function TableBody({ loading, error, visible, shown, onRetry, renderRow }) {
  if (loading && visible.length === 0)
    return (
      <TableStateRow>
        <Spinner size="sm" color={P.blue} />
      </TableStateRow>
    );

  if (error)
    return (
      <TableStateRow>
        <Text
          fontSize={FS.cap}
          color={P.orange700}
          fontFamily={MONO}
          textAlign="center"
          maxW="377px"
        >
          {error}
        </Text>
        <Button
          {...smallBtn}
          variant="outline"
          borderColor={P.orange200}
          color={P.orange700}
          bg={P.white}
          _hover={{ bg: P.orange50, borderColor: P.orange }}
          onClick={onRetry}
        >
          Retry
        </Button>
      </TableStateRow>
    );

  if (visible.length === 0)
    return (
      <TableStateRow>
        <Text fontSize={FS.cap} color={P.ink500} fontFamily={MONO}>
          No videos saved yet.
        </Text>
      </TableStateRow>
    );

  return <>{shown.map(renderRow)}</>;
}

/* ============================================================================
 * 23. FOOTER — Pagination
 * ==========================================================================*/

function Pagination({
  shownCount,
  visibleCount,
  dbLoaded,
  total,
  loadingMore,
  onLoadMore,
  pageSize,
}) {
  const showMore = visibleCount >= shownCount && dbLoaded < total;
  const remaining = Math.min(pageSize, total - dbLoaded);

  return (
    <Flex
      px={SP.xl}
      py={SP.md}
      justify="space-between"
      align="center"
      borderTopWidth="1px"
      borderColor={P.ink200}
      bg={P.ink50}
      gap={SP.md}
      wrap="wrap"
    >
      <Text fontSize={FS.tiny} color={P.ink500} fontFamily={MONO}>
        Showing {shownCount} of {visibleCount} loaded
        {total > dbLoaded ? ` (total ${total})` : ""}
      </Text>

      {showMore && (
        <Button
          {...smallBtn}
          variant="outline"
          onClick={onLoadMore}
          disabled={loadingMore}
          borderColor={P.ink200}
          color={P.blue}
          bg={P.white}
          _hover={{ bg: P.blue50, borderColor: P.blue200 }}
          _focusVisible={FOCUS_BLUE}
        >
          {loadingMore ? (
            <Spinner size="xs" color={P.blue} />
          ) : (
            `Load next ${remaining}`
          )}
        </Button>
      )}
    </Flex>
  );
}

/* ============================================================================
 * 24. MAIN COMPONENT — AnomalyList
 * ==========================================================================*/

function AnomalyList({ summary = EMPTY_SUMMARY, line = "", stationId = "" }) {
  const { isAdmin } = useAuth();

  /* ── state ─────────────────────────────────────────────────────────── */
  const [videos, setVideos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(null);
  const [selectedVideo, setSelectedVideo] = useState(null);
  const [filterStation, setFilterStation] = useState("");
  const [filters, setFilters] = useState(["anomaly"]);
  const [range, setRange] = useState({ from: "", to: "" });
  const [sort, setSort] = useState({ key: "time", dir: "desc" });
  const [total, setTotal] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [renderCount, setRenderCount] = useState(PAGE_SIZE);
  const [page, setPage] = useState(1);

  /* ── filter toggle ─────────────────────────────────────────────────── */
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
        return base.includes("anomaly")
          ? [...base, key]
          : [...base, "anomaly", key];
      }

      if (key === "normal") {
        if (on) return prev.filter((k) => k !== "normal");
        return [
          ...prev.filter((k) => !ANOMALY_KEYS.includes(k) && k !== "expected"),
          "normal",
        ];
      }

      return on ? prev.filter((k) => k !== key) : [...prev, key];
    });
  }, []);

  /* ── sort ──────────────────────────────────────────────────────────── */
  const onSort = (key) =>
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "desc" ? "asc" : "desc" }
        : { key, dir: key === "time" ? "desc" : "asc" }
    );

  /* ── server filters mapping ────────────────────────────────────────── */
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

  /* ── base params ───────────────────────────────────────────────────── */
  const parentControlled =
    (line !== "" && line != null) || (stationId !== "" && stationId != null);

  const baseParams = useMemo(() => {
    let qLine = line,
      qStation = stationId;
    if (!parentControlled && filterStation)
      [qLine, qStation] = filterStation.split("-");
    const p = { ...serverFilters, sort: sort.key, dir: sort.dir, limit: FETCH_LIMIT };
    if (range.from) p.from = wib(range.from);
    if (range.to) p.to = wib(range.to);
    if (qLine !== "" && qLine != null) p.line = qLine;
    if (qStation !== "" && qStation != null) p.station_id = qStation;
    return p;
  }, [serverFilters, sort, filterStation, line, stationId, parentControlled, range]);

  useEffect(() => setPage(1), [baseParams]);

  const queryParams = useMemo(
    () => ({ ...baseParams, offset: (page - 1) * PAGE_SIZE }),
    [baseParams, page]
  );

  const reqId = useRef(0);
  const summaryRef = useRef(summary);
  summaryRef.current = summary;

  /* ── fetch ─────────────────────────────────────────────────────────── */
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
      console.error("Failed to fetch videos:", err.message);
      setVideos([]);
      setTotal(0);
      setError(
        err.response?.data?.error ||
          "Unable to load video list. Check the connection to the server."
      );
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  }, [queryParams]);

  const dbLoaded = useMemo(
    () => videos.filter((v) => !v.is_buffer).length,
    [videos]
  );

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
      alert("Failed to load data: " + (e.response?.data?.error || e.message));
    } finally {
      setLoadingMore(false);
    }
  };

  useEffect(() => {
    loadVideos();
  }, [loadVideos]);

  /* ── delete ────────────────────────────────────────────────────────── */
  const handleDelete = useCallback(async (video) => {
    if (
      !window.confirm(
        `Delete this anomaly video?\n\n${video.filename}\n\nThis action cannot be undone.`
      )
    )
      return;
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

  /* ── lookup maps ───────────────────────────────────────────────────── */
  const stationNames = useMemo(() => {
    const map = new Map();
    summary.forEach((s) => {
      map.set(
        `${Number(s.line)}-${Number(s.station_id)}`,
        s.station_name || s.name || ""
      );
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

  /* ── client-side filtering ─────────────────────────────────────────── */
  const visible = useMemo(() => {
    const subs = filters.filter((k) => k === "long" || k === "short");
    const types = filters.filter(
      (k) => TYPE_KEYS.includes(k) && !(k === "anomaly" && subs.length)
    );
    const flags = filters.filter((k) => FLAG_KEYS.includes(k));
    return videos.filter((v) => {
      if (types.length && !types.some((k) => matchType(v, k))) return false;
      if (flags.includes("genesis") && !v.is_genesis) return false;
      if (flags.includes("expected") && (v.is_buffer || !hasExpected(v)))
        return false;
      if (flags.includes("late") && (v.is_buffer || !v.is_buffered)) return false;
      return true;
    });
  }, [videos, filters]);

  const bufferCount = useMemo(
    () => visible.filter((v) => v.is_buffer).length,
    [visible]
  );

  useEffect(() => setRenderCount(PAGE_SIZE), [queryParams]);

  const shown = useMemo(
    () => visible.slice(0, renderCount),
    [visible, renderCount]
  );

  const onTableScroll = (e) => {
    const el = e.currentTarget;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 120)
      setRenderCount((c) => (c < visible.length ? c + PAGE_SIZE : c));
  };

  /* ── header text ───────────────────────────────────────────────────── */
  const filterText = filters.length
    ? filters.map((k) => FILTERS[k].label).join(" + ")
    : "";
const scope = parentControlled ? ` · L${line} ST${stationId}` : "";
const title = (filters.length ? `${filterText} Videos` : "All Videos") + scope;
  const rangeText =
    range.from || range.to
      ? ` · ${range.from ? range.from.replace("T", " ") : "start"} → ${
          range.to ? range.to.replace("T", " ") : "now"
        }`
      : " · today";
  const subtitle =
    `${shown.length} of ${total + bufferCount} videos` +
    (bufferCount ? ` · ${bufferCount} buffer` : "") +
    rangeText;

  /* ── render ────────────────────────────────────────────────────────── */
  return (
    <Box
      bg={P.white}
      borderWidth="1px"
      borderColor={P.ink200}
      borderRadius={RADII.lg}
      overflow="hidden"
      mt={SP.lg}
    >
      {/* HEADER */}
      <Flex
        px={SP.xl}
        py={SP.lg}
        align="center"
        justify="space-between"
        gap={SP.lg}
        wrap="wrap"
        borderBottomWidth="1px"
        borderColor={P.ink200}
      >
        <PanelTitleBlock title={title} subtitle={subtitle} />
        <FilterBar
          filters={filters}
          toggleFilter={toggleFilter}
          range={range}
          setRange={setRange}
          parentControlled={parentControlled}
          filterStation={filterStation}
          setFilterStation={setFilterStation}
          summary={summary}
          loading={loading}
          onRefresh={loadVideos}
        />
      </Flex>

      {/* TABLE */}
      <Box overflow="auto" maxH={TABLE_MAX_H} onScroll={onTableScroll}>
        <Table.Root size="sm" stickyHeader interactive>
          <Table.Header>
            <Table.Row bg={P.ink50} h={ROW_H}>
              {COLUMNS.map((c) => (
                <ColumnHeader key={c.label} col={c} sort={sort} onSort={onSort} />
              ))}
            </Table.Row>
          </Table.Header>

          <Table.Body>
            <TableBody
              loading={loading}
              error={error}
              visible={visible}
              shown={shown}
              onRetry={loadVideos}
              renderRow={(v) => (
                <AnomalyRow
                  key={`${v.is_buffer ? "b" : "d"}-${v.line}-${v.station_id}-${v.id}`}
                  v={v}
                  name={stationNames.get(
                    `${Number(v.line)}-${Number(v.station_id)}`
                  )}
                  limits={stationLimits.get(
                    `${Number(v.line)}-${Number(v.station_id)}`
                  )}
                  isDeleting={deleting === v.id}
                  onPlay={setSelectedVideo}
                  onDelete={isAdmin ? handleDelete : undefined}
                />
              )}
            />
          </Table.Body>
        </Table.Root>
      </Box>

      {/* PAGINATION */}
      {(renderCount < visible.length || dbLoaded < total) && (
        <Pagination
          shownCount={shown.length}
          visibleCount={visible.length}
          dbLoaded={dbLoaded}
          total={total}
          loadingMore={loadingMore}
          onLoadMore={loadMore}
          pageSize={FETCH_LIMIT}
        />
      )}

      {/* VIDEO PLAYER */}
      {selectedVideo && (
        <VideoPlayer
          video={selectedVideo}
          onClose={() => setSelectedVideo(null)}
        />
      )}
    </Box>
  );
}

export default React.memo(AnomalyList);