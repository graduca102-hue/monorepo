/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'Arial', 'sans-serif'],
      },
      colors: {
        zinc: {
          950: '#111113',
          900: '#18191b',
          800: '#212225',
          700: '#272a2d',
          600: '#2e3135',
          500: '#363a3f',
          400: '#43484e',
          300: '#5a6169',
          200: '#696e77',
          100: '#b0b4ba',
          50: '#edeef0',
        },
        green: {
          950: '#052e16',
          900: '#14532d',
          800: '#166534',
          700: '#15803d',
          600: '#16a34a',
          500: '#22c55e',
          400: '#4ade80',
          300: '#86efac',
          200: '#bbf7d0',
          100: '#dcfce7',
          50: '#f0fdf4',
        },
      },
      borderRadius: {
        DEFAULT: '12px',
        sm: '8px',
        md: '10px',
        lg: '14px',
        xl: '16px',
        '2xl': '24px',
      },
      transitionDuration: {
        DEFAULT: '150ms',
      },
      keyframes: {
        shimmer: {
          '0%': { backgroundPosition: '200% 0' },
          '100%': { backgroundPosition: '-200% 0' },
        },
        pulse: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.4' },
        },
        spin: {
          from: { transform: 'rotate(0deg)' },
          to: { transform: 'rotate(360deg)' },
        },
      },
      animation: {
        shimmer: 'shimmer 2s linear infinite',
        pulse: 'pulse 2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};
