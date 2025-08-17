<script setup lang="ts">
import { ref } from 'vue'
import { useApi } from '~/composables/useApi'
const api = useApi()
const iqRecording = ref(false)
const videoRecording = ref(false)
const configText = ref<string>('')
const putBusy = ref(false)
const configPlaceholder = `{
  "bands": [ ... ]
}`

async function toggleIq(){
  iqRecording.value = !iqRecording.value
  await api.record('iq', iqRecording.value)
}
async function toggleVideo(){
  videoRecording.value = !videoRecording.value
  await api.record('video', videoRecording.value)
}

async function loadConfig(){
  try { const cfg = await api.getConfig(); configText.value = JSON.stringify(cfg, null, 2) } catch {}
}

async function saveConfig(){
  if(!configText.value) return
  try {
    putBusy.value = true
    const cfg = JSON.parse(configText.value)
    await api.putConfig(cfg)
  } finally {
    putBusy.value = false
  }
}
</script>

<template>
  <section class="rounded-xl border border-gray-200 bg-white shadow-sm">
    <header class="px-4 py-3">
      <h2 class="text-lg font-semibold">Settings</h2>
    </header>
    <div class="px-4 pb-4 grid gap-4">
      <div class="flex items-center justify-between">
        <div>
          <div class="font-medium">IQ Recording</div>
          <div class="text-sm text-gray-500">Toggle .cfile/.cs16 recording during focus</div>
        </div>
        <button class="rounded px-3 py-1.5 border" :class="iqRecording ? 'bg-green-600 text-white border-green-700' : 'bg-white text-gray-700 border-gray-300'" @click="toggleIq()">
          {{ iqRecording ? 'On' : 'Off' }}
        </button>
      </div>
      <div class="flex items-center justify-between">
        <div>
          <div class="font-medium">Video Recording</div>
          <div class="text-sm text-gray-500">Toggle MJPEG→MP4 recording (optional)</div>
        </div>
        <button class="rounded px-3 py-1.5 border" :class="videoRecording ? 'bg-green-600 text-white border-green-700' : 'bg-white text-gray-700 border-gray-300'" @click="toggleVideo()">
          {{ videoRecording ? 'On' : 'Off' }}
        </button>
      </div>
      <div>
        <div class="flex items-center justify-between mb-2">
          <div>
            <div class="font-medium">Channel Plan JSON</div>
            <div class="text-sm text-gray-500">Edit and save to update scan frequencies</div>
          </div>
          <div class="flex gap-2">
            <button class="rounded bg-gray-100 px-3 py-1.5 border border-gray-300" @click="loadConfig()">Load</button>
            <button class="rounded bg-blue-600 px-3 py-1.5 text-white hover:bg-blue-700 disabled:opacity-50" :disabled="putBusy" @click="saveConfig()">Save</button>
          </div>
        </div>
        <textarea v-model="configText" rows="8" class="w-full rounded border border-gray-300 font-mono text-xs p-2" :placeholder="configPlaceholder"></textarea>
      </div>
    </div>
  </section>
</template>

