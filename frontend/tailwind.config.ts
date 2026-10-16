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
          panel: '#111111',
          elevated: '#181818',
          pressed: '#222222',
        },
        border: {
          subtle: '#1c1c1c',
          default: '#282828',
          focus: '#555555',
          contrast: '#ffffff',
        },
        text: {
          primary: '#f0f0f0',
          secondary: '#999999',
          muted: '#555555',
        },
        accent: {
          active: '#ffffff',
        },
        status: {
          info: '#3b82f6',
          warning: '#eab308',
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
        mono: ['JetBrains Mono', 'Fira Code', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
      }
    },
  },
  plugins: [],
} satisfies Config
