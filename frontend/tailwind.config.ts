import type { Config } from 'tailwindcss'

export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        surface: {
          bg: '#000000',
          base: '#080808',
          panel: '#0c0c0c',
          elevated: '#141414',
          pressed: '#1e1e1e',
        },
        border: {
          subtle: '#1c1c1c',
          default: '#282828',
          focus: '#444444',
          contrast: '#ffffff',
        },
        text: {
          primary: '#f0f0f0',
          secondary: '#a3a3a3',
          muted: '#888888',
          caption: '#737373',
        },
        accent: {
          active: '#ffffff',
        },
        status: {
          info: '#38bdf8',
          warning: '#facc15',
          danger: '#ef4444',
          success: '#22c55e',
        }
      },
      borderRadius: {
        DEFAULT: '0px',
        none: '0px',
        sm: '0px',
        md: '0px',
        lg: '0px',
        xl: '0px',
        '2xl': '0px',
        '3xl': '0px',
        full: '0px',
      },
      boxShadow: {
        none: 'none',
        sm: 'none',
        DEFAULT: 'none',
        md: 'none',
        lg: 'none',
        xl: 'none',
        '2xl': 'none',
        inner: 'none',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'Liberation Mono', 'monospace'],
      }
    },
  },
  plugins: [],
} satisfies Config
