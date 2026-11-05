/**
 * StationDrawer.jsx
 * Panel samping kanan untuk detail station.
 *  - Layar >= xl (1280px): drawer "mendorong" halaman (lihat App.jsx), kartu tetap
 *    terlihat dan bisa diklik untuk berpindah station.
 *  - Layar lebih kecil: drawer menimpa halaman dengan latar gelap; klik latar = tutup.
 */
import React, { useEffect, useRef, useState } from "react";
import { Box, Flex, Text, Button } from "@chakra-ui/react";

const MONO = `"JetBrains Mono", "SF Mono", ui-monospace, Consolas, monospace`;

// Dipakai juga di App.jsx untuk lebar padding halaman saat drawer terbuka.
export const DRAWER_W = "clamp(380px, 33vw, 600px)";

// true bila layar cukup lebar untuk "mendorong" halaman (>= 1024px).
export function usePushLayout() {
  const q = "(min-width: 1024px)";
  const [push, setPush] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const on = () => setPush(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return push;
}

export default function StationDrawer({ open, onClose, title, scrollKey, push, children }) {
  const panelRef = useRef(null);
  const bodyRef = useRef(null);

  // Fokus ke drawer saat dibuka, supaya Escape langsung berfungsi.
  useEffect(() => {
    if (open && panelRef.current) panelRef.current.focus({ preventScroll: true });
  }, [open]);

  // Pindah station: kembali ke atas.
  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = 0;
  }, [scrollKey]);

  if (!open) return null;

  return (
    <>
      <style>{`@keyframes astra-drawer { from { transform: translateX(32px); opacity: 0 } to { transform: translateX(0); opacity: 1 } }`}</style>

      {/* Latar gelap hanya saat drawer menimpa halaman (layar sempit) */}
      {!push && (
        <Box
          position="fixed"
          inset={0}
          bg="rgba(13,17,23,0.45)"
          zIndex={19}
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      <Flex
        as="aside"
        ref={panelRef}
        tabIndex={-1}
        aria-label={title}
        onKeyDown={(e) => e.key === "Escape" && onClose()}
        direction="column"
        position="fixed"
        top={0}
        right={0}
        bottom={0}
        w={push ? DRAWER_W : "min(720px, 100vw)"}
        bg="#F4F6F8"
        borderLeft="2px solid #F7941D"
        boxShadow="-8px 0 24px rgba(35,31,32,0.10)"
        zIndex={20}
        outline="none"
        style={{ animation: "astra-drawer 220ms cubic-bezier(.2,.8,.2,1) both" }}
      >
        <Flex
          align="center"
          justify="space-between"
          gap={3}
          px="21px"
          h="48px"
          flexShrink={0}
          bg="white"
          borderBottom="1px solid #E2E8F0"
        >
          <Text
            fontSize="12px"
            fontWeight="700"
            color="#231F20"
            fontFamily={MONO}
            textTransform="uppercase"
            letterSpacing="0.06em"
            whiteSpace="nowrap"
            overflow="hidden"
            textOverflow="ellipsis"
          >
            {title}
          </Text>
          <Button
            variant="ghost"
            onClick={onClose}
            h="24px"
            px="8px"
            fontSize="11px"
            fontWeight="700"
            fontFamily={MONO}
            textTransform="uppercase"
            letterSpacing="0.06em"
            borderRadius="3px"
            color="#64748B"
            flexShrink={0}
            _hover={{ color: "#C5660A", bg: "#FEF4E5" }}
            _focusVisible={{ boxShadow: "0 0 0 3px #FCE4C5" }}
          >
            Close ✕
          </Button>
        </Flex>

        <Box ref={bodyRef} flex="1" overflowY="auto" p="21px">
          {children}
        </Box>
      </Flex>
    </>
  );
}