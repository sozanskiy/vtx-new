export function useApi() {
  const getCandidates = (limit = 10) => $fetch(`/api/candidates?limit=${limit}`)
  const startScan = (plan = '58g_default') => $fetch('/api/scan/start', { method: 'POST', body: { plan } })
  const stopScan = () => $fetch('/api/scan/stop', { method: 'POST' })
  const scanStatus = () => $fetch('/api/scan/status')
  const focus = (freq_hz: number) => $fetch('/api/focus', { method: 'POST', body: { freq_hz } })
  const stopFocus = async () => {
    try {
      return await $fetch('/api/focus', { method: 'DELETE' })
    } catch {
      return await $fetch('/api/focus/stop', { method: 'POST' })
    }
  }
  const record = (type: 'iq' | 'video', enable: boolean) => $fetch('/api/record', { method: 'POST', body: { type, enable } })
  const getConfig = () => $fetch('/api/config')
  const putConfig = (cfg: any) => $fetch('/api/config', { method: 'PUT', body: cfg })
  return { getCandidates, startScan, stopScan, scanStatus, focus, stopFocus, record, getConfig, putConfig }
}

