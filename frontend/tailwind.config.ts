import type { Config } from 'tailwindcss'

export default {
  content: [
    './components/**/*.{vue,js,ts}',
    './composables/**/*.{js,ts}',
    './pages/**/*.{vue,js,ts}',
    './app.vue',
    './assets/**/*.{css,vue}'
  ],
  theme: { extend: {} },
  plugins: []
} satisfies Config

