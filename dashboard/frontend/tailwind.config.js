/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,jsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Trading-specific colours
        profit: '#22c55e',
        loss: '#ef4444',
        neutral: '#6b7280',
      },
    },
  },
  plugins: [],
}
