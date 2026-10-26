import { createSystem, defaultConfig } from "@chakra-ui/react";

export const system = createSystem(defaultConfig, {
    theme: {
        tokens: {
            colors: {
                brand: {
                    50: { value: "#f4f6f8" },
                    100: { value: "#e5e9ed" },
                    200: { value: "#cbd2d9" },
                    300: { value: "#aeb8c2" },
                    400: { value: "#8c99a5" },
                    500: { value: "#5f6b76" },
                    600: { value: "#4b5660" },
                    700: { value: "#39434c" },
                    800: { value: "#252d34" },
                    900: { value: "#171c21" },
                },

                industrial: {
                    50: { value: "#f5f6f7" },
                    100: { value: "#e7e9eb" },
                    200: { value: "#d0d4d8" },
                    300: { value: "#adb3b9" },
                    400: { value: "#858d95" },
                    500: { value: "#626b74" },
                    600: { value: "#4b535b" },
                    700: { value: "#383f46" },
                    800: { value: "#272d32" },
                    900: { value: "#191d21" },
                },

                success: {
                    500: { value: "#1d9e75" },
                },

                danger: {
                    500: { value: "#e24b4a" },
                },

                warning: {
                    500: { value: "#d89b27" },
                },
            },

            fonts: {
                heading: {
                    value:
                        "Inter, system-ui, -apple-system, BlinkMacSystemFont, sans-serif",
                },
                body: {
                    value:
                        "Inter, system-ui, -apple-system, BlinkMacSystemFont, sans-serif",
                },
            },

            radii: {
                sm: { value: "6px" },
                md: { value: "8px" },
                lg: { value: "10px" },
            },

            shadows: {
                card: {
                    value: "0 1px 3px rgba(0, 0, 0, 0.08)",
                },
                cardHover: {
                    value: "0 4px 12px rgba(0, 0, 0, 0.10)",
                },
            },
        },
    },
});