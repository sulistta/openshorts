/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        paper: 'oklch(16% 0.014 265 / <alpha-value>)',
        paper2: 'oklch(19% 0.014 265 / <alpha-value>)',
        paper3: 'oklch(23% 0.016 265 / <alpha-value>)',
        paper4: 'oklch(27% 0.018 265 / <alpha-value>)',
        ink: 'oklch(96% 0.006 262 / <alpha-value>)',
        ink2: 'oklch(84% 0.01 262 / <alpha-value>)',
        muted: 'oklch(63% 0.012 262 / <alpha-value>)',
        faint: 'oklch(53% 0.012 262 / <alpha-value>)',
        brass: 'oklch(78% 0.13 78 / <alpha-value>)',
        brassink: 'oklch(20% 0.03 78 / <alpha-value>)',
        coral: 'oklch(78% 0.13 78 / <alpha-value>)',
        accent: 'oklch(78% 0.13 78 / <alpha-value>)',
        primary: 'oklch(78% 0.13 78 / <alpha-value>)',
        ok: 'oklch(76% 0.12 150 / <alpha-value>)',
        warn: 'oklch(80% 0.14 82 / <alpha-value>)',
        danger: 'oklch(70% 0.17 25 / <alpha-value>)',
        background: 'oklch(16% 0.014 265 / <alpha-value>)',
        surface: 'oklch(19% 0.014 265 / <alpha-value>)',
      },
      fontFamily: {
        display: ['var(--font-display)'],
        body: ['var(--font-body)'],
        sans: ['var(--font-body)'],
        serif: ['var(--font-display)'],
        mono: ['var(--font-mono)'],
      },
      borderColor: {
        rule: 'var(--color-rule)',
        rule2: 'var(--color-rule-2)',
      },
      borderRadius: {
        card: 'var(--radius-card)',
        panel: 'var(--radius-panel)',
        input: 'var(--radius-input)',
        control: 'var(--radius-control)',
      },
      fontSize: {
        micro: ['10px', { letterSpacing: '0.06em' }],
      },
      transitionTimingFunction: {
        out: 'var(--ease-out)',
        'in-out': 'var(--ease-in-out)',
      },
      animation: {
        fade: 'fadeIn 240ms var(--ease-out)',
      },
      boxShadow: {
        panel: 'var(--shadow-panel)',
        popover: 'var(--shadow-popover)',
      },
    },
  },
  plugins: [],
};
