/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/pages/**/*.{js,jsx}",
    "./src/components/**/*.{js,jsx}",
  ],
  theme: {
    extend: {
      colors: {
        neo: {
          bg: '#1a1b1e',
          surface: '#2a2c34',
          'surface-light': '#33353d',
          border: '#3e4047',
          text: '#e8e8e8',
          'text-muted': '#a0a3ab',
          blue: '#4c8eda',
          green: '#4cd964',
          red: '#f25757',
          yellow: '#f5c842',
          cyan: '#4edbe5',
        },
      },
    },
  },
  plugins: [],
}
