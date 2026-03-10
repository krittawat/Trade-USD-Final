<!-- 
  app.vue — Root layout ของ Antigravity Dashboard
  ทุกหน้าแสดงผ่าน NuxtPage
-->
<template>
  <div class="min-h-screen bg-gray-950 text-gray-100">
    <!-- Header -->
    <header class="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm">
      <div class="container mx-auto px-4 py-3 flex items-center justify-between">
        <h1 class="text-xl font-bold text-emerald-400">
          ⚡ Antigravity Trading
        </h1>
        <div class="flex items-center gap-4">
          <!-- แสดงโหมดปัจจุบัน -->
          <span class="px-3 py-1 rounded-full text-xs font-semibold bg-yellow-500/20 text-yellow-400">
            DRY_RUN
          </span>
        </div>
      </div>
    </header>

    <!-- เนื้อหาหน้า -->
    <main class="container mx-auto px-4 py-6">
      <NuxtPage />
    </main>
  </div>
</template>
