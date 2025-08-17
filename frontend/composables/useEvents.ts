export function useEvents(onEvent: (e: any) => void) {
  const evt = new EventSource('/events')
  evt.onmessage = (m) => {
    try { onEvent(JSON.parse(m.data)) } catch { /* noop */ }
  }
  const stop = () => evt.close()
  return stop
}