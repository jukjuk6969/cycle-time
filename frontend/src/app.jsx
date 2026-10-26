/**
 * App.jsx
 * Dashboard utama CT Monitor.
 * Layout: header → grid station cards → anomaly list → detail panel
 */

import React, { useState } from "react";
import {
  ChakraProvider,
  Box,
  Flex,
  Text,
  Button,
  Image,
  SimpleGrid,
  HStack,
  VStack,
  Badge,
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

export default function App() {
  return (
    <ChakraProvider value={system}>
      <Dashboard />
    </ChakraProvider>
  );
}

function Dashboard() {
  const {
    summary,
    heartbeats,
    recentCycles,
    connected,
    fetchHistory,
    fetchHourly,
    fetchStats,
    exportCSV,
  } = useSocket();

  const [page, setPage] = useState("dashboard");
  const dbOk = useDbHealth(connected);
  const stKey = (s) => `${s.line}-${s.station_id}`;

  const [selectedKey, setSelectedKey] = useState(null);

  const selected =
    summary.find((s) => stKey(s) === selectedKey) || null;

  const totalCycles = summary.reduce(
    (s, st) => s + (st.cycles_today || 0),
    0
  );

  const avgCT = summary.length
    ? (
      summary.reduce(
        (s, st) => s + (st.avg_ct_today || 0),
        0
      ) /
      summary.filter((s) => s.avg_ct_today).length || 0
    )
    : 0;

  const overTakt = summary.filter(
    (s) =>
      s.last_cycle_sec &&
      s.takt_time &&
      s.last_cycle_sec > s.takt_time
  ).length;

  /* =========================================================
     STATION REGISTER PAGE
     ========================================================= */

  if (page === "station-register") {
    return (
      <Box minH="100vh" bg="gray.50">
        <Flex
          h="64px"
          px={{ base: 4, md: 6 }}
          bg="white"
          borderBottomWidth="1px"
          borderColor="gray.200"
          align="center"
          justify="space-between"
          boxShadow="sm"
        >
          <HStack gap={3}>
            <Image
              src={logoGif}
              alt="CT Monitor"
              h="36px"
              w="auto"
              borderRadius="md"
            />

            <VStack
              align="start"
              gap={0}
              display={{ base: "none", sm: "flex" }}
            >
              <Text
                fontSize="sm"
                fontWeight="700"
                color="gray.800"
                lineHeight="1.2"
              >
                CT Monitor
              </Text>

              <Text
                fontSize="xs"
                color="gray.500"
                lineHeight="1.2"
              >
                Station Managementation Register
              </Text>
            </VStack>
          </HStack>

          <Button
            size="sm"
            variant="outline"
            onClick={() => setPage("dashboard")}
          >
            Dashboard
          </Button>
        </Flex>

        <Box
          maxW="1800px"
          mx="auto"
          p={{ base: 4, md: 6 }}
        >
          <StationRegister
            onBack={() => setPage("dashboard")}
          />
        </Box>
      </Box>
    );
  }

  /* =========================================================
     MAIN DASHBOARD
     ========================================================= */

  return (
    <Box minH="100vh" bg="gray.50">
      {/* =====================================================
          HEADER
          ===================================================== */}

      <Flex
        h={{ base: "auto", md: "64px" }}
        minH="64px"
        px={{ base: 4, md: 6 }}
        py={{ base: 3, md: 0 }}
        bg="white"
        borderBottomWidth="1px"
        borderColor="gray.200"
        align={{ base: "stretch", md: "center" }}
        justify="space-between"
        gap={4}
        direction={{ base: "column", md: "row" }}
        boxShadow="sm"
      >
        {/* BRAND */}

        <HStack gap={3}>
          <Image
            src={logoGif}
            alt="CT Monitor"
            h="36px"
            w="auto"
            borderRadius="md"
          />

          <VStack
            align="start"
            gap={0}
          >
            <Text
              fontSize="sm"
              fontWeight="700"
              color="gray.800"
              lineHeight="1.2"
            >
              CT Monitor
            </Text>

            <Text
              fontSize="xs"
              color="gray.500"
              lineHeight="1.2"
            >
              Final Assembly Line Dashboard
            </Text>
          </VStack>
        </HStack>

        {/* HEADER METRICS */}

        <Flex
          align="center"
          justify={{ base: "space-between", md: "flex-end" }}
          gap={{ base: 3, md: 6 }}
          flexWrap="wrap"
        >
          <Button
            size="sm"
            variant="outline"
            onClick={() => setPage("station-register")}
          >
            Station Managementation Register
          </Button>

          <Metric
            value={totalCycles}
            label="Cycles today"
          />

          <Metric
            value={
              avgCT > 0
                ? `${avgCT.toFixed(1)}s`
                : "—"
            }
            label="Avg CT"
          />

          <Metric
            value={overTakt}
            label="Over takt"
            valueColor={
              overTakt > 0
                ? "red.500"
                : "green.500"
            }
          />

          <HStack gap={2}>
            <Box
              w="8px"
              h="8px"
              borderRadius="full"
              bg={
                connected
                  ? "green.500"
                  : "red.500"
              }
            />

            <Text
              fontSize="xs"
              color="gray.500"
              fontWeight="500"
            >
              {connected
                ? "Connected"
                : "Disconnected"}
            </Text>
          </HStack>
        </Flex>
      </Flex>

      {/* =====================================================
          CONTENT
          ===================================================== */}

      <Box
        maxW="1800px"
        mx="auto"
        px={{ base: 4, md: 6 }}
        py={{ base: 4, md: 6 }}
      >
        {/* ===================================================
            STATION GRID
            =================================================== */}

        <SimpleGrid
          columns={{
            base: 1,
            sm: 2,
            md: 3,
            lg: 4,
            xl: 5,
            "2xl": 6,
          }}
          gap={4}
          mb={6}
        >
          {summary.map((station) => {
            const k = stKey(station);

            return (
              <StationCard
                key={k}
                station={station}
                heartbeat={heartbeats[k]}
                selected={k === selectedKey}
                onClick={() =>
                  setSelectedKey(
                    k === selectedKey
                      ? null
                      : k
                  )
                }
              />
            );
          })}

          {summary.length === 0 && (
            <Box
              gridColumn="1 / -1"
              py={16}
              textAlign="center"
            >
              <VStack gap={2}>
                <Text
                  fontSize="sm"
                  color="gray.500"
                >
                  {connected
                    ? "Memuat data stasiun..."
                    : "Menunggu koneksi ke backend..."}
                </Text>

                <Badge
                  colorPalette={
                    connected
                      ? "blue"
                      : "red"
                  }
                  variant="subtle"
                >
                  {connected
                    ? "Loading"
                    : "Offline"}
                </Badge>
              </VStack>
            </Box>
          )}
        </SimpleGrid>

        {/* ===================================================
            ANOMALIES
            =================================================== */}

        <Box mb={6}>
          <AnomalyList summary={summary} />
        </Box>

        {/* ===================================================
            SELECTED STATION DETAIL
            =================================================== */}

        {selected && (
          <>
            <Box mb={4} p={4} bg="white" borderWidth="1px" borderColor="gray.200" borderRadius="md">
              <Text fontSize="xs" fontWeight="600" color="gray.500">
                Connectivity · L{selected.line} ST{selected.station_id}
              </Text>
              <StationConnectivity
                heartbeat={heartbeats[stKey(selected)]}
                connected={connected}
                dbOk={dbOk}
              />
            </Box>
            <StationTimeline
              station={selected}
              heartbeat={heartbeats[stKey(selected)]}
            />

            <Box mt={6}>
              <StationDetail
                station={selected}
                fetchHistory={fetchHistory}
                fetchHourly={fetchHourly}
                fetchStats={fetchStats}
                exportCSV={exportCSV}
                recentCycles={recentCycles}
              />
            </Box>
          </>
        )}
      </Box>
    </Box>
  );
}

/* =========================================================
   HEADER METRIC
   ========================================================= */

function Metric({
  value,
  label,
  valueColor = "gray.800",
}) {
  return (
    <VStack
      gap={0}
      minW="70px"
    >
      <Text
        fontSize="lg"
        fontWeight="700"
        lineHeight="1.2"
        color={valueColor}
      >
        {value}
      </Text>

      <Text
        fontSize="10px"
        color="gray.400"
        whiteSpace="nowrap"
      >
        {label}
      </Text>
    </VStack>
  );
}
