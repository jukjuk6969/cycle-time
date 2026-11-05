import React, { useEffect, useState } from "react";
import { Box, Flex, Text } from "@chakra-ui/react";
import TimelineBar from "./TimelineBar";

const STALE_MS = 5000;
const ACTIVE = ["RUNNING", "TRANSIT"];

export default function StationTimeline({ station, heartbeat }) {
    const [, tick] = useState(0);
    useEffect(() => {
        const t = setInterval(() => tick((n) => n + 1), 250);
        return () => clearInterval(t);
    }, []);

    if (!station) return null;

    const age = heartbeat?.received ? Date.now() - heartbeat.received : Infinity;
    const stale = age > STALE_MS;
    const liveState = stale ? "OFFLINE" : heartbeat?.state || "IDLE";
    const active = ACTIVE.includes(liveState);

    const elapsed =
        stale || heartbeat?.elapsed_sec == null
            ? null
            : heartbeat.elapsed_sec + (active ? (Date.now() - heartbeat.received) / 1000 : 0);

    const target = station.target_cycle_time || station.takt_time;

    return (
        <Box
            bg="white"
            borderWidth="1px"
            borderColor="gray.200"
            borderRadius="8px"
            mt="13px"
            px="21px"
            py="13px"
        >
            <Flex justify="space-between" align="baseline" mb="8px">
                <Text fontSize="13px" fontWeight="600" color="gray.800">
                    Time Line Cycle
                </Text>
                <Text fontSize="11px" color="gray.500">
                    L{station.line} ST{station.station_id} · {liveState}
                    {elapsed != null ? ` · ${elapsed.toFixed(1)}s` : ""}
                </Text>
            </Flex>

            <TimelineBar
                elapsed={elapsed}
                min={station.min_cycle_sec}
                short={station.short_anomaly_sec}
                target={target}
                active={active}
            />
            {(() => {
                const setpoint = Number(station.roi_timeout_sec) || 0;
                const idle =
                    stale || heartbeat?.roi_idle_sec == null
                        ? null
                        : heartbeat.roi_idle_sec + (active ? (Date.now() - heartbeat.received) / 1000 : 0);
                if (!setpoint) return null;

                const ratio = idle != null ? Math.min(idle / setpoint, 1) : 0;
                const color = ratio >= 1 ? "red.500" : ratio >= 0.7 ? "orange.400" : "green.500";

                return (
                    <Box mt="8px">
                        <Flex justify="space-between" mb="4px">
                            <Text fontSize="11px" color="gray.500">ROI timeout</Text>
                            <Text fontSize="11px" color={ratio >= 0.7 ? "red.500" : "gray.500"}
                                style={{ fontVariantNumeric: "tabular-nums" }}>
                                {idle != null
                                    ? `${idle.toFixed(0)}s / ${setpoint.toFixed(0)}s · sisa ${Math.max(setpoint - idle, 0).toFixed(0)}s`
                                    : `setpoint ${setpoint.toFixed(0)}s`}
                            </Text>
                        </Flex>
                        <Box bg="gray.100" borderRadius="full" h="6px" overflow="hidden">
                            <Box h="100%" borderRadius="full" bg={color}
                                width={`${ratio * 100}%`}
                                transition="width 0.25s linear, background-color 0.3s ease" />
                        </Box>
                    </Box>
                );
            })()}
        </Box>
    );
}