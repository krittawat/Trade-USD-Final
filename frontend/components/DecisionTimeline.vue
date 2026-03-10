<template>
  <div class="space-y-3">
    <div v-if="!decisions.length" class="text-gray-500 text-sm">
      ไม่มีข้อมูลการตัดสินใจเร็วๆ นี้ — กำลังรอการประเมินรอบต่อไป...
    </div>
    
    <div v-else class="flex flex-col gap-2 max-h-[400px] overflow-y-auto pr-2 custom-scrollbar">
      <div v-for="d in decisions" :key="d.id" 
           class="p-3 bg-gray-800 rounded flex flex-col md:flex-row justify-between md:items-center text-sm border-l-4 shadow-sm"
           :class="d.result === 'ok' ? 'border-emerald-500' : 'border-yellow-500'">
        
        <div class="flex items-center gap-3 flex-wrap">
          <!-- Symbol & Time -->
          <span class="font-bold text-gray-200 min-w-[70px]">{{ d.symbol }}</span>
          <span class="text-gray-500 text-xs hidden md:inline">{{ formatTime(d.timestamp) }}</span>
          
          <!-- Stage & Result -->
          <div class="px-2 py-0.5 rounded text-xs tracking-wide uppercase font-semibold"
               :class="d.result === 'ok' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-yellow-500/10 text-yellow-400'">
            {{ d.stage }} : {{ d.result }}
          </div>
          
          <!-- Reason -->
          <span class="text-gray-300 break-all">
            {{ d.reason || 'ผ่านเงื่อนไขปกติ' }}
          </span>
        </div>
        
        <!-- Mobile Time -->
        <div class="text-gray-500 text-xs mt-2 md:mt-0 text-right md:hidden">
          {{ formatTime(d.timestamp) }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

const decisions = ref([])
let pollInterval = null

async function fetchDecisions() {
  try {
    const res = await fetch('http://localhost:8000/api/decisions?limit=15')
    if (res.ok) {
      const data = await res.json()
      decisions.value = data.decisions || []
    }
  } catch (e) {
    console.error('Failed to fetch decisions:', e)
  }
}

function formatTime(ts) {
  if (!ts) return ''
  const date = new Date(ts)
  return date.toLocaleTimeString('th-TH', { hour12: false })
}

onMounted(() => {
  fetchDecisions()
  pollInterval = setInterval(fetchDecisions, 3000)
})

onUnmounted(() => {
  if (pollInterval) clearInterval(pollInterval)
})
</script>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: #1f2937; /* gray-800 */
  border-radius: 4px;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #374151; /* gray-700 */
  border-radius: 4px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #4b5563; /* gray-600 */
}
</style>
