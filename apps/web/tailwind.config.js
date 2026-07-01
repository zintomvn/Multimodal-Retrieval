/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', "system-ui", "-apple-system", "sans-serif"],
        mono: ['"JetBrains Mono"', '"Fira Code"', "ui-monospace", "monospace"],
      },
      colors: {
        surface: {
          base: "var(--bg-base)",
          panel: "var(--bg-panel)",
          raised: "var(--bg-raised)",
          hover: "var(--bg-hover)",
          active: "var(--bg-active)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          hi: "var(--accent-hi)",
          dim: "var(--accent-dim)",
          glow: "var(--accent-glow)",
          ring: "var(--accent-ring)",
        },
        border: {
          DEFAULT: "var(--border)",
          hi: "var(--border-hi)",
          acc: "var(--border-acc)",
        },
        txt: {
          1: "var(--text-1)",
          2: "var(--text-2)",
          3: "var(--text-3)",
        },
        green: { DEFAULT: "var(--green)", dim: "var(--green-dim)", bdr: "var(--green-bdr)" },
        amber: { DEFAULT: "var(--amber)", dim: "var(--amber-dim)", bdr: "var(--amber-bdr)" },
        red: { DEFAULT: "var(--red)", dim: "var(--red-dim)", bdr: "var(--red-bdr)" },
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(9px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        spin: { to: { transform: "rotate(360deg)" } },
      },
      animation: {
        "fade-up": "fade-up 0.4s cubic-bezier(0.16,1,0.3,1) both",
        "spin-slow": "spin 0.85s linear infinite",
      },
      transitionTimingFunction: {
        "out-expo": "cubic-bezier(0.16,1,0.3,1)",
      },
    },
  },
  plugins: [],
};
