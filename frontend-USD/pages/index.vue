<!-- 
  pages/index.vue — Dashboard หน้าหลัก
  แสดง: System Health, Symbols, Decisions, Metrics
-->
<template>
  <div class="space-y-6">
    <!-- System Health -->
    <section class="grid grid-cols-1 md:grid-cols-4 gap-4">
      <div v-for="service in services" :key="service.name"
           class="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <div class="flex items-center justify-between">
          <span class="text-sm text-gray-400">{{ service.name }}</span>
          <span :class="service.status === 'ok' ? 'text-emerald-400' : 'text-red-400'"
                class="text-xs font-semibold">
            {{ service.status === 'ok' ? '● Online' : '○ Offline' }}
          </span>
        </div>
      </div>
    </section>

    <!-- Trading Mode -->
    <section class="bg-gray-900 border border-gray-800 rounded-xl p-6">
      <h2 class="text-lg font-semibold mb-4 text-gray-200">
        📊 สถานะระบบ
      </h2>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
        <div>
          <p class="text-3xl font-bold text-emerald-400">0</p>
          <p class="text-sm text-gray-400">เทรดวันนี้</p>
        </div>
        <div>
          <p class="text-3xl font-bold text-yellow-400">0</p>
          <p class="text-sm text-gray-400">ถูกบล็อก</p>
        </div>
        <div>
          <p class="text-3xl font-bold text-blue-400">$0.00</p>
          <p class="text-sm text-gray-400">กำไร/ขาดทุนวันนี้</p>
        </div>
        <div>
          <p class="text-3xl font-bold text-gray-300">0</p>
          <p class="text-sm text-gray-400">Positions เปิด</p>
        </div>
      </div>
    </section>

    <!-- Decision Timeline Placeholder -->
    <section class="bg-gray-900 border border-gray-800 rounded-xl p-6">
      <h2 class="text-lg font-semibold mb-4 text-gray-200">
        🔍 Decision Timeline
      </h2>
      <p class="text-gray-500 text-sm">
        ยังไม่มีข้อมูล — เริ่มระบบเพื่อเห็น decisions แบบ real-time
      </p>
    </section>
  </div>
</template>

<script setup>
// --- สถานะ services ---
const services = ref([
  { name: 'MT5', status: 'stub' },
  { name: 'QuestDB', status: 'stub' },
  { name: 'SQLite', status: 'stub' },
  { name: 'DuckDB', status: 'stub' },
])

// TODO: ดึงข้อมูลจาก /api/health
</script>
