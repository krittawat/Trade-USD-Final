// https://nuxt.com/docs/api/configuration/nuxt-config
// Antigravity Dashboard — Nuxt config

export default defineNuxtConfig({
  // เปิดใช้ Tailwind CSS
  modules: ['@pinia/nuxt'],

  // ตั้งค่า dev server
  devtools: { enabled: true },

  // Proxy API requests ไปยัง backend
  runtimeConfig: {
    public: {
      apiBase: process.env.API_BASE_URL || 'http://localhost:8000',
    },
  },

  // CSS Config
  css: ['~/assets/css/tailwind.css'],

  // PostCSS config
  postcss: {
    plugins: {
      tailwindcss: {},
      autoprefixer: {},
    },
  },





  // CompatibilityDate สำหรับ Nuxt 3
  compatibilityDate: '2024-11-01',
})
