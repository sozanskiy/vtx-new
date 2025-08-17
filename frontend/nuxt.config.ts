// Nuxt 3 configuration for RER-Kilo kiosk
export default defineNuxtConfig({
  typescript: { shim: false },
  css: ['~/assets/styles.css'],
  nitro: {
    routeRules: {
      '/api/**': { proxy: 'http://127.0.0.1:8080/**' },
      '/events': { proxy: 'http://127.0.0.1:8080/events' },
      '/video.mjpeg': { proxy: 'http://127.0.0.1:8080/video.mjpeg' }
    }
  },
  postcss: {
    plugins: {
      tailwindcss: {},
      autoprefixer: {}
    }
  }
})

