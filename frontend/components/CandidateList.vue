<script setup lang="ts">
interface Item { freq_hz:number; snr_db:number; power_dbm:number; last_seen:string; status:string }
defineProps<{ items: Item[] }>()
const emit = defineEmits<{ (e:'focus', freq_hz:number): void }>()
function onFocus(freq:number){ emit('focus', freq) }
</script>

<template>
  <section class="rounded-xl border border-gray-200 bg-white shadow-sm">
    <header class="flex items-center justify-between px-4 py-3">
      <h2 class="text-lg font-semibold">Candidates</h2>
    </header>
    <div class="overflow-x-auto">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-gray-600">
          <tr>
            <th class="px-3 py-2 text-left">#</th>
            <th class="px-3 py-2 text-left">Freq (MHz)</th>
            <th class="px-3 py-2 text-left">SNR (dB)</th>
            <th class="px-3 py-2 text-left">Power (dBm)</th>
            <th class="px-3 py-2 text-left">Last Seen</th>
            <th class="px-3 py-2 text-left">Status</th>
            <th class="px-3 py-2 text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(it, idx) in items" :key="it.freq_hz" class="border-t">
            <td class="px-3 py-2">{{ idx + 1 }}</td>
            <td class="px-3 py-2">{{ (it.freq_hz/1e6).toFixed(1) }}</td>
            <td class="px-3 py-2">{{ it.snr_db.toFixed(1) }}</td>
            <td class="px-3 py-2">{{ it.power_dbm.toFixed(1) }}</td>
            <td class="px-3 py-2">{{ new Date(it.last_seen).toLocaleTimeString() }}</td>
            <td class="px-3 py-2">
              <span :class="['px-2 py-0.5 rounded text-xs', it.status==='active' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600']">{{ it.status }}</span>
            </td>
            <td class="px-3 py-2 text-right">
              <button class="inline-flex items-center rounded bg-blue-600 px-3 py-1.5 text-white hover:bg-blue-700" @click="onFocus(it.freq_hz)">Focus</button>
            </td>
          </tr>
          <tr v-if="items.length === 0">
            <td colspan="7" class="px-3 py-6 text-center text-gray-500">No candidates yet. Start scanning…</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
  
</template>

