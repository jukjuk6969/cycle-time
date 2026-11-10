import React from "react";
import { Box } from "@chakra-ui/react";
import { useThemeMode } from "../hooks/useThemeMode";

const EASE = "cubic-bezier(.2,.8,.2,1)";

/** Tombol terang/gelap: matahari dan bulan saling berputar + memudar. */
export default function ThemeToggle({ size = "30px" }) {
  const { mode, toggle } = useThemeMode();
  const dark = mode === "dark";

  const icon = (visible, rotateOut) => ({
    position: "absolute",
    inset: 0,
    margin: "auto",
    opacity: visible ? 1 : 0,
    transform: visible ? "rotate(0deg) scale(1)" : `rotate(${rotateOut}deg) scale(.4)`,
    transition: `transform 420ms ${EASE}, opacity 260ms ease`,
  });

  return (
    <Box
      as="button"
      type="button"
      onClick={toggle}
      role="switch"
      aria-checked={dark}
      aria-label={dark ? "Ganti ke mode terang" : "Ganti ke mode gelap"}
      title={dark ? "Mode terang" : "Mode gelap"}
      position="relative" flexShrink={0}
      w={size} h={size} borderRadius="4px"
      border="1px solid" borderColor="var(--ct-ink-200)"
      bg="var(--ct-surface)" color="var(--ct-ink-600)"
      cursor="pointer" overflow="hidden"
      transition={`background-color 120ms, border-color 120ms, color 120ms, transform 160ms ${EASE}`}
      _hover={{ bg: "var(--ct-blue-50)", borderColor: "var(--ct-blue-200)", color: "var(--ct-blue)" }}
      _active={{ transform: "scale(.94)" }}
      _focusVisible={{ outline: "none", boxShadow: "0 0 0 3px var(--ct-blue-100)" }}
    >
      {/* Matahari */}
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
        style={icon(!dark, 90)}>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
      </svg>
      {/* Bulan */}
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
        style={icon(dark, -90)}>
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
      </svg>
    </Box>
  );
}
