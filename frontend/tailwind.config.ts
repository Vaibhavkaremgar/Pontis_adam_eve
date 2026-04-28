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
        background: "#F6F1E8",
        foreground: "#4b3f32",
        card: "#F3EDE3",
        muted: "#EFE6D8",
        border: "rgba(120, 100, 80, 0.08)",
        primary: "#14532D",
        "primary-foreground": "#ffffff",
        accent: "#f59e0b",
        success: "#166534"
      },
      borderRadius: {
        xl: "1rem"
      },
      boxShadow: {
        card: "0 4px 12px rgba(0, 0, 0, 0.02)"
      }
    }
  },
  plugins: []
};

export default config;
