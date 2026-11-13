import { describe, expect, it } from 'vitest';
import {
  absorbToolResultEvent,
  looksLikeEnglishToolNarration,
  mergeHistoryToolMessages,
  toolEventKey,
} from './streamUiHelpers';

describe('streamUiHelpers', () => {
  it('detects english tool narration', () => {
    expect(
      looksLikeEnglishToolNarration("I'll now call the knowledge_search tool.")
    ).toBe(true);
    expect(looksLikeEnglishToolNarration('正在检索知识库并整理答案。')).toBe(
      false
    );
  });

  it('dedupes identical tool events into one log entry', () => {
    const stream = { content: "I'll search now", messages: [] as any[] };
    const parsed = {
      type: 'tool_result',
      event_id: 'tr-same',
      tool_name: 'knowledge_search',
      summary: 'ok',
      step: 1,
      tool_output: '{"hits":1}',
    };
    expect(
      absorbToolResultEvent(stream, parsed, {
        time: '10:00',
        content: '{"hits":1}',
        summary: 'ok',
      })
    ).toBe(true);
    expect(
      absorbToolResultEvent(stream, parsed, {
        time: '10:00',
        content: '{"hits":1}',
        summary: 'ok',
      })
    ).toBe(false);
    expect(stream.content).toBe('');
    expect(stream.messages.filter((m) => m.isExecutionLog)).toHaveLength(1);
    expect(stream.messages.find((m) => m.isExecutionLog)?.toolEntries).toHaveLength(
      1
    );
    expect(stream.messages.some((m) => m.isProgress)).toBe(true);
  });

  it('keeps distinct tool results as separate entries in one card', () => {
    const stream = { content: '', messages: [] as any[] };
    for (let i = 0; i < 30; i++) {
      absorbToolResultEvent(
        stream,
        {
          event_id: `tr-${i}`,
          tool_name: 'execute_skill_script',
          summary: `s${i}`,
          step: i,
          tool_output: `out-${i}`,
        },
        { time: '10:00', content: `out-${i}`, summary: `s${i}` }
      );
    }
    const log = stream.messages.find((m) => m.isExecutionLog);
    expect(log?.toolEntries).toHaveLength(30);
    expect(stream.messages.filter((m) => m.isExecutionLog)).toHaveLength(1);
  });

  it('merges consecutive history tool messages', () => {
    const merged = mergeHistoryToolMessages([
      { content: 'a', messageType: 'tool', toolName: 't1', time: '1' },
      { content: 'b', messageType: 'tool', toolName: 't2', time: '2' },
      { content: 'final', messageType: 'ai', time: '3' },
    ]);
    expect(merged).toHaveLength(2);
    expect(merged[0].isExecutionLog).toBe(true);
    expect(merged[0].toolEntries).toHaveLength(2);
  });

  it('builds stable tool event keys', () => {
    expect(
      toolEventKey({ event_id: 'x', tool_name: 'a', summary: 'b' })
    ).toBe('x');
  });
});
