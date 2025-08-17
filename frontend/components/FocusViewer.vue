<script setup lang="ts">
defineProps<{ src: string }>()
const emit = defineEmits<{ (e:'stop-focus'): void }>()
</script>

<template>
  <section class="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
    <header class="flex items-center justify-between px-4 py-3">
      <h2 class="text-lg font-semibold">Focus Preview</h2>
      <button class="inline-flex items-center rounded bg-gray-700 px-3 py-1.5 text-white hover:bg-black" @click="emit('stop-focus')">Stop Focus</button>
    </header>
    <div class="bg-black">
      <img :src="src" alt="VTX preview" class="w-full max-h-[70vh] object-contain" />
    </div>
  </section>
</template>

