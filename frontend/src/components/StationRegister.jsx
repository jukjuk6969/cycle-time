import React, { useCallback, useEffect, useState } from "react";
import axios from "axios";
import {
  Box, Flex, Text, Button, Input, SimpleGrid, HStack, VStack, Badge, Table,
} from "@chakra-ui/react";
import { BASE_URL } from "../config";

const MONO = `"JetBrains Mono", "SF Mono", ui-monospace, Consolas, monospace`;
const SANS = `"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;

const P = {
  blue: "var(--ct-blue)",
  blueHover: "var(--ct-blue-hover)",
  blue50: "var(--ct-blue-50)",
  blue100: "var(--ct-blue-100)",
  orange: "var(--ct-orange)",
  orangeHover: "var(--ct-orange-hover)",
  orange50: "var(--ct-orange-50)",
  orange100: "var(--ct-orange-100)",
  black: "var(--ct-black)",
  ink900: "var(--ct-black)",
  ink700: "var(--ct-ink-700)",
  ink600: "var(--ct-ink-600)",
  ink500: "var(--ct-ink-500)",
  ink400: "var(--ct-ink-400)",
  ink300: "var(--ct-ink-300)",
  ink200: "var(--ct-ink-200)",
  ink100: "var(--ct-ink-100)",
  ink50: "var(--ct-ink-50)",
  white: "var(--ct-surface)",
};

const SP = { xs: "3px", sm: "5px", md: "8px", lg: "13px", xl: "21px" };
const FS = { micro: "10px", tiny: "11px", cap: "13px", body: "14px", title: "17px" };
const RADII = { xs: "2px", sm: "3px", md: "4px", lg: "6px" };
const BTN_H = "21px";
const ROW_H = "34px";

const smallBtn = {
  h: BTN_H,
  minW: 0,
  px: SP.md,
  fontSize: FS.micro,
  fontWeight: "700",
  fontFamily: MONO,
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  borderRadius: RADII.sm,
};

const isOnline = (s) =>
  s.last_seen && Date.now() - new Date(s.last_seen).getTime() < 60000;

const EMPTY = {
  station_id: "", line: "", name: "", ip: "",
  takt_time: "", roi_timeout_sec: "", min_cycle_sec: "", short_anomaly_sec: "",
};

export default function StationRegister() {
  const [stations, setStations] = useState([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState(null);

  const loadStations = useCallback(async () => {
    try {
      const res = await axios.get(`${BASE_URL}/api/stations`);
      setStations(res.data);
    } catch (err) {
      console.error(err);
      setError("Failed to fetch station data.");
    }
  }, []);

  useEffect(() => {
    loadStations();
    const t = setInterval(loadStations, 15000);
    return () => clearInterval(t);
  }, [loadStations]);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  };

  const resetForm = () => {
    setForm(EMPTY);
    setEditing(null);
    setMessage("");
    setError("");
  };

  const startEdit = (s) => {
    setEditing({ line: s.line, station_id: s.station_id });
    setForm({
      station_id: String(s.station_id),
      line: String(s.line),
      name: s.name || "",
      ip: s.ip || "",
      takt_time: String(s.takt_time ?? ""),
      roi_timeout_sec: String(s.roi_timeout_sec ?? ""),
      min_cycle_sec: String(s.min_cycle_sec ?? ""),
      short_anomaly_sec: s.short_anomaly_sec > 0 ? String(s.short_anomaly_sec) : "",
    });
    setMessage("");
    setError("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const errText = (err, fallback) => err.response?.data?.error || fallback;

  const applyLimits = async (line, station_id) => {
    const base = `${BASE_URL}/api/station/${line}/${station_id}`;
    if (form.min_cycle_sec !== "")
      await axios.put(`${base}/min-cycle`, { min_cycle_sec: Number(form.min_cycle_sec) });
    if (form.short_anomaly_sec !== "" || editing)
      await axios.put(`${base}/short-anomaly`, {
        short_anomaly_sec: form.short_anomaly_sec === "" ? 0 : Number(form.short_anomaly_sec),
      });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setMessage("");
    setError("");

    const payload = {
      name: form.name.trim(),
      ip: form.ip.trim(),
      takt_time: Number(form.takt_time),
      roi_timeout_sec: Number(form.roi_timeout_sec),
      min_cycle_sec: form.min_cycle_sec === "" ? null : Number(form.min_cycle_sec),
    };

    try {
      if (editing) {
        await axios.put(`${BASE_URL}/api/station/${editing.line}/${editing.station_id}`, payload);
        await applyLimits(editing.line, editing.station_id);
        setMessage(`Station L${editing.line}-ST${editing.station_id} updated successfully.`);
      } else {
        await axios.post(`${BASE_URL}/api/station/register`, {
          ...payload,
          station_id: Number(form.station_id),
          line: Number(form.line),
        });
        await applyLimits(Number(form.line), Number(form.station_id));
        setMessage("Station registered successfully.");
      }
      resetForm();
      await loadStations();
    } catch (err) {
      console.error(err);
      setError(errText(err, editing ? "Failed to update station." : "Failed to register station."));
    } finally {
      setLoading(false);
    }
  };

  const toggleActive = async (s) => {
    setMessage("");
    setError("");
    try {
      await axios.put(`${BASE_URL}/api/station/${s.line}/${s.station_id}`, {
        is_active: !s.is_active,
      });
      setMessage(`Station L${s.line}-ST${s.station_id} ${s.is_active ? "deactivated" : "activated"}.`);
      await loadStations();
    } catch (err) {
      setError(errText(err, "Failed to change station status."));
    }
  };

  const handleDelete = async (s) => {
    const ok = window.confirm(
      `Permanently delete L${s.line}-ST${s.station_id} (${s.name})?\nThis action cannot be undone.`
    );
    if (!ok) return;

    setMessage("");
    setError("");
    try {
      await axios.delete(`${BASE_URL}/api/station/${s.line}/${s.station_id}`);
      setMessage(`Station L${s.line}-ST${s.station_id} deleted.`);
      if (editing && editing.line === s.line && editing.station_id === s.station_id) {
        resetForm();
      }
      await loadStations();
    } catch (err) {
      setError(errText(err, "Failed to delete station."));
    }
  };

  return (
    <Box minH="100vh" bg="var(--ct-canvas)" px={{ base: SP.lg, md: SP.xl }} py={SP.xl} fontFamily={SANS}>
      <Box maxW="1400px" mx="auto">
        
        {/* FORM SECTION */}
        <Box
          bg={P.white}
          borderWidth="1px"
          borderColor={editing ? P.orange : P.ink200}
          borderRadius={RADII.lg}
          p={SP.xl}
          mb={SP.xl}
        >
          <Text fontSize={FS.title} fontWeight="700" color={P.black} mb={SP.xs} letterSpacing="-0.01em">
            {editing ? `Edit Station L${editing.line}-ST${editing.station_id}` : "Register New Station"}
          </Text>

          <Text fontSize={FS.tiny} color={P.ink500} mb={SP.lg} lineHeight="1.5" fontFamily={MONO}>
            {editing
              ? "Line and Station ID are immutable identifiers. To change them, delete and re-register."
              : "Registration is managed by the server admin. FDs are automatically recognized by their registered IP."}
          </Text>

          <form onSubmit={handleSubmit}>
            <SimpleGrid columns={{ base: 1, sm: 2, lg: 4 }} gap={SP.lg}>
              <FormField label="Station ID">
                <Input
                  type="number" name="station_id" min="1" required
                  value={form.station_id} onChange={handleChange} placeholder="e.g. 11"
                  disabled={!!editing} bg={editing ? P.ink100 : P.white}
                  fontFamily={MONO} fontSize={FS.cap} borderRadius={RADII.md}
                  borderColor={P.ink200} h="32px"
                  _focus={{ borderColor: P.blue, boxShadow: `0 0 0 1px ${P.blue}` }}
                />
              </FormField>

              <FormField label="Line">
                <Input
                  type="number" name="line" min="1" required
                  value={form.line} onChange={handleChange} placeholder="e.g. 1"
                  disabled={!!editing} bg={editing ? P.ink100 : P.white}
                  fontFamily={MONO} fontSize={FS.cap} borderRadius={RADII.md}
                  borderColor={P.ink200} h="32px"
                  _focus={{ borderColor: P.blue, boxShadow: `0 0 0 1px ${P.blue}` }}
                />
              </FormField>

              <FormField label="Station Name">
                <Input
                  type="text" name="name" required
                  value={form.name} onChange={handleChange} placeholder="e.g. Final Assembly 01"
                  fontSize={FS.cap} borderRadius={RADII.md}
                  borderColor={P.ink200} h="32px"
                  _focus={{ borderColor: P.blue, boxShadow: `0 0 0 1px ${P.blue}` }}
                />
              </FormField>

              <FormField label="FD IP Address">
                <Input
                  type="text" name="ip" required
                  value={form.ip} onChange={handleChange} placeholder="e.g. 192.168.137.117"
                  fontFamily={MONO} fontSize={FS.cap} borderRadius={RADII.md}
                  borderColor={P.ink200} h="32px"
                  _focus={{ borderColor: P.blue, boxShadow: `0 0 0 1px ${P.blue}` }}
                />
              </FormField>

              <FormField label="Takt Time (sec)">
                <Input
                  type="number" name="takt_time" min="0.1" step="0.1" required
                  value={form.takt_time} onChange={handleChange} placeholder="e.g. 45"
                  fontFamily={MONO} fontSize={FS.cap} borderRadius={RADII.md}
                  borderColor={P.ink200} h="32px"
                  _focus={{ borderColor: P.blue, boxShadow: `0 0 0 1px ${P.blue}` }}
                />
              </FormField>

              <FormField label="ROI Timeout (sec)">
                <Input
                  type="number" name="roi_timeout_sec" min="0.1" step="0.1" required
                  value={form.roi_timeout_sec} onChange={handleChange} placeholder="e.g. 10"
                  fontFamily={MONO} fontSize={FS.cap} borderRadius={RADII.md}
                  borderColor={P.ink200} h="32px"
                  _focus={{ borderColor: P.blue, boxShadow: `0 0 0 1px ${P.blue}` }}
                />
              </FormField>
              
              <FormField label="Min Cycle Time (sec)">
                <Input
                  type="number" name="min_cycle_sec" min="0.1" step="0.1"
                  value={form.min_cycle_sec} onChange={handleChange} placeholder="Blank = default (3)"
                  fontFamily={MONO} fontSize={FS.cap} borderRadius={RADII.md}
                  borderColor={P.ink200} h="32px"
                  _focus={{ borderColor: P.blue, boxShadow: `0 0 0 1px ${P.blue}` }}
                />
              </FormField>
              
              <FormField label="Short Limit (sec)">
                <Input
                  type="number" name="short_anomaly_sec" min="0" step="0.1"
                  value={form.short_anomaly_sec} onChange={handleChange} placeholder="Blank / 0 = disabled"
                  fontFamily={MONO} fontSize={FS.cap} borderRadius={RADII.md}
                  borderColor={P.ink200} h="32px"
                  _focus={{ borderColor: P.blue, boxShadow: `0 0 0 1px ${P.blue}` }}
                />
              </FormField>
            </SimpleGrid>

            {message && (
              <Box mt={SP.lg} px={SP.md} py={SP.sm} borderRadius={RADII.md} bg={P.blue50} border="1px solid" borderColor={P.blue100}>
                <Text fontSize={FS.tiny} color={P.blue} fontFamily={MONO} fontWeight="500">
                  {message}
                </Text>
              </Box>
            )}

            {error && (
              <Box mt={SP.lg} px={SP.md} py={SP.sm} borderRadius={RADII.md} bg={P.orange50} border="1px solid" borderColor={P.orange100}>
                <Text fontSize={FS.tiny} color={P.orangeHover} fontFamily={MONO} fontWeight="500">
                  {error}
                </Text>
              </Box>
            )}

            <HStack mt={SP.lg} gap={SP.md}>
              <Button
                type="submit"
                h="32px" px={SP.xl} borderRadius={RADII.md}
                bg={loading ? P.ink300 : editing ? P.orange : P.blue}
                color={loading ? P.ink500 : "var(--ct-on-accent)"} fontFamily={MONO} fontSize={FS.tiny} fontWeight="700"
                textTransform="uppercase" letterSpacing="0.05em"
                _hover={{ bg: loading ? P.ink300 : editing ? P.orangeHover : P.blueHover }}
                disabled={loading}
              >
                {loading ? "Saving..." : editing ? "Save Changes" : "Register Station"}
              </Button>

              {editing && (
                <Button
                  type="button"
                  h="32px" px={SP.xl} variant="outline" borderRadius={RADII.md}
                  borderColor={P.ink200} color={P.ink600} fontFamily={MONO} fontSize={FS.tiny}
                  fontWeight="700" textTransform="uppercase" letterSpacing="0.05em"
                  onClick={resetForm} disabled={loading}
                  _hover={{ bg: P.ink100, borderColor: P.ink300 }}
                >
                  Cancel
                </Button>
              )}
            </HStack>
          </form>
        </Box>

        {/* LIST SECTION */}
        <Box bg={P.white} borderWidth="1px" borderColor={P.ink200} borderRadius={RADII.lg} overflow="hidden">
          <Flex px={SP.xl} py={SP.md} borderBottomWidth="1px" borderColor={P.ink200} align="center" justify="space-between">
            <Text fontSize={FS.body} fontWeight="700" color={P.black} fontFamily={SANS}>
              Registered Stations
            </Text>
            <Badge bg={P.ink100} color={P.ink700} variant="solid" borderRadius={RADII.md} px={SP.md} py={SP.xs} fontFamily={MONO} fontSize={FS.micro} fontWeight="700">
              {stations.length}
            </Badge>
          </Flex>

          {stations.length === 0 ? (
            <Box py={SP.xl} textAlign="center">
              <Text fontSize={FS.cap} color={P.ink500} fontFamily={MONO}>
                No stations registered.
              </Text>
            </Box>
          ) : (
            <Box overflowX="auto">
              <Table.Root size="sm">
                <Table.Header>
                  <Table.Row bg={P.ink50} borderBottom="1px solid" borderColor={P.ink200}>
                    {["Line", "Station ID", "Name", "IP", "Takt", "ROI Timeout", "Min CT", "Short", "Status", "Actions"].map((h) => (
                      <Table.ColumnHeader
                        key={h}
                        color={P.ink600}
                        fontWeight="700"
                        fontSize={FS.micro}
                        fontFamily={MONO}
                        textTransform="uppercase"
                        letterSpacing="0.05em"
                        px={SP.lg}
                        py={SP.md}
                        borderColor={P.ink200}
                        whiteSpace="nowrap"
                      >
                        {h}
                      </Table.ColumnHeader>
                    ))}
                  </Table.Row>
                </Table.Header>

                <Table.Body>
                  {stations.map((s) => {
                    const online = isOnline(s);
                    const isEditingRow = editing && editing.line === s.line && editing.station_id === s.station_id;
                    const cellProps = {
                      px: SP.lg, py: 0, h: ROW_H, borderColor: P.ink200,
                      fontFamily: MONO, fontSize: FS.tiny, verticalAlign: "middle"
                    };

                    return (
                      <Table.Row
                        key={`${s.line}-${s.station_id}`}
                        opacity={s.is_active ? 1 : 0.6}
                        bg={isEditingRow ? P.blue50 : P.white}
                        _hover={{ bg: isEditingRow ? P.blue50 : P.ink50 }}
                        borderBottom="1px solid"
                        borderColor={P.ink200}
                        transition="background-color 120ms ease"
                      >
                        <Table.Cell {...cellProps} color={P.black} fontWeight="600">
                          {s.line}
                        </Table.Cell>
                        <Table.Cell {...cellProps} color={P.black} fontWeight="600">
                          {s.station_id}
                        </Table.Cell>
                        <Table.Cell {...cellProps} fontFamily={SANS} color={P.black} fontWeight="500">
                          {s.name}
                        </Table.Cell>
                        <Table.Cell {...cellProps} color={P.ink500}>
                          {s.ip}
                        </Table.Cell>
                        <Table.Cell {...cellProps} color={P.black}>
                          {s.takt_time}s
                        </Table.Cell>
                        <Table.Cell {...cellProps} color={P.black}>
                          {s.roi_timeout_sec}s
                        </Table.Cell>
                        <Table.Cell {...cellProps} color={P.black}>
                          {s.min_cycle_sec != null ? `${s.min_cycle_sec}s` : "—"}
                        </Table.Cell>
                        <Table.Cell {...cellProps} color={P.black}>
                          {s.short_anomaly_sec > 0 ? `${s.short_anomaly_sec}s` : "—"}
                        </Table.Cell>
                        
                        <Table.Cell {...cellProps}>
                          <HStack gap={SP.sm}>
                            <Box
                              w="6px" h="6px" borderRadius="full"
                              bg={online ? P.blue : P.ink400}
                              boxShadow={online ? `0 0 6px ${P.blue}` : "none"}
                            />
                            <Text
                              fontSize={FS.micro} fontWeight="700"
                              color={online ? P.blue : P.ink500}
                              fontFamily={MONO} textTransform="uppercase" letterSpacing="0.05em"
                            >
                              {online ? "Online" : "Offline"}
                            </Text>
                            {!s.is_active && (
                              <Badge bg={P.orange100} color={P.orangeHover} variant="solid" fontSize={FS.micro} fontFamily={MONO} textTransform="uppercase" borderRadius={RADII.xs} ml={SP.xs} fontWeight="700">
                                Inactive
                              </Badge>
                            )}
                          </HStack>
                        </Table.Cell>

                        <Table.Cell {...cellProps}>
                          <HStack gap={SP.xs}>
                            <Button
                              {...smallBtn} variant="outline" color={P.blue} borderColor={P.blue100}
                              _hover={{ bg: P.blue50, borderColor: P.blue200 }}
                              onClick={() => startEdit(s)}
                            >
                              Edit
                            </Button>

                            <Button
                              {...smallBtn} variant="outline" color={P.ink600} borderColor={P.ink200}
                              _hover={{ bg: P.ink100, borderColor: P.ink300 }}
                              onClick={() => toggleActive(s)}
                            >
                              {s.is_active ? "Deactivate" : "Activate"}
                            </Button>

                            <Button
                              {...smallBtn} variant="outline" color={P.orangeHover} borderColor={P.orange100}
                              _hover={{ bg: P.orange50, borderColor: P.orange }}
                              onClick={() => handleDelete(s)}
                            >
                              Delete
                            </Button>
                          </HStack>
                        </Table.Cell>
                      </Table.Row>
                    );
                  })}
                </Table.Body>
              </Table.Root>
            </Box>
          )}
        </Box>
      </Box>
    </Box>
  );
}

function FormField({ label, children }) {
  return (
    <VStack align="stretch" gap={SP.xs}>
      <Text
        fontSize={FS.micro}
        fontWeight="700"
        color={P.ink600}
        fontFamily={MONO}
        textTransform="uppercase"
        letterSpacing="0.05em"
      >
        {label}
      </Text>
      {children}
    </VStack>
  );
}