import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import * as moduleApi from 'node:module';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const source = await readFile(new URL('../src/features/langgraph/utils/streamUiHelpers.ts', import.meta.url), 'utf8');
let javascript;
if (moduleApi.stripTypeScriptTypes) javascript = moduleApi.stripTypeScriptTypes(source);
else {
  const ts = createRequire(import.meta.url)('typescript');
  javascript = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
}
const { mergeHistoryToolMessages, projectRuntimeProgress, clearedStreamSnapshot, absorbToolResultEvent, toolEventKey, isNoopCompletedTask, shouldAppendTaskTerminalMessage } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`);
const tool = (id, extra = {}) => ({ content: JSON.stringify({ status: 'succeeded', operation_id: `op-${id}`, data: {}, summary: '已观察' }), messageType: 'tool', toolName: 'execute_skill_script', toolCallId: id, time: '10:00', ...extra });

test('one history card contains all 37 real calls across steps and narration', () => {
  const messages = [{ messageType: 'human', content: '执行任务', time: '10:00' }];
  for (let i = 0; i < 37; i++) messages.push(
    { messageType: 'step_separator', content: `步骤 ${i}`, time: '10:00' },
    { messageType: 'ai', content: `正在检查 ${i}`, time: '10:00' }, tool(`c${i}`));
  messages.push({ messageType: 'ai', content: '已完成报告，商城加购失败。', time: '10:01' });
  const history = mergeHistoryToolMessages(messages);
  assert.equal(history.filter(x => x.isExecutionLog).length, 1);
  assert.equal(history.find(x => x.isExecutionLog).toolEntries.length, 37);
  assert.equal(history.filter(x => x.isProgress).length, 1);
  assert.equal(history.at(-1).content, '已完成报告，商城加购失败。');
  assert.equal(mergeHistoryToolMessages(history).find(x => x.isExecutionLog).toolEntries.length, 37);
  history.find(x => x.isExecutionLog).isExpanded = true;
  assert.equal(mergeHistoryToolMessages(history).find(x => x.isExecutionLog).isExpanded, true);
});

test('distinct user turns retain their own execution records', () => {
  const history = mergeHistoryToolMessages([
    { messageType: 'human', content: '第一轮', time: '1' }, tool('same'),
    { messageType: 'ai', content: '第一轮报告', time: '2' },
    { messageType: 'human', content: '第二轮', time: '3' }, tool('same'),
  ]);
  assert.equal(history.filter(x => x.isExecutionLog).length, 2);
});

test('replayed notifications dedupe, distinct calls with same business operation do not', () => {
  const a = tool('a'); const b = { ...tool('b'), content: a.content };
  const history = mergeHistoryToolMessages([a, { ...a }, b]);
  assert.equal(history.find(x => x.isExecutionLog).toolEntries.length, 2);
  assert.notEqual(toolEventKey({ tool_call_id: 'a', operation_id: 'same' }), toolEventKey({ tool_call_id: 'b', operation_id: 'same' }));
});

test('live records and reconstructed history have the same cardinality', () => {
  const stream = { content: '', messages: [] };
  for (let i = 0; i < 30; i++) absorbToolResultEvent(stream,
    { type: 'tool_result', tool_call_id: `c${i}`, step: i, tool_name: 'execute_skill_script' },
    { time: '10:00', content: `result-${i}`, summary: '已观察' });
  assert.equal(stream.messages.filter(x => x.isExecutionLog).length, 1);
  assert.equal(stream.messages.find(x => x.isExecutionLog).toolEntries.length, 30);
});

test('progress is projected from persisted counts, independent of model rounds', () => {
  const stream = { effectiveConfig: { used_tool_actions: 0 } };
  projectRuntimeProgress(stream, { type: 'step_start', step: 1, tools: ['a', 'b', 'c'] });
  assert.equal(stream.effectiveConfig.used_tool_actions, 0);
  const snapshot = { type: 'task_status', status: 'running', payload: { stage: 'add_to_cart' }, effective_config: { used_tool_actions: 3, completed_business_steps: 2 } };
  projectRuntimeProgress(stream, snapshot); projectRuntimeProgress(stream, snapshot);
  assert.equal(stream.effectiveConfig.used_tool_actions, 3);
  assert.equal(stream.currentStage, 'add_to_cart');
  assert.equal(stream.effectiveConfig.completed_business_steps, 2);
});

test('canonical final reports replace once and cancelled state cannot revive', () => {
  const stream = { content: '中间文字', taskStatus: 'cancelled' };
  projectRuntimeProgress(stream, { type: 'task_status', status: 'running', payload: { stage: 'other' } });
  assert.equal(stream.taskStatus, 'cancelled');
  const report = { type: 'final_answer', content: '真实测试报告' };
  projectRuntimeProgress(stream, report); projectRuntimeProgress(stream, report);
  assert.equal(stream.content, '真实测试报告');
});

test('solidifying a completed stream retains progress without replaying messages', () => {
  const snapshot = clearedStreamSnapshot({ taskId: 't1', taskStatus: 'completed', content: '最终报告', messages: [tool('a')],
    currentStage: 'verify_deliver', effectiveConfig: { used_tool_actions: 10, completed_business_steps: 4 },
    acceptance: { test_result: 'failed' }, isWaitingForApproval: true });
  assert.equal(snapshot.taskStatus, 'completed');
  assert.equal(snapshot.effectiveConfig.used_tool_actions, 10);
  assert.equal(snapshot.effectiveConfig.completed_business_steps, 4);
  assert.equal(snapshot.acceptance.test_result, 'failed');
  assert.equal(snapshot.snapshotIdle, true); assert.equal(snapshot.liveGeneration, false);
  assert.equal(snapshot.content, ''); assert.deepEqual(snapshot.messages, []);
  assert.equal(snapshot.isWaitingForApproval, undefined);
});

test('a completed single chat turn with zero work is not shown as a completed task', () => {
  assert.equal(isNoopCompletedTask('completed', [{ stage_id: 'single' }], {
    expected_business_steps: 0, completed_business_steps: 0, used_tool_actions: 0,
  }), true);
  assert.equal(isNoopCompletedTask('completed', [{ stage_id: 'single' }], {
    expected_business_steps: 1, completed_business_steps: 0, used_tool_actions: 0,
  }), true);
  assert.equal(isNoopCompletedTask('running', [{ stage_id: 'single' }], {
    expected_business_steps: 0, completed_business_steps: 0, used_tool_actions: 0,
  }), false);
});

test('deferred-executor and exhausted-budget details do not create duplicate chat bubbles', () => {
  assert.equal(shouldAppendTaskTerminalMessage({
    type: 'task_blocked',
    acceptance: { blockers: [{ blocker_code: 'assistant_deferred_executor_mount' }] },
  }), false);
  assert.equal(shouldAppendTaskTerminalMessage({
    type: 'task_blocked',
    acceptance: { blockers: [{ blocker_code: 'execution_budget_exhausted_no_progress' }] },
  }), false);
  assert.equal(shouldAppendTaskTerminalMessage({
    type: 'task_status', status: 'ambiguous',
  }), true);
});
