/**
 * StationDetail.jsx
 * Panel detail satu station: pengaturan (takt, ROI timeout), filter,
 * ringkasan statistik, grafik cycle time, dan riwayat cycle.
 */

import React, { useEffect, useState, useCallback, useRef } from "react";

import axios from "axios";
import {
  Box,
  Flex,
  Grid,
  Text,
  Button,
  Input,
  SimpleGrid,
  HStack,
  Badge,
  Table,
  Spinner,
  NativeSelect,
} from "@chakra-ui/react";
  import { ANOMALY_STYLE, getAnomalyType, expectedList, getRowUi } from "../hooks/useSocket";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";

import { BASE_URL } from "../config";

/*
 * Skala golden ratio (φ ≈ 1.618) dengan deret Fibonacci, sama dengan AnomalyList.
 *   Jarak/ukuran : 3 · 5 · 8 · 13 · 21 · 34
 *   Teks         : 11 · 13 · 17 · 21 (angka statistik)
 *   Baris tabel 34px, tombol/input 21px, tinggi grafik dan riwayat 233px.
 *   Grafik : riwayat = 1.618 : 1
 */
const SP = { xs: "3px", sm: "5px", md: "8px", lg: "13px", xl: "21px" };
const FS = { cap: "11px", body: "13px", title: "17px", stat: "21px" };
const ROW_H = "34px";
const BTN_H = "21px";
const PANEL_H = "233px";
const SPLIT = "1.618fr 1fr";

// Warna grafik (setara token Chakra)
const C = {
  line: "#3b82f6",
  anomaly: "#ef4444",
  grid: "#f4f4f5",
  takt: "#a1a1aa",
  axis: "#71717a",
  skipped: "#eab308",
};

const num = { fontVariantNumeric: "tabular-nums" };

/* =========================================================
   HELPER WAKTU (WIB)
   ========================================================= */

const WIB = 7 * 3600 * 1000;

const todayWIB = () => new Date(Date.now() + WIB).toISOString().slice(0, 10);

const daysAgoWIB = (n) =>
  new Date(Date.now() + WIB - n * 86400000).toISOString().slice(0, 10);

// tanggal WIB → parameter API. `to` eksklusif, jadi tambah 1 hari.
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
    return d.toLocaleTimeString("id-ID", {
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
   HOOK: edit angka inline (dipakai Takt dan ROI timeout)
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
      setError(`${label} harus diisi.`);
      return;
    }
    if (!Number.isFinite(value) || value < 0 || (!allowZero && value === 0)) {
      setError(
        allowZero
          ? `${label} harus angka 0 atau lebih (0 = nonaktif).`
          : `${label} harus berupa angka lebih besar dari 0.`
      );
      return;
    }

    setSaving(true);
    try {
      await save(value);
      setSuccess(`${label} berhasil diperbarui.`);
      setEditing(false);
      setInput("");
    } catch (err) {
      console.error(`Gagal update ${label}:`, err);
      setError(
        err.response?.data?.error ||
        err.response?.data?.message ||
        err.message ||
        `Gagal memperbarui ${label.toLowerCase()}.`
      );
    } finally {
      setSaving(false);
    }
  };

  // Pesan sukses hilang sendiri
  useEffect(() => {
    if (!success) return undefined;
    const t = setTimeout(() => setSuccess(""), 4000);
    return () => clearTimeout(t);
  }, [success]);

  return { editing, input, setInput, saving, error, success, start, cancel, submit, reset };
}

/* =========================================================
   KOMPONEN KECIL
   ========================================================= */

const smallBtn = {
  h: BTN_H,
  minW: 0,
  px: SP.md,
  fontSize: FS.cap,
  fontWeight: "500",
};

function InlineSetting({ label, valueText, edit, extra }) {
  return (
    <HStack gap={SP.md} wrap="wrap">
      <Text fontSize={FS.cap} color="gray.500">
        {label}
      </Text>

      {!edit.editing ? (
        <>
          <Text fontSize={FS.body} fontWeight="600" color="gray.800" style={num}>
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
            w="89px"
            h={BTN_H}
            px={SP.md}
            fontSize={FS.body}
            style={num}
            borderColor={edit.error ? "red.400" : "blue.400"}
          />
          <Text fontSize={FS.cap} color="gray.500">
            s
          </Text>
          <Button
            {...smallBtn}
            colorPalette="green"
            onClick={edit.submit}
            disabled={edit.saving}
          >
            {edit.saving ? <Spinner size="xs" /> : "Simpan"}
          </Button>
          <Button {...smallBtn} variant="outline" onClick={edit.cancel} disabled={edit.saving}>
            Batal
          </Button>
        </>
      )}
    </HStack>
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
    fontSize: FS.cap,
  };

  return (
    <Flex gap={SP.md} wrap="wrap" align="center">
      <NativeSelect.Root w="189px" size="xs">
        <NativeSelect.Field
          aria-label="Filter shift"
          value={filters.shift || ""}
          onChange={(e) =>
            onChange({ ...filters, shift: e.target.value, from: "", to: "" })
          }
          h={BTN_H}
          fontSize={FS.cap}
          py={0}
        >
          <option value="">Semua shift</option>
          <option value="1">Shift 1 (19.00-07.00)</option>
          <option value="2">Shift 2 (07.00-19.00)</option>
        </NativeSelect.Field>
        <NativeSelect.Indicator />
      </NativeSelect.Root>

      <Input
        {...dateProps}
        aria-label="Dari tanggal"
        value={filters.from || ""}
        max={filters.to || todayWIB()}
        onChange={(e) => onChange({ ...filters, from: e.target.value, shift: "" })}
      />

      <Text fontSize={FS.cap} color="gray.400">
        s/d
      </Text>

      <Input
        {...dateProps}
        aria-label="Sampai tanggal"
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
          color="gray.500"
          onClick={() => onChange({ shift: "", from: "", to: "" })}
        >
          Reset
        </Button>
      </HStack>
    </Flex>
  );
}

/* Sel statistik: dipisah garis tipis, bukan kartu terpisah */
function StatCell({ label, value, sub, color = "gray.800" }) {
  return (
    <Box
      px={SP.lg}
      py={SP.lg}
      borderRightWidth="1px"
      borderBottomWidth="1px"
      borderColor="gray.200"
      // margin negatif + overflow hidden di induk menyembunyikan garis tepi luar
      mr="-1px"
      mb="-1px"
      minW="0"
    >
      <Text fontSize={FS.cap} color="gray.500" lineHeight="13px" mb={SP.sm}>
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
        <Text fontSize={FS.cap} color="gray.500" lineHeight="13px" mt={SP.sm}>
          {sub}
        </Text>
      )}
    </Box>
  );
}

function Panel({ title, meta, children }) {
  return (
    <Box minW="0">
      <Flex align="baseline" justify="space-between" mb={SP.md} gap={SP.md}>
        <Text fontSize={FS.body} fontWeight="600" color="gray.800">
          {title}
        </Text>
        {meta && (
          <Text fontSize={FS.cap} color="gray.500">
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
      <Text fontSize={FS.body} color="gray.500" textAlign="center">
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

  const [filters, setFilters] = useState({
    shift: "",
    from: todayWIB(),
    to: todayWIB(),
  });

  /* ---------- pengaturan station ---------- */

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
/* ---------- God Mode / Saving Mode (endpoint BE: saving-mode) ---------- */
const savingMode = station?.saving === true;   // God Mode = tidak saving
const [modeBusy, setModeBusy] = useState(false);
const [waitingGod, setWaitingGod] = useState(false);
const [modeError, setModeError] = useState("");

const setMode = async (toSaving) => {
  if (
    toSaving &&
    !window.confirm(
      `Aktifkan Saving Mode untuk L${line} ST${stationId}?\nHeartbeat berhenti dan live stream diputus.`
    )
  )
    return;

  setModeBusy(true);
  setModeError("");
  try {
    await axios.put(`${BASE_URL}/api/station/${line}/${stationId}/saving-mode`, {
      enabled: toSaving,
    });
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
  setModeBusy(false);
  setWaitingGod(false);
  setModeError("");
}, [stationKey]);

  useEffect(() => {
    setMinLocal(null);
  }, [station?.min_cycle_sec]);
  useEffect(() => {
    setShortLocal(null);
  }, [station?.short_anomaly_sec]);
  // Nilai dari server sudah berubah: pakai itu lagi
  useEffect(() => {
    setRoiLocal(null);
  }, [station?.roi_timeout_sec]);
  /* ---------- muat data ---------- */

  const loadAll = useCallback(
    async (flt) => {
      if (stationId == null) return;

      const id = ++reqId.current;
      setLoading(true);

      try {
        const q = toApiRange(flt);

        const [hist, st] = await Promise.all([
          fetchHistory(line, stationId, 100, q),
          fetchStats(line, stationId, q),
        ]);

        if (id !== reqId.current) return; // abaikan respons lama

        setHistory(hist.slice(-100));
        setStats(st);
      } catch (e) {
        if (id !== reqId.current) return;
        console.error("Gagal memuat data station:", e);
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

  /* ---------- cycle realtime ---------- */

  useEffect(() => {
    const l = recentCycles.find(
      (c) => c.line === line && c.station_id === stationId
    );
    lastSeenRef.current = l?.end_timestamp ?? null;
    // eslint-disable-next-line
  }, [stationKey]);

  useEffect(() => {
    const latest = recentCycles.find(
      (c) => c.line === line && c.station_id === stationId
    );

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
        avg_ct:
          prev.avg_ct != null ? (prev.avg_ct * (total - 1) + ct) / total : ct,
        min_ct: prev.min_ct != null ? Math.min(prev.min_ct, ct) : ct,
        max_ct: prev.max_ct != null ? Math.max(prev.max_ct, ct) : ct,
      };
    });
  }, [recentCycles, line, stationId, targetCT]);

  if (!station) return null;

  /* ---------- data turunan ---------- */

  const chartData = history.map((h, i) => ({
    idx: i + 1,
    ct: Number(h.cycle_time_sec?.toFixed(2)),
    time: tsToTime(h.end_timestamp),
    type: getAnomalyType(h),
    anomaly: h.is_anomaly ? Number(h.cycle_time_sec?.toFixed(2)) : null,
  }));

  const rows = [...history].reverse();

  // Sumbu Y selalu memuat garis takt dan target
  const refs = [takt, targetCT, Number(shortLimit) > 0 ? shortLimit : null]
    .filter((n) => n != null && Number.isFinite(Number(n)))
    .map(Number);
  const yDomain = [
    (min) => Math.max(0, Math.floor(Math.min(min, ...refs) * 0.85)),
    (max) => Math.ceil(Math.max(max, ...refs) * 1.1),
  ];
  const sameTarget = targetCT != null && Number(targetCT) === Number(takt);

  const eff = stats?.efficiency_pct;
  const effColor =
    eff == null ? "gray.800" : eff >= 90 ? "green.600" : eff >= 70 ? "orange.500" : "red.500";

  const longCount = stats?.long_count ?? stats?.over_takt_count ?? 0;
  const shortCount = stats?.short_count || 0;
  const skippedCount = stats?.expected_count || 0;
  const statItems = stats
    ? [
      { label: "Total cycles", value: stats.total_cycles || 0 },
      { label: "Avg CT", value: fmt(stats.avg_ct) },
      { label: "Min CT", value: fmt(stats.min_ct), color: "green.600" },
      { label: "Max CT", value: fmt(stats.max_ct), color: "red.500" },
      {
        label: "Std deviation",
        value: stats.stddev_ct != null ? `${Number(stats.stddev_ct).toFixed(2)}s` : "—",
      },
      {
        label: "Over target",
        value: longCount,
        color: longCount > 0 ? "red.500" : "gray.800",
      },
      {
        label: "Short",
        value: shortCount,
        color: shortCount > 0 ? "orange.500" : "gray.800",
      },
      {
        label: "Skipped",
        value: skippedCount,
        color: skippedCount > 0 ? "yellow.600" : "gray.800",
      },
      {
        label: "Efficiency",
        value: eff != null ? `${eff}%` : "—",
        color: effColor,
        sub: "cycle dalam target",
      },
    ]
    : [];

  const initialLoading = loading && !stats && history.length === 0;

  const messages = [
    { text: taktEdit.error, color: "red.500" },
    { text: roiEdit.error, color: "red.500" },
    { text: taktEdit.success, color: "green.600" },
    { text: roiEdit.success, color: "green.600" },
    { text: minEdit.error, color: "red.500" },
    { text: minEdit.success, color: "green.600" },
    { text: shortEdit.error, color: "red.500" },
    { text: shortEdit.success, color: "green.600" },
  ].filter((m) => m.text);

  return (
    <Box
      bg="white"
      borderWidth="1px"
      borderColor="gray.200"
      borderRadius={SP.md}
      overflow="hidden"
    >
      {/* ===================================================
          HEADER: judul, pengaturan, ekspor
          =================================================== */}
      <Box px={SP.xl} pt={SP.lg} pb={SP.lg}>
        <Flex justify="space-between" align="flex-start" gap={SP.lg} wrap="wrap">
          <Box minW="0">
            <Text fontSize={FS.title} lineHeight="21px" fontWeight="700" color="gray.900">
              {station.station_name || `Station ${stationId}`}
            </Text>
            <Text fontSize={FS.cap} lineHeight="13px" mt={SP.xs} color="gray.500">
              Line {line}, Station {stationId}
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

        <Flex mt={SP.lg} gap={`${SP.xl} ${SP.xl}`} wrap="wrap" align="center">
          <InlineSetting
            label="Takt time"
            valueText={fmt(takt)}
            edit={taktEdit}
            extra={
              targetCT && Number(targetCT) !== Number(takt) ? (
                <HStack gap={SP.sm}>
                  <Text fontSize={FS.cap} color="gray.500">
                    Target CT
                  </Text>
                  <Text fontSize={FS.body} fontWeight="600" color="red.500" style={num}>
                    {fmt(targetCT)}
                  </Text>
                </HStack>
              ) : null
            }
          />
          <InlineSetting label="ROI timeout" valueText={fmt(roiTimeout)} edit={roiEdit} />

          <InlineSetting label="Min CT" valueText={fmt(minCycle)} edit={minEdit} />   {/* ← kembalikan */}

          <InlineSetting
            label="Short limit"
            valueText={Number(shortLimit) > 0 ? fmt(shortLimit) : "Nonaktif"}
            edit={shortEdit}
          />
        </Flex>

        {messages.map((m) => (
          <Text key={m.text} mt={SP.sm} fontSize={FS.cap} color={m.color}>
            {m.text}
          </Text>
        ))}
      </Box>

      {/* ===================================================
          TOOLBAR FILTER
          =================================================== */}
      <Box
        px={SP.xl}
        py={SP.md}
        bg="gray.50"
        borderTopWidth="1px"
        borderBottomWidth="1px"
        borderColor="gray.200"
      >
        <FilterBar filters={filters} onChange={handleFilter} />
      </Box>

      {/* ===================================================
          ISI
          =================================================== */}
      <Box p={SP.xl}>
        {initialLoading ? (
          <Flex justify="center" align="center" h="233px" gap={SP.lg}>
            <Spinner size="sm" color="gray.500" />
            <Text color="gray.500" fontSize={FS.body}>
              Memuat data...
            </Text>
          </Flex>
        ) : (
          <Box
            opacity={loading ? 0.55 : 1}
            transition="opacity 0.2s ease"
            pointerEvents={loading ? "none" : "auto"}
          >
            {/* ---------- STATISTIK ---------- */}
            {stats && (
              <Box
                borderWidth="1px"
                borderColor="gray.200"
                borderRadius={SP.md}
                overflow="hidden"
                mb={SP.xl}
              >
                <SimpleGrid columns={{ base: 2, sm: 4, lg: 9 }} gap={0}>
                  {statItems.map((s) => (
                    <StatCell key={s.label} {...s} />
                  ))}
                </SimpleGrid>
              </Box>
            )}

            {/* ---------- GRAFIK : RIWAYAT = 1.618 : 1 ---------- */}
            <Grid templateColumns={{ base: "1fr", xl: SPLIT }} gap={SP.xl}>
              <Panel title="Cycle time" meta={`${history.length} Last Cycle`}>
                <Box
                  h={PANEL_H}
                  borderWidth="1px"
                  borderColor="gray.200"
                  borderRadius={SP.md}
                  py={SP.md}
                  pr={SP.md}
                >
                  {chartData.length === 0 ? (
                    <EmptyNote>Belum ada cycle pada rentang ini.</EmptyNote>
                  ) : (
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart
                        data={chartData}
                        margin={{ top: 4, right: 8, left: -16, bottom: 0 }}
                      >
                        <CartesianGrid strokeDasharray="3 3" stroke={C.grid} />

                        <XAxis
                          dataKey="idx"
                          tick={{ fontSize: 11, fill: C.axis }}
                          tickLine={false}
                          interval={
                            chartData.length <= 10 ? 0 : Math.floor(chartData.length / 10)
                          }
                        />

                        <YAxis
                          tick={{ fontSize: 11, fill: C.axis }}
                          tickLine={false}
                          axisLine={false}
                          unit="s"
                          domain={yDomain}
                          allowDecimals={false}
                        />

                        <Tooltip
                          contentStyle={{
                            fontSize: 12,
                            borderRadius: 8,
                            border: "1px solid #e4e4e7",
                            boxShadow: "none",
                          }}
                          formatter={(v) => [`${v}s`, "CT"]}
                          labelFormatter={(i) =>
                            `Cycle #${i}, ${chartData[i - 1]?.time || ""}`
                          }
                        />

                        {takt && (
                          <ReferenceLine
                            y={Number(takt)}
                            stroke={C.takt}
                            strokeDasharray="4 2"
                            label={{
                              value: sameTarget ? "Takt = Target" : "Takt",
                              position: "insideBottomRight",
                              fontSize: 11,
                              fill: C.takt,
                            }}
                          />
                        )}

                        {targetCT && !sameTarget && (
                          <ReferenceLine
                            y={Number(targetCT)}
                            stroke={C.anomaly}
                            strokeDasharray="4 2"
                            label={{
                              value: "Target",
                              position: "insideTopRight",
                              fontSize: 11,
                              fill: C.anomaly,
                            }}
                          />
                        )}
                        {Number(shortLimit) > 0 && (
                          <ReferenceLine
                            y={Number(shortLimit)}
                            stroke={ANOMALY_STYLE.SHORT.color}
                            strokeDasharray="4 2"
                            label={{ value: "Short", position: "insideBottomLeft", fontSize: 11, fill: ANOMALY_STYLE.SHORT.color }}
                          />
                        )}
                        <Line
                          type="monotone"
                          dataKey="ct"
                          stroke={C.line}
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
                                fill={d?.anomaly ? (d.type !== "OK" ? ANOMALY_STYLE[d.type].color : C.skipped) : C.line}
                              />
                            );
                          }}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  )}
                </Box>
              </Panel>

              {/* ---------- RIWAYAT ---------- */}
              <Panel title="Newest Cycle's History">
                <Box
                  h={PANEL_H}
                  overflow="auto"
                  borderWidth="1px"
                  borderColor="gray.200"
                  borderRadius={SP.md}
                >
                  {rows.length === 0 ? (
                    <EmptyNote>Belum ada cycle pada rentang ini.</EmptyNote>
                  ) : (
                    <Table.Root size="sm" stickyHeader interactive>
                      <Table.Header>
                        <Table.Row bg="gray.50" h={ROW_H}>
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
                              bg="gray.50"
                              color="gray.600"
                              fontWeight="600"
                              fontSize={FS.cap}
                              px={SP.lg}
                              py={0}
                              whiteSpace="nowrap"
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
                          const cell = { px: SP.lg, py: 0, fontSize: FS.body, verticalAlign: "middle", whiteSpace: "nowrap" };

                          return (
                            <Table.Row
                              key={`${row.end_timestamp}-${i}`}
                              h={ROW_H}
                              bg={ui.bg}
                              _hover={{ bg: ui.hover }}
                              boxShadow={ui.bar ? `inset 3px 0 0 var(--chakra-colors-${ui.bar}-500)` : "none"}
                            >
                              <Table.Cell {...cell} textAlign="end" color="gray.400" style={num}>
                                {history.length - i}
                              </Table.Cell>

                              <Table.Cell {...cell} color="gray.700" style={num}>
                                {tsToTime(row.end_timestamp)}
                              </Table.Cell>

                              <Table.Cell
                                {...cell}
                                textAlign="end"
                                color={anomaly ? ui.text : "gray.800"} fontWeight={anomaly ? "700" : "500"}
                                style={num}
                              >
                                {fmt(row.cycle_time_sec)}
                              </Table.Cell>

                              <Table.Cell {...cell} color="gray.600">
                                {row.operator_id || "—"}
                              </Table.Cell>

                              <Table.Cell {...cell}>
                                <HStack gap={SP.xs}>
                                  <Badge
                                    colorPalette={anomaly && type === "OK" ? "red" : ui.palette}
                                    variant="subtle" fontSize={FS.cap} h={BTN_H} px={SP.md} borderRadius={SP.sm}
                                  >
                                    {!anomaly ? "Normal" : type === "SHORT" ? "Short" : "Anomali"}
                                  </Badge>
                                  {skipped.length > 0 && (
                                    <Badge
                                      colorPalette="yellow" variant="subtle" fontSize={FS.cap} h={BTN_H} px={SP.md}
                                      borderRadius={SP.sm} maxW="144px" overflow="hidden" textOverflow="ellipsis"
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