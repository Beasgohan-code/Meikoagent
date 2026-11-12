import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        charcoal: {
          950: "#08080c",
          900: "#0c0d12",
          850: "#101118",
          800: "#14161f",
          750: "#191c26",
          700: "#1f2230",
          600: "#2a2e3f",
        },
        violet: {
          400: "#a78bfa",
          500: "#8b5cf6",
          600: "#7c3aed",
          700: "#6d28d9",
        },
        indigo: { 400: "#818cf8", 500: "#6366f1", 600: "#4f46e5" },
        cyan: { 300: "#67e8f9", 400: "#22d3ee", 500: "#06b6d4" },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(139,92,246,0.18), 0 12px 40px -12px rgba(99,102,241,0.45)",
        card: "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 18px 40px -24px rgba(0,0,0,0.9)",
      },
      backgroundImage: {
        "grid-fade":
          "radial-gradient(circle at 1px 1px, rgba(148,163,184,0.10) 1px, transparent 0)",
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        pulse_ring: {
          "0%": { boxShadow: "0 0 0 0 rgba(34,211,238,0.5)" },
          "100%": { boxShadow: "0 0 0 8px rgba(34,211,238,0)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.35s ease-out both",
        "pulse-ring": "pulse_ring 1.8s ease-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
