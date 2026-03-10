<template>
  <div class="space-y-6">
    <!-- 1. Regime Section -->
    <div>
      <h2 class="text-lg font-semibold text-gray-200 flex items-center gap-2 mb-4">
        <span>🌐</span> สภาวะตลาด (Market Regimes)
      </h2>
      
      <div v-if="Object.keys(regimes).length === 0" class="text-gray-500 text-sm italic">
        กำลังรอข้อมูลตลาด...
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div v-for="(ctx, symbol) in regimes" :key="symbol"
             class="bg-gray-900 border border-gray-800 rounded-xl p-4 hover:border-gray-700 transition-colors">
          
          <!-- Header -->
          <div class="flex justify-between items-start mb-3">
            <div>
              <h3 class="font-bold text-gray-100 flex items-center gap-2">
                {{ symbol }}
              </h3>
              <span class="text-xs text-gray-400 capitalize">{{ ctx.regime.replace('_', ' ').toLowerCase() }}</span>
            </div>
            <!-- Actionable Badge -->
            <span :class="ctx.actionable ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 'bg-red-500/10 text-red-400 border-red-500/20'"
                  class="text-xs px-2 py-1 rounded border font-medium flex items-center gap-1">
              <span v-if="ctx.actionable">✅ เทรดได้</span>
              <span v-else>⛔ บล็อก</span>
            </span>
          </div>

          <!-- Reason / Analysis -->
          <div class="text-sm text-gray-300 mb-3 bg-gray-950/50 p-2 rounded border border-gray-800 min-h-[3rem]">
            {{ ctx.reason || 'ไม่มีสัญญาณ' }}
          </div>

          <!-- Metrics Grid -->
          <div class="grid grid-cols-3 gap-2 text-xs border-t border-gray-800 pt-3">
            <div v-for="(val, key) in formatDetails(ctx.details)" :key="key" 
                 class="text-center">
              <div class="text-gray-500 uppercase tracking-wider text-[10px]">{{ key }}</div>
              <div class="font-mono text-gray-300">{{ val }}</div>
            </div>
          </div>

        </div>
      </div>
    </div>

    <!-- 2. Risk Status Section (New Phase D) -->
    <div v-if="riskMetrics" class="border-t border-gray-800 pt-6">
      <h2 class="text-lg font-semibold text-gray-200 flex items-center gap-2 mb-4">
        <span>🛡️</span> สถานะความเสี่ยง (Dynamic Risk)
      </h2>
      
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <!-- Drawdown -->
        <div class="bg-gray-900 border border-gray-800 rounded-xl p-4">
          <div class="text-gray-400 text-xs uppercase mb-1">Max Drawdown</div>
          <div class="text-2xl font-mono" :class="getDDClass(riskMetrics.max_dd_pct)">
            {{ riskMetrics.max_dd_pct.toFixed(2) }}%
          </div>
          <div class="text-xs mt-1" :class="getDDClass(riskMetrics.max_dd_pct)">
             {{ getDDText(riskMetrics.max_dd_pct) }}
          </div>
        </div>

        <!-- Loss Streak -->
        <div class="bg-gray-900 border border-gray-800 rounded-xl p-4">
          <div class="text-gray-400 text-xs uppercase mb-1">Loss Streak (แพ้ติดกัน)</div>
          <div class="text-2xl font-mono" :class="getStreakClass(riskMetrics.loss_streak, true)">
            {{ riskMetrics.loss_streak }}
          </div>
           <div class="text-xs mt-1 text-gray-500">
             {{ getLossText(riskMetrics.loss_streak) }}
          </div>
        </div>

        <!-- Win Streak -->
        <div class="bg-gray-900 border border-gray-800 rounded-xl p-4">
          <div class="text-gray-400 text-xs uppercase mb-1">Win Streak (ชนะติดกัน)</div>
          <div class="text-2xl font-mono text-emerald-400">
            {{ riskMetrics.win_streak }}
          </div>
          <div class="text-xs mt-1 text-emerald-600">
             {{ getWinText(riskMetrics.win_streak) }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  regimes: {
    type: Object,
    required: true,
    default: () => ({})
  },
  riskMetrics: {
    type: Object,
    default: null
  }
})

function formatDetails(details) {
  if (!details) return {}
  const { adx, atr_ratio, wick_ratio } = details
  return {
    ADX: adx,
    'ATR%': atr_ratio,
    'Wick': wick_ratio,
  }
}

// Helpers for Risk Visuals
function getDDClass(dd) {
  if (dd >= 10) return 'text-red-500'
  if (dd >= 5) return 'text-orange-400'
  return 'text-emerald-400'
}

function getDDText(dd) {
  if (dd >= 10) return '⚠️ โหมดเอาตัวรอด (Risk 50%)'
  if (dd >= 5) return '⚠️ ระมัดระวัง (Risk 75%)'
  return '✅ ปกติ (Normal)'
}

function getStreakClass(streak, isLoss) {
  if (isLoss) {
    if (streak >= 4) return 'text-red-600'
    if (streak >= 2) return 'text-orange-400'
    return 'text-gray-200'
  }
  return 'text-emerald-400'
}

function getLossText(streak) {
  if (streak >= 4) return '❄️ มือเย็นเฉียบ (Risk 25%)'
  if (streak >= 3) return '📉 เริ่มเป๋ (Risk 50%)'
  if (streak >= 2) return '📉 สะดุด (Risk 80%)'
  return 'ปกติ'
}

function getWinText(streak) {
  if (streak >= 3) return '🔥 มือขึ้น (Boost 1.1x)'
  return 'กำลังสะสม...'
}
</script>
