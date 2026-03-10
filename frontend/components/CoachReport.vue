<template>
  <div class="bg-gray-900 border border-gray-800 rounded-xl p-6 shadow-lg">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-lg font-bold flex items-center gap-2">
        <span class="text-2xl">🏋️</span> 
        <span class="bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-purple-400">
          Auto Coach AI
        </span>
      </h2>
      <button 
        @click="fetchReport" 
        class="text-xs px-3 py-1 bg-gray-800 hover:bg-gray-700 rounded-full transition-colors text-gray-400"
        :disabled="loading"
      >
        {{ loading ? 'วิเคราะห์...' : 'รีเฟรช' }}
      </button>
    </div>

    <!-- Identity Header -->
    <div v-if="performance && !loading && !error" class="mb-6 p-4 bg-gradient-to-br from-gray-800 to-gray-900 rounded-xl border border-gray-700 relative overflow-hidden group">
      <div class="absolute inset-0 bg-gradient-to-r from-blue-500/10 to-purple-500/10 opacity-0 group-hover:opacity-100 transition-opacity"></div>
      <div class="relative flex items-center gap-4">
          <div class="text-4xl bg-gray-900/50 p-3 rounded-2xl shadow-inner border border-gray-700/50">
            {{ performance.identity?.emoji || '🤖' }}
          </div>
          <div class="flex-1">
              <div class="flex items-center gap-2 mb-1">
                <span class="text-[10px] text-blue-400 font-bold tracking-widest uppercase bg-blue-500/10 px-2 py-0.5 rounded border border-blue-500/20">
                  Trader Profile
                </span>
              </div>
              <h3 class="text-xl font-bold text-white tracking-tight">
                {{ performance.identity || 'กำลังวิเคราะห์...' }}
              </h3>
              <!-- <p class="text-xs text-gray-400 mt-1 line-clamp-1">Based on recent {{ performance.total_trades || 0 }} trades</p> -->
          </div>
          <div class="text-right pl-4 border-l border-gray-700/50">
              <div class="text-3xl font-black text-transparent bg-clip-text bg-gradient-to-br from-emerald-400 to-teal-300">
                {{ performance.session_score || 0 }}
              </div>
              <div class="text-[10px] text-gray-500 uppercase font-medium">Performance Score</div>
          </div>
      </div>
    </div>

    <!-- Loading State -->
    <div v-if="loading" class="animate-pulse space-y-3">
      <div class="h-4 bg-gray-800 rounded w-3/4"></div>
      <div class="h-4 bg-gray-800 rounded w-1/2"></div>
      <div class="h-32 bg-gray-800 rounded w-full"></div>
    </div>

    <!-- Error State -->
    <div v-else-if="error" class="text-red-400 text-sm p-4 bg-red-900/20 rounded-lg border border-red-900/50">
      ⚠️ {{ error }}
    </div>

    <!-- Report Content -->
    <div v-else class="prose prose-invert max-w-none prose-sm">
      <div v-if="reportMarkdown" v-html="renderedReport" class="coach-markdown space-y-2"></div>
      <div v-else class="text-gray-500 italic text-center py-8">
        ยังไม่มีข้อมูลการวิเคราะห์ โปรดเทรดเพิ่มเพื่อให้โค้ชทำงาน!
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: true
})

const reportMarkdown = ref('')
const loading = ref(false)
const error = ref(null)

const renderedReport = computed(() => {
  if (!reportMarkdown.value) return ''
  // Basic markdown rendering
  return md.render(reportMarkdown.value)
})

const performance = ref(null)

async function fetchReport() {
  loading.value = true
  error.value = null
  try {
    const res = await fetch('http://localhost:8000/api/coach/report')
    if (!res.ok) throw new Error('Failed to fetch report')
    const data = await res.json()
    
    // Check if we have structured data
    if (data.performance_summary) {
        performance.value = data.performance_summary
    }

    // Markdown content (fallback or detail)
    // If structured, we might optionally construct markdown or expect it
    // backend's AutoCoach.to_dict() doesn't include markdown text, so we rely on what's available
    // OR: backend status.py logic might just send dict.
    // If no markdown in JSON, we can hide the detail section or show a summary?
    // Let's assume report_markdown might be added or we just rely on identity for now.
    
    // For MVP: If data has markdown, use it. If not, maybe just show identity?
    if (data.report_markdown) {
      reportMarkdown.value = data.report_markdown
    } else {
       // If we have performance but no markdown, maybe we don't clear it?
       // Or set empty.
       reportMarkdown.value = ''
    }
  } catch (err) {
    console.error(err)
    error.value = 'Auto Coach ไม่พร้อมใช้งาน'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  fetchReport()
  // Auto-refresh every 5 minutes
  setInterval(fetchReport, 300000)
})
</script>

<style>
.coach-markdown h1 {
  @apply text-xl font-bold text-gray-100 mb-4 border-b border-gray-700 pb-2;
}
.coach-markdown h2 {
  @apply text-lg font-semibold text-emerald-400 mt-6 mb-3;
}
.coach-markdown h3 {
  @apply text-base font-semibold text-blue-400 mt-4 mb-2;
}
.coach-markdown ul {
  @apply list-disc list-inside space-y-1 text-gray-300 ml-2;
}
.coach-markdown li {
  @apply mb-1;
}
.coach-markdown strong {
  @apply text-gray-100 font-bold;
}
.coach-markdown code {
  @apply bg-gray-800 px-1 py-0.5 rounded text-yellow-300 font-mono text-xs;
}
.coach-markdown blockquote {
  @apply border-l-4 border-gray-700 pl-4 py-1 my-4 bg-gray-800/50 rounded-r text-gray-400 italic;
}
</style>
