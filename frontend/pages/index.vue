<script setup lang="ts">
import { ref, onMounted } from 'vue'
import CandidateList from '~/components/CandidateList.vue'
import FocusViewer from '~/components/FocusViewer.vue'
import SettingsPane from '~/components/SettingsPane.vue'
import { useApi } from '~/composables/useApi'
import { useEvents } from '~/composables/useEvents'

const api = useApi()
const items = ref<any[]>([])
const scanning = ref(false)
const focused = ref(false)
const videoUrl = ref<string | null>(null)

async function refresh(){
  items.value = await api.getCandidates(10)
}

async function startScan(){
  await api.startScan()
  // scanning will update via SSE 'scan_state'
}

async function stopScan(){
  await api.stopScan()
  // scanning will update via SSE 'scan_state'
}

async function onFocus(freq_hz:number){
  focused.value = true
  const r:any = await api.focus(freq_hz)
  videoUrl.value = r.video_url
}

async function stopFocus(){
  // Immediately update UI; backend/SSE will confirm state
  focused.value = false
  videoUrl.value = null
  try { await api.stopFocus() } catch {}
  // Ensure scanning resumes regardless of server memory of previous state
  if(!scanning.value){
    try { await api.startScan() } catch {}
  }
}

onMounted(async () => {
  try {
    const st:any = await api.scanStatus()
    scanning.value = st.state === 'running'
  } catch {}
  // Initial candidate list (until SSE starts broadcasting)
  await refresh()
  useEvents((e:any)=>{
    if(e?.type === 'candidates'){
      items.value = Array.isArray(e.items) ? e.items : []
    } else if(e?.type === 'scan_state'){
      scanning.value = e.state === 'running'
    } else if(e?.type === 'focus_state'){
      focused.value = !!e.focused
      if(!e.focused){ videoUrl.value = null }
    }
  })
})
</script>

<template>
  <main class="mx-auto max-w-5xl p-4 grid gap-4">
    <header class="flex items-center justify-between">
      <h1 class="text-2xl font-bold">RER‑Kilo</h1>
      <div class="flex items-center gap-2">
        <button class="rounded bg-emerald-600 px-3 py-1.5 text-white hover:bg-emerald-700" :disabled="scanning" @click="startScan()">Start Scan</button>
        <button class="rounded bg-rose-600 px-3 py-1.5 text-white hover:bg-rose-700" :disabled="!scanning" @click="stopScan()">Stop Scan</button>
      </div>
    </header>

    <CandidateList :items="items" @focus="onFocus" />

    <FocusViewer v-if="focused && videoUrl" :src="videoUrl" @stop-focus="stopFocus" />

    <SettingsPane />
  </main>
</template>

