/** Agent 流式 UI：中文进度、执行记录汇总、事件去重。 */

export interface ToolLogEntry {
  key: string;
  toolName: string;
  summary: string;
  detail: string;
  time: string;
  step?: number;
  retryOf?: string;
}

export interface StreamUiMessage {
  content: string;
  type: 'human' | 'ai' | 'tool' | 'system' | 'agent_step';
  time: string;
  toolName?: string;
  imageDataUrl?: string;
  fileAttachments?: unknown[];
  isExpanded?: boolean;
  isThinkingProcess?: boolean;
  isThinkingExpanded?: boolean;
  isProgress?: boolean;
  isExecutionLog?: boolean;
  toolEntries?: ToolLogEntry[];
  eventKey?: string;
  stepNumber?: number;
  maxSteps?: number;
  stepStatus?: 'start' | 'complete' | 'error';
}

export function isNoopCompletedTask(
  status: unknown,
  stages: Array<{ stage_id?: string }> = [],
  config: Record<string, unknown> = {},
): boolean {
  const terminal = ['completed', 'task_completed'].includes(String(status || ''));
  if (!terminal || stages.length === 0) return false;
  const nonBusinessStages = stages.every((s) =>
    ['single', 'summarize', ''].includes(String(s?.stage_id || '')),
  );
  return nonBusinessStages
    && Number(config.completed_business_steps || 0) === 0
    && Number(config.used_tool_actions || 0) === 0;
}

export interface StreamUiState {
  content: string;
  messages: StreamUiMessage[];
  seenToolEventKeys?: Set<string>;
  currentStep?: number;
  maxSteps?: number;
}

const TOOL_LABELS: Record<string, string> = {
  knowledge_search: '知识库检索',
  read_skill_content: '读取 Skill 说明',
  execute_skill_script: '执行 Skill',
  request_tool_expansion: '申请扩展工具',
  assert_task_postcondition: '验收断言',
  report_task_blocked: '报告阻塞',
};

export function toolLabel(name?: string): string {
  const n = (name || '').trim();
  if (!n) return '工具';
  return TOOL_LABELS[n] || n;
}

/** 粗判：整段几乎是英文工具旁白（非最终答复）。 */
export function looksLikeEnglishToolNarration(text: string): boolean {
  const t = (text || '').trim();
  if (!t || t.length < 8) return false;
  const latin = (t.match(/[A-Za-z]/g) || []).length;
  const cjk = (t.match(/[\u4e00-\u9fff]/g) || []).length;
  if (cjk >= 4) return false;
  if (latin < 12) return false;
  const cues =
    /\b(I('ll| will)|Let me|Now I|Calling|Using the|tool|search|execute|please wait|I'm going to|I am going to)\b/i;
  return cues.test(t) || latin / Math.max(t.length, 1) > 0.55;
}

export function buildProgressText(opts: {
  toolName?: string;
  step?: number;
  maxSteps?: number;
  interim?: string;
}): string {
  const label = toolLabel(opts.toolName);
  const stepPart =
    opts.step != null
      ? opts.maxSteps != null
        ? `（步骤 ${opts.step}/${opts.maxSteps}）`
        : `（步骤 ${opts.step}）`
      : '';
  if (opts.interim && !looksLikeEnglishToolNarration(opts.interim)) {
    const short = opts.interim.replace(/\s+/g, ' ').trim().slice(0, 80);
    if (short) return `进度：${short}${stepPart}`;
  }
  return `正在执行：${label}${stepPart}`;
}

function simpleHash(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h).toString(36);
}

export function toolEventKey(parsed: Record<string, unknown>): string {
  // D09/RC13：业务操作身份优先于传输 event_id，避免同操作不同传输 ID 重复、
  // 或不同操作仅前 200 字相同被误合并。
  const opId = parsed.operation_id ?? parsed.operationId;
  const toolCallId = parsed.tool_call_id ?? parsed.toolCallId;
  const attempt =
    parsed.attempt_id ?? parsed.attemptId ?? parsed.attempt ?? parsed.retry_of;
  const tool = String(parsed.tool_name || parsed.toolName || '');
  const step = String(parsed.step ?? parsed.step_id ?? '');
  if (typeof toolCallId === 'string' && toolCallId.trim()) {
    return `tc:${toolCallId.trim()}|${tool}|a:${String(attempt ?? '1')}`;
  }
  if (typeof opId === 'string' && opId.trim()) {
    return `op:${opId.trim()}|${tool}|${step}|a:${String(attempt ?? '1')}`;
  }
  const eid = parsed.event_id ?? parsed.eventId;
  if (typeof eid === 'string' && eid.trim()) {
    // 传输身份：仅在无业务操作身份时用于去重重放
    return `tx:${eid.trim()}`;
  }
  const summary = String(parsed.summary || '').slice(0, 120);
  const output = parsed.tool_output ?? parsed.content;
  let body = '';
  try {
    body =
      typeof output === 'string'
        ? output.slice(0, 200)
        : JSON.stringify(output ?? '').slice(0, 200);
  } catch {
    body = String(output ?? '').slice(0, 200);
  }
  return `fb:${tool}|${step}|${simpleHash(summary + '|' + body)}|a:${String(attempt ?? '1')}`;
}

export function upsertProgressMessage(
  stream: StreamUiState,
  text: string,
  time: string
): void {
  const idx = stream.messages.findIndex((m) => m.isProgress);
  if (idx >= 0) {
    stream.messages[idx].content = text;
    stream.messages[idx].time = time;
    return;
  }
  stream.messages.push({
    content: text,
    type: 'system',
    time,
    isProgress: true,
    isThinkingProcess: true,
    isThinkingExpanded: false,
  });
}

export function upsertExecutionLogEntry(
  stream: StreamUiState,
  entry: ToolLogEntry,
  time: string
): boolean {
  if (!stream.seenToolEventKeys) {
    stream.seenToolEventKeys = new Set();
  }
  if (stream.seenToolEventKeys.has(entry.key)) {
    return false; // 重复事件：只显示一次
  }
  stream.seenToolEventKeys.add(entry.key);

  const idx = stream.messages.findIndex((m) => m.isExecutionLog);
  if (idx >= 0) {
    const log = stream.messages[idx];
    const entries = log.toolEntries || [];
    entries.push(entry);
    log.toolEntries = entries;
    log.content = formatExecutionLogContent(entries);
    log.time = time;
    // D13/RC13：追加条目时保留用户已展开状态，不强制折叠
    return true;
  }
  stream.messages.push({
    content: formatExecutionLogContent([entry]),
    type: 'tool',
    time,
    toolName: '执行记录',
    isExpanded: false,
    isExecutionLog: true,
    toolEntries: [entry],
  });
  return true;
}

export function formatExecutionLogContent(entries: ToolLogEntry[]): string {
  const lines = entries.map((e, i) => {
    const label = toolLabel(e.toolName);
    const step = e.step != null ? ` #${e.step}` : '';
    const sum = (e.summary || '').replace(/\s+/g, ' ').trim().slice(0, 100);
    return `${i + 1}. ${label}${step}${sum ? ` — ${sum}` : ''}`;
  });
  return `执行记录（${entries.length}）\n${lines.join('\n')}`;
}

/**
 * 工具结果到达时：把模型英文旁白/中间说明降级为进度，并写入折叠执行记录。
 * 返回是否写入了新条目（false=去重跳过）。
 */
export function absorbToolResultEvent(
  stream: StreamUiState,
  parsed: Record<string, unknown>,
  opts: {
    time: string;
    content: string;
    imageDataUrl?: string;
    summary?: string;
  }
): boolean {
  const toolName =
    typeof parsed.tool_name === 'string'
      ? parsed.tool_name
      : typeof parsed.toolName === 'string'
        ? parsed.toolName
        : '';
  const step = normalizeStep(parsed.step);
  const key = toolEventKey(parsed);
  const interim = (stream.content || '').trim();
  upsertProgressMessage(
    stream,
    buildProgressText({
      toolName,
      step,
      maxSteps: stream.maxSteps,
      interim,
    }),
    opts.time
  );
  // 中间旁白不得固化成普通 AI 回复
  stream.content = '';

  const summary =
    (opts.summary || '').trim() ||
    opts.content.replace(/\s+/g, ' ').trim().slice(0, 160) ||
    (opts.imageDataUrl ? '[图片]' : '');

  return upsertExecutionLogEntry(
    stream,
    {
      key,
      toolName,
      summary,
      detail: opts.content || summary,
      time: opts.time,
      step,
    },
    opts.time
  );
}

function normalizeStep(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const n = Number(value);
    if (Number.isFinite(n)) return n;
  }
  return undefined;
}

/** Initial execution and approval resume consume the same persisted projection. */
export function clearedStreamSnapshot(stream: Record<string, any>): Record<string, any> | undefined {
  if (!stream.taskId) return undefined;
  const snapshot: Record<string, any> = { content: '', messages: [], isComplete: true, liveGeneration: false, snapshotIdle: true };
  for (const key of ['taskId', 'taskStatus', 'currentStage', 'stagePlan', 'acceptance', 'effectiveConfig', 'taskEvents', 'responseLocale']) {
    if (stream[key] !== undefined) snapshot[key] = stream[key];
  }
  return snapshot;
}

export function projectRuntimeProgress(stream: Record<string, any>, event: Record<string, any>): void {
  if (event.type === 'final_answer') {
    stream.content = String(event.content || '');
    return;
  }
  if (!['task_started', 'task_status', 'task_completed', 'task_blocked', 'task_cancelling', 'step_result', 'stage_changed'].includes(event.type)) return;
  const payload = event.payload || {};
  const current = String(stream.taskStatus || '');
  const status = String(event.status || '');
  const terminal = ['completed', 'cancelled', 'failed'];
  if (terminal.includes(current) && status && current !== status) return;
  if (status) stream.taskStatus = status;
  stream.taskId = event.task_id || stream.taskId;
  stream.currentStage = payload.stage || payload.current_stage || stream.currentStage;
  if (Array.isArray(payload.stage_plan)) stream.stagePlan = payload.stage_plan;
  const config = event.effective_config || payload.effective_config;
  if (config) stream.effectiveConfig = { ...(stream.effectiveConfig || {}), ...config };
  if (payload.completed_business_steps != null) {
    stream.effectiveConfig = { ...(stream.effectiveConfig || {}), completed_business_steps: Number(payload.completed_business_steps) };
  }
  if (event.acceptance || payload.acceptance) stream.acceptance = event.acceptance || payload.acceptance;
}

/** Project one execution card per user turn, including stop/reload history. */
export function mergeHistoryToolMessages<
  T extends {
    messageType?: string; content: string; toolName?: string; isExpanded?: boolean;
    isExecutionLog?: boolean; toolEntries?: ToolLogEntry[]; time: string;
    toolCallId?: string; isProgress?: boolean; progressNotes?: string[];
  },
>(messages: T[]): T[] {
  const out: T[] = [];
  let turn: T[] = [];
  let turnNumber = 0;
  const flush = () => {
    if (!turn.length) return;
    const lastTool = turn.reduce((last, m, i) => m.messageType === 'tool' ? i : last, -1);
    if (lastTool < 0) { out.push(...turn.filter(m => m.messageType !== 'step_separator')); turn = []; return; }
    const entries: ToolLogEntry[] = [];
    const seen = new Set<string>();
    const notes: string[] = [];
    let log: T | undefined;
    let progress: T | undefined;
    for (const [index, m] of turn.entries()) {
      if (m.messageType === 'step_separator') continue;
      if (m.messageType === 'tool') {
        notes.push(...(m.progressNotes || []));
        if (!log) {
          log = { ...m, content: '', toolName: '执行记录', isExecutionLog: true, isExpanded: Boolean(m.isExpanded), toolEntries: entries };
          out.push(log);
        }
        let payload: Record<string, any> = {};
        try { payload = JSON.parse(m.content); } catch { /* historical plain text */ }
        const key = m.toolCallId ? `call:${m.toolCallId}` : payload.operation_id
          ? `op:${payload.operation_id}` : `hist:${turnNumber}:${index}`;
        const incoming = m.isExecutionLog && m.toolEntries?.length ? m.toolEntries : [{
          key, toolName: m.toolName || payload.evidence?.tool_name || payload.evidence?.skill_name || 'tool',
          summary: String(payload.summary || m.content).replace(/\s+/g, ' ').trim().slice(0, 100),
          detail: m.content, time: m.time,
        }];
        for (const entry of incoming) {
          if (!seen.has(entry.key)) { seen.add(entry.key); entries.push(entry); }
        }
        continue;
      }
      if (index < lastTool && m.messageType === 'ai' && m.content.trim()) {
        notes.push(m.content);
        const text = looksLikeEnglishToolNarration(m.content) ? '正在执行任务，详情见执行记录。' : m.content;
        if (progress) { progress.content = text; progress.time = m.time; }
        else { progress = { ...m, messageType: 'system', content: text, isProgress: true }; out.push(progress); }
        continue;
      }
      out.push(m);
    }
    if (log) {
      log.content = formatExecutionLogContent(entries);
      Object.assign(log, { progressNotes: [...new Set(notes)] });
    }
    turn = [];
    turnNumber++;
  };
  for (const message of messages) {
    if (message.messageType === 'human') flush();
    turn.push(message);
  }
  flush();
  return out;
}
