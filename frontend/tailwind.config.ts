import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "sans-serif"],
        body: ["Inter", "sans-serif"],
        heading: ["Playfair Display", "serif"]
      },
      colors: {
        background: "#F8F5F0",
        foreground: "#4b5563",
        card: "#ffffff",
        muted: "#f3f4f6",
        border: "#E5E7EB",
        primary: "#14532D",
        "primary-foreground": "#ffffff",
        accent: "#f59e0b",
        success: "#166534"
      },
      borderRadius: {
        xl: "0.75rem"
      },
      boxShadow: {
        card: "0 1px 2px rgba(0, 0, 0, 0.06)"
      }
    }
  },
  plugins: []
};

export default config;
