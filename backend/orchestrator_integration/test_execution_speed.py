"""Failure semantics and plan reuse for continuous browser execution."""
import json
from django.test import SimpleTestCase
from .browser_test_runtime import contract_for_goal, form_execution_hint, observe
from .tool_result import classify_skill_output
from .test_platform_progress import GOAL

class ExecutionSpeedTests(SimpleTestCase):
    def test_runtime_failure_after_large_dom_is_not_reported_as_success(self):
        raw = '\n'.join(json.dumps({'dom':i}) for i in range(120))
        raw += '\n' + json.dumps({'runtime_execution':{'status':'failed','error':'Identifier state'}})
        result = classify_skill_output(text=raw,skill_name='playwright-skill',exit_code=0)
        self.assertEqual(result.status,'failed')
        self.assertEqual(result.error_code,'browser_script_error')
        self.assertEqual(result.data['execution_status'],'failed')

    def test_form_plan_preserves_native_empty_assertion_even_without_click_in_wrapper(self):
        state = observe(contract_for_goal(GOAL),tool_name='execute_skill_script',result={'operation_id':'actual-op','data':{
            'browser_plan':{'action':'run_form_test','status':'completed','empty_check':{'passed':False,'errors':['POST 400']}}}})
        self.assertFalse(state['empty_check']['passed'])
        self.assertEqual(state['empty_check']['operation_id'],'actual-op')

    def test_current_dom_binding_drives_remaining_rows_only(self):
        state = contract_for_goal(GOAL)
        state['form_plan']={'status':'ready','bindings':{'text':'#current-dom'}}
        state['rows']['row-1']={'result':'failed','retryable':False}
        hint = form_execution_hint(state)
        self.assertIn('run_form_test',hint)
        self.assertIn('#current-dom',hint)
        self.assertIn('E2E_BETA',hint)
        self.assertNotIn('E2E_ALPHA',hint)

    def test_unknown_widget_returns_to_model_without_restarting_inspection(self):
        state = contract_for_goal(GOAL)
        state['form_plan']={'status':'needs_binding','message':'unknown widget'}
        hint = form_execution_hint(state)
        self.assertIn('修复当前节点',hint)
        self.assertNotIn('inspect_form',hint)

    def test_unbound_values_keep_existing_execution_path(self):
        self.assertEqual(form_execution_hint(contract_for_goal(GOAL.replace('数量各1',''))),'')
