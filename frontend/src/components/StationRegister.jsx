import React, { useCallback, useEffect, useState } from "react";
import axios from "axios";
import {
    Box,
    Flex,
    Text,
    Button,
    Input,
    SimpleGrid,
    HStack,
    VStack,
    Badge,
    Table,
} from "@chakra-ui/react";
import { BASE_URL } from "../config";

const isOnline = (s) =>
    s.last_seen &&
    Date.now() - new Date(s.last_seen).getTime() < 60_000;

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
            setError("Gagal mengambil data station.");
        }
    }, []);

    useEffect(() => {
        loadStations();

        const t = setInterval(loadStations, 15000);

        return () => clearInterval(t);
    }, [loadStations]);

    const handleChange = (e) => {
        const { name, value } = e.target;

        setForm((prev) => ({
            ...prev,
            [name]: value,
        }));
    };

    const resetForm = () => {
        setForm(EMPTY);
        setEditing(null);
    };

    const startEdit = (s) => {
        setEditing({
            line: s.line,
            station_id: s.station_id,
        });

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

        window.scrollTo({
            top: 0,
            behavior: "smooth",
        });
    };

    const errText = (err, fallback) =>
        err.response?.data?.error || fallback;
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
                setMessage(
                    `Station L${editing.line}-ST${editing.station_id} berhasil diperbarui.`
                );
            } else {
                await axios.post(`${BASE_URL}/api/station/register`, {
                    ...payload,
                    station_id: Number(form.station_id),
                    line: Number(form.line),
                });
                await applyLimits(Number(form.line), Number(form.station_id));

                setMessage("Station berhasil didaftarkan.");
            }

            resetForm();
            await loadStations();
        } catch (err) {
            console.error(err);

            setError(
                errText(
                    err,
                    editing
                        ? "Gagal memperbarui station."
                        : "Gagal mendaftarkan station."
                )
            );
        } finally {
            setLoading(false);
        }
    };

    const toggleActive = async (s) => {
        setMessage("");
        setError("");

        try {
            await axios.put(
                `${BASE_URL}/api/station/${s.line}/${s.station_id}`,
                {
                    is_active: !s.is_active,
                }
            );

            setMessage(
                `Station L${s.line}-ST${s.station_id} ${s.is_active ? "dinonaktifkan" : "diaktifkan"
                }.`
            );

            await loadStations();
        } catch (err) {
            setError(
                errText(err, "Gagal mengubah status station.")
            );
        }
    };

    const handleDelete = async (s) => {
        const ok = window.confirm(
            `Hapus permanen L${s.line}-ST${s.station_id} (${s.name})?\nTindakan ini tidak bisa dibatalkan.`
        );

        if (!ok) return;

        setMessage("");
        setError("");

        try {
            await axios.delete(
                `${BASE_URL}/api/station/${s.line}/${s.station_id}`
            );

            setMessage(
                `Station L${s.line}-ST${s.station_id} dihapus.`
            );

            if (
                editing &&
                editing.line === s.line &&
                editing.station_id === s.station_id
            ) {
                resetForm();
            }

            await loadStations();
        } catch (err) {
            setError(
                errText(err, "Gagal menghapus station.")
            );
        }
    };

    return (
        <Box
            minH="100vh"
            bg="gray.50"
            px={{ base: 4, md: 6 }}
            py={{ base: 5, md: 6 }}
        >
            <Box maxW="1400px" mx="auto">
                {/* FORM */}
                <Box
                    bg="white"
                    borderWidth="1px"
                    borderColor={
                        editing ? "blue.300" : "gray.200"
                    }
                    borderRadius="lg"
                    boxShadow="card"
                    p={{ base: 5, md: 6 }}
                    mb={5}
                >
                    <Text
                        fontSize="lg"
                        fontWeight="600"
                        color="gray.800"
                        mb={1}
                    >
                        {editing
                            ? `Edit Station L${editing.line}-ST${editing.station_id}`
                            : "Register New Station"}
                    </Text>

                    <Text
                        fontSize="sm"
                        color="gray.500"
                        mb={5}
                        lineHeight="1.6"
                    >
                        {editing
                            ? "Line dan Station ID adalah identitas station dan tidak bisa diubah. Untuk mengganti, hapus lalu daftarkan ulang."
                            : "Registrasi dilakukan oleh pemegang server. FD akan otomatis dikenali berdasarkan IP yang didaftarkan."}
                    </Text>

                    <form onSubmit={handleSubmit}>
                        <SimpleGrid
                            columns={{
                                base: 1,
                                sm: 2,
                                lg: 3,
                            }}
                            gap={4}
                        >
                            <FormField label="Station ID">
                                <Input
                                    type="number"
                                    name="station_id"
                                    min="1"
                                    required
                                    value={form.station_id}
                                    onChange={handleChange}
                                    placeholder="Contoh: 11"
                                    disabled={!!editing}
                                    bg={editing ? "gray.100" : "white"}
                                />
                            </FormField>

                            <FormField label="Line">
                                <Input
                                    type="number"
                                    name="line"
                                    min="1"
                                    required
                                    value={form.line}
                                    onChange={handleChange}
                                    placeholder="Contoh: 1"
                                    disabled={!!editing}
                                    bg={editing ? "gray.100" : "white"}
                                />
                            </FormField>

                            <FormField label="Station Name">
                                <Input
                                    type="text"
                                    name="name"
                                    required
                                    value={form.name}
                                    onChange={handleChange}
                                    placeholder="Contoh: Final Assembly 01"
                                />
                            </FormField>

                            <FormField label="FD IP Address">
                                <Input
                                    type="text"
                                    name="ip"
                                    required
                                    value={form.ip}
                                    onChange={handleChange}
                                    placeholder="Contoh: 192.168.137.117"
                                    fontFamily="mono"
                                />
                            </FormField>

                            <FormField label="Takt Time (sec)">
                                <Input
                                    type="number"
                                    name="takt_time"
                                    min="0.1"
                                    step="0.1"
                                    required
                                    value={form.takt_time}
                                    onChange={handleChange}
                                    placeholder="Contoh: 45"
                                />
                            </FormField>

                            <FormField label="ROI Timeout (sec)">
                                <Input
                                    type="number"
                                    name="roi_timeout_sec"
                                    min="0.1"
                                    step="0.1"
                                    required
                                    value={form.roi_timeout_sec}
                                    onChange={handleChange}
                                    placeholder="Contoh: 10"
                                />
                            </FormField>
                            <FormField label="Min Cycle Time (sec)">
                                <Input
                                    type="number"
                                    name="min_cycle_sec"
                                    min="0.1"
                                    step="0.1"
                                    value={form.min_cycle_sec}
                                    onChange={handleChange}
                                    placeholder="Kosong = default (3)"
                                />
                            </FormField>
                            <FormField label="Short Limit (sec)">
                                <Input
                                    type="number"
                                    name="short_anomaly_sec"
                                    min="0"
                                    step="0.1"
                                    value={form.short_anomaly_sec}
                                    onChange={handleChange}
                                    placeholder="Kosong / 0 = nonaktif"
                                />
                            </FormField>
                        </SimpleGrid>

                        {message && (
                            <Box
                                mt={5}
                                px={4}
                                py={3}
                                borderRadius="md"
                                bg="green.50"
                                borderWidth="1px"
                                borderColor="green.200"
                            >
                                <Text
                                    fontSize="sm"
                                    color="green.700"
                                >
                                    {message}
                                </Text>
                            </Box>
                        )}

                        {error && (
                            <Box
                                mt={5}
                                px={4}
                                py={3}
                                borderRadius="md"
                                bg="red.50"
                                borderWidth="1px"
                                borderColor="red.200"
                            >
                                <Text
                                    fontSize="sm"
                                    color="red.700"
                                >
                                    {error}
                                </Text>
                            </Box>
                        )}

                        <HStack mt={5} gap={2}>
                            <Button
                                type="submit"
                                size="sm"
                                bg={
                                    loading
                                        ? "gray.400"
                                        : editing
                                            ? "blue.600"
                                            : "gray.800"
                                }
                                color="white"
                                _hover={{
                                    bg: loading
                                        ? "gray.400"
                                        : editing
                                            ? "blue.700"
                                            : "gray.900",
                                }}
                                disabled={loading}
                            >
                                {loading
                                    ? "Menyimpan..."
                                    : editing
                                        ? "Simpan Perubahan"
                                        : "Register Station"}
                            </Button>

                            {editing && (
                                <Button
                                    type="button"
                                    size="sm"
                                    variant="outline"
                                    onClick={resetForm}
                                    disabled={loading}
                                >
                                    Batal
                                </Button>
                            )}
                        </HStack>
                    </form>
                </Box>

                {/* LIST */}
                <Box
                    bg="white"
                    borderWidth="1px"
                    borderColor="gray.200"
                    borderRadius="lg"
                    boxShadow="card"
                    overflow="hidden"
                >
                    <Flex
                        px={5}
                        py={4}
                        borderBottomWidth="1px"
                        borderColor="gray.200"
                        align="center"
                        justify="space-between"
                    >
                        <Text
                            fontSize="md"
                            fontWeight="600"
                            color="gray.800"
                        >
                            Registered Stations
                        </Text>

                        <Badge
                            colorPalette="gray"
                            variant="subtle"
                            borderRadius="full"
                            px={2.5}
                        >
                            {stations.length}
                        </Badge>
                    </Flex>

                    {stations.length === 0 ? (
                        <Box
                            py={10}
                            textAlign="center"
                        >
                            <Text
                                fontSize="sm"
                                color="gray.500"
                            >
                                Belum ada station yang terdaftar.
                            </Text>
                        </Box>
                    ) : (
                        <Box overflowX="auto">
                            <Table.Root
                                size="sm"
                                variant="outline"
                            >
                                <Table.Header>
                                    <Table.Row bg="gray.50">
                                        <Table.ColumnHeader>
                                            Line
                                        </Table.ColumnHeader>

                                        <Table.ColumnHeader>
                                            Station ID
                                        </Table.ColumnHeader>

                                        <Table.ColumnHeader>
                                            Name
                                        </Table.ColumnHeader>

                                        <Table.ColumnHeader>
                                            IP
                                        </Table.ColumnHeader>

                                        <Table.ColumnHeader>
                                            Takt
                                        </Table.ColumnHeader>

                                        <Table.ColumnHeader>
                                            ROI Timeout
                                        </Table.ColumnHeader>
                                        <Table.ColumnHeader>Min CT</Table.ColumnHeader>
                                        <Table.ColumnHeader>Short</Table.ColumnHeader>
                                        <Table.ColumnHeader>
                                            Status
                                        </Table.ColumnHeader>

                                        <Table.ColumnHeader>
                                            Aksi
                                        </Table.ColumnHeader>
                                    </Table.Row>
                                </Table.Header>

                                <Table.Body>
                                    {stations.map((s) => {
                                        const online = isOnline(s);

                                        const isEditingRow =
                                            editing &&
                                            editing.line === s.line &&
                                            editing.station_id === s.station_id;

                                        return (
                                            <Table.Row
                                                key={`${s.line}-${s.station_id}`}
                                                opacity={s.is_active ? 1 : 0.55}
                                                bg={
                                                    isEditingRow
                                                        ? "blue.50"
                                                        : undefined
                                                }
                                            >
                                                <Table.Cell>
                                                    {s.line}
                                                </Table.Cell>

                                                <Table.Cell>
                                                    {s.station_id}
                                                </Table.Cell>

                                                <Table.Cell>
                                                    <Text
                                                        fontWeight="500"
                                                        color="gray.700"
                                                    >
                                                        {s.name}
                                                    </Text>
                                                </Table.Cell>

                                                <Table.Cell>
                                                    <Text fontFamily="mono">
                                                        {s.ip}
                                                    </Text>
                                                </Table.Cell>

                                                <Table.Cell>
                                                    {s.takt_time}s
                                                </Table.Cell>

                                                <Table.Cell>
                                                    {s.roi_timeout_sec}s
                                                </Table.Cell>
                                                <Table.Cell>
                                                    {s.min_cycle_sec != null ? `${s.min_cycle_sec}s` : "—"}
                                                </Table.Cell>
                                                <Table.Cell>
                                                    {s.short_anomaly_sec > 0 ? `${s.short_anomaly_sec}s` : "—"}
                                                </Table.Cell>
                                                <Table.Cell>
                                                    <HStack gap={2}>
                                                        <Box
                                                            w="8px"
                                                            h="8px"
                                                            borderRadius="full"
                                                            bg={
                                                                online
                                                                    ? "green.500"
                                                                    : "gray.400"
                                                            }
                                                        />

                                                        <Text
                                                            fontSize="sm"
                                                            color="gray.700"
                                                        >
                                                            {online
                                                                ? "Online"
                                                                : "Offline"}
                                                        </Text>

                                                        {!s.is_active && (
                                                            <Badge
                                                                colorPalette="red"
                                                                variant="subtle"
                                                                fontSize="10px"
                                                            >
                                                                Nonaktif
                                                            </Badge>
                                                        )}
                                                    </HStack>
                                                </Table.Cell>

                                                <Table.Cell>
                                                    <HStack gap={1.5}>
                                                        <Button
                                                            size="xs"
                                                            variant="outline"
                                                            colorPalette="blue"
                                                            onClick={() =>
                                                                startEdit(s)
                                                            }
                                                        >
                                                            Edit
                                                        </Button>

                                                        <Button
                                                            size="xs"
                                                            variant="outline"
                                                            colorPalette="gray"
                                                            onClick={() =>
                                                                toggleActive(s)
                                                            }
                                                        >
                                                            {s.is_active
                                                                ? "Nonaktifkan"
                                                                : "Aktifkan"}
                                                        </Button>

                                                        <Button
                                                            size="xs"
                                                            variant="outline"
                                                            colorPalette="red"
                                                            onClick={() =>
                                                                handleDelete(s)
                                                            }
                                                        >
                                                            Hapus
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
        <VStack
            align="stretch"
            gap={1.5}
        >
            <Text
                fontSize="xs"
                fontWeight="600"
                color="gray.600"
            >
                {label}
            </Text>

            {children}
        </VStack>
    );
}