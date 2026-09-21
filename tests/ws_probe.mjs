const ws = new WebSocket('ws://127.0.0.1:8100/ws/execute');
const t0 = Date.now();
const types = [];
const timed = [];
ws.onopen = () => ws.send(JSON.stringify({ goal: 'Research three independent topics (solar panel efficiency, EV battery chemistry, grid-scale storage) in one short paragraph each, then merge the three paragraphs into one summary.' }));
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  types.push(msg.type);
  if (['task_started', 'task_worker_completed', 'task_review_passed', 'task_review_failed', 'tool_execution', 'task_retry', 'run_completed'].includes(msg.type)) {
    const d = msg.data || {};
    timed.push(`${msg.type}[${d.task_id || d.tool_name || ''}] @${Date.now() - t0}ms`);
  }
  if (msg.type === 'task_retry') {
    console.log('=== TASK_RETRY PAYLOAD ===');
    console.log(JSON.stringify(msg, null, 2));
  }
  if (msg.type === 'task_fallback_exhausted') {
    console.log('=== FALLBACK EXHAUSTED PAYLOAD ===');
    console.log(JSON.stringify(msg, null, 2));
  }
  if (msg.type === 'run_completed') {
    console.log('final_output present:', !!(msg.data && msg.data.final_output && msg.data.final_output.length > 0));
  }
  if (['execution_completed', 'execution_complete', 'execution_failed', 'error'].includes(msg.type)) {
    console.log('terminal event:', msg.type);
    console.log('Timed lifecycle sequence:');
    for (const line of timed) console.log('  ' + line);
    ws.close();
    process.exit(0);
  }
};
ws.onerror = (err) => { console.error('WS error', err.message || err); process.exit(1); };
setTimeout(() => { console.error('TIMEOUT'); process.exit(1); }, 540000);
