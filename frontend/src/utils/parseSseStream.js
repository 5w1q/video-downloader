/**
 * 消费 fetch 的 text/event-stream，按 SSE 块（以空行分隔）解析 event / data。
 * 兼容 data 跨多行、\r\n，并在流结束时冲刷末尾不完整块。
 */
function dispatchSseBlock(block, onEvent) {
  let eventName = ''
  const dataLines = []
  for (const raw of block.split('\n')) {
    const line = raw.replace(/\r$/, '')
    if (!line || line.startsWith(':')) continue
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trimStart()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }
  if (!dataLines.length) return
  const data = dataLines.join('\n')
  onEvent(eventName || 'message', data)
}

function flushCompleteBlocks(buffer, onEvent) {
  const normalized = buffer.replace(/\r\n/g, '\n')
  const parts = normalized.split('\n\n')
  const rest = parts.pop() ?? ''
  for (const block of parts) {
    if (block.trim()) dispatchSseBlock(block, onEvent)
  }
  return rest
}

export async function consumeFetchSse(response, onEvent) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    buffer = flushCompleteBlocks(buffer, onEvent)
  }
  buffer = flushCompleteBlocks(buffer, onEvent)
  if (buffer.trim()) {
    dispatchSseBlock(buffer, onEvent)
  }
}
