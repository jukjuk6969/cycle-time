/**
 * AuthGate.jsx
 * Menampilkan children hanya bila sudah login; selain itu halaman login.
 */
import React from "react";
import { Flex, Text, Box } from "@chakra-ui/react";
import { useAuth } from "../hooks/useAuth";
import LoginPage from "./LoginPage";

const MONO = `"JetBrains Mono", "SF Mono", ui-monospace, monospace`;

export default function AuthGate({ children }) {
  const { user, loading } = useAuth();
  
  if (loading)
    return (
      <Flex minH="100vh" align="center" justify="center" bg="var(--ct-canvas)" direction="column" gap={4}>
        <Box w="8px" h="8px" bg="var(--ct-orange-strong)" borderRadius="full" style={{ animation: "pulse 1.5s infinite" }} />
        <Text fontFamily={MONO} fontSize="12px" color="var(--ct-ink-500)" letterSpacing="0.1em">
          VERIFYING SESSION...
        </Text>
      </Flex>
    );
    
  return user ? children : <LoginPage />;
}