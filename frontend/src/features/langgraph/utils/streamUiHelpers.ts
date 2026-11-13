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
  const eid = parsed.event_id ?? parsed.eventId;
  if (typeof eid === 'string' && eid.trim()) return eid.trim();
  const tool = String(parsed.tool_name || parsed.toolName || '');
  const step = String(parsed.step ?? '');
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
  return `${tool}|${step}|${simpleHash(summary + '|' + body)}`;
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
    log.isExpanded = false;
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

/** 历史里连续 tool 消息合并为一张执行记录。 */
export function mergeHistoryToolMessages<
  T extends {
    messageType?: string;
    content: string;
    toolName?: string;
    isExpanded?: boolean;
    isExecutionLog?: boolean;
    toolEntries?: ToolLogEntry[];
    time: string;
  },
>(messages: T[]): T[] {
  const out: T[] = [];
  let buf: T[] = [];

  const flush = () => {
    if (buf.length === 0) return;
    if (buf.length === 1) {
      out.push({ ...buf[0], isExpanded: false });
      buf = [];
      return;
    }
    const entries: ToolLogEntry[] = buf.map((m, i) => ({
      key: `hist-${m.time}-${i}-${simpleHash(m.content.slice(0, 80))}`,
      toolName: m.toolName || 'tool',
      summary: m.content.replace(/\s+/g, ' ').trim().slice(0, 100),
      detail: m.content,
      time: m.time,
    }));
    const first = buf[0];
    out.push({
      ...first,
      content: formatExecutionLogContent(entries),
      toolName: '执行记录',
      isExpanded: false,
      isExecutionLog: true,
      toolEntries: entries,
    });
    buf = [];
  };

  for (const m of messages) {
    if (m.messageType === 'tool' && !m.isExecutionLog) {
      buf.push(m);
    } else {
      flush();
      out.push(m);
    }
  }
  flush();
  return out;
}
