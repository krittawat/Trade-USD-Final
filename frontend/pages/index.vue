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
            {{ service.status === 'ok' ? '● ออนไลน์' : '○ ออฟไลน์' }}
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
          <p class="text-3xl font-bold text-emerald-400">{{ stats.trades_today }}</p>
          <p class="text-sm text-gray-400">เทรดวันนี้</p>
        </div>
        <div>
          <p class="text-3xl font-bold text-yellow-400">{{ stats.blocked_today }}</p>
          <p class="text-sm text-gray-400">ถูกบล็อก</p>
        </div>
        <div>
          <p class="text-3xl font-bold" :class="stats.pnl_today >= 0 ? 'text-blue-400' : 'text-red-400'">
            ${{ stats.pnl_today }}
          </p>
          <p class="text-sm text-gray-400">กำไร/ขาดทุนวันนี้</p>
        </div>
        <div>
          <p class="text-3xl font-bold text-gray-300">{{ stats.open_positions }}</p>
          <p class="text-sm text-gray-400">Positions เปิด</p>
        </div>
      </div>
    </section>

    <!-- Market Regimes & Risk -->
    <RegimeDisplay :regimes="regimes" :riskMetrics="risk_metrics" />

    <!-- Coach Report -->
    <CoachReport />

    <!-- Decision Timeline Placeholder -->
    <section class="bg-gray-900 border border-gray-800 rounded-xl p-6">
      <h2 class="text-lg font-semibold mb-4 text-gray-200">
        🔍 ไทม์ไลน์การตัดสินใจ (Decision Timeline)
      </h2>
      <DecisionTimeline />
    </section>
  </div>
</template>

<script setup>
// --- สถานะ services ---
const services = ref([
  { name: 'MT5', status: 'stub' },
  { name: 'QuestDB', status: 'stub' },
  { name: 'SQLite', status: 'stub' },
  { name: 'Brain', status: 'stub' },
])

const regimes = ref({})
const risk_metrics = ref(null)
const stats = ref({
  trades_today: 0,
  blocked_today: 0,
  pnl_today: 0,
  open_positions: 0
})

async function fetchStatus() {
  try {
    const res = await fetch('http://localhost:8000/api/status')
    if (!res.ok) return
    const data = await res.json()
    
    // Update Services
    services.value = [
      { name: 'MT5', status: data.services.mt5 === 'connected' ? 'ok' : 'error' },
      { name: 'QuestDB', status: data.services.sqlite === 'connected' ? 'ok' : 'error' }, // Mapping sqlite/questdb based on API
      { name: 'Brain', status: data.services.brain === 'connected' ? 'ok' : 'error' },
      { name: 'Mode', status: data.mode === 'LIVE' ? 'ok' : 'warn' }
    ]

    // Update Regimes
    if (data.regimes) {
      regimes.value = data.regimes
    }
    
    // Update Risk Metrics (Phase D)
    if (data.risk_metrics) {
      risk_metrics.value = data.risk_metrics
    }

    // Update Stats
    if (data.today) {
      stats.value.trades_today = data.today.signals_today || 0
      stats.value.blocked_today = data.today.blocked_today || 0
    }
    if (data.account) {
      stats.value.pnl_today = data.account.daily_pl || 0
      stats.value.open_positions = data.positions_count || 0
    }

  } catch (e) {
    console.error(e)
  }
}

onMounted(() => {
  fetchStatus()
  setInterval(fetchStatus, 3000)
})
</script>
