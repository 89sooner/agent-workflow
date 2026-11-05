import tempfile,sqlite3
from pathlib import Path
from unittest.mock import patch
import unittest
from agent_workflow import native,session_metrics
from agent_workflow.core import WorkflowError
class MetricsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.home=Path(self.tmp.name)/'home';self.project=Path(self.tmp.name)/'project';self.project.mkdir()
  native.install(self.home,self.project)
 def event(self,at,event,provider='claude'):
  if provider=='codex' and not any(s.get('provider')=='codex' for s in native.sources(self.home)):native.install(self.home,self.project,provider='codex')
  with patch('agent_workflow.native.time.time',return_value=at):
   native.ingest(self.home,self.project,dict(session_id=provider,hook_event_name=event,cwd=str(self.project),tool_name='Bash'),provider)
 def test_durations_gap_and_outcome_denominator(self):
  self.event(1000,'UserPromptSubmit');self.event(1100,'PermissionRequest');self.event(1200,'PostToolUseFailure');self.event(1600,'SessionEnd')
  r=session_metrics.snapshot(self.home,now=2000)
  self.assertEqual(r['durations_seconds']['working'],100)
  self.assertEqual(r['durations_seconds']['human_waiting'],100)
  self.assertEqual(r['durations_seconds']['attention'],300)
  self.assertEqual(r['durations_seconds']['unknown'],100)
  self.assertEqual(r['known_tool_results'],1);self.assertEqual(r['tool_error_rate'],1)
  self.assertFalse((self.home/'state.sqlite3').exists())
 def test_unsupported_outcomes_clock_anomaly_and_empty(self):
  self.assertIsNone(session_metrics.snapshot(self.home,now=2000)['tool_error_rate'])
  self.event(1000,'PreToolUse','codex');self.event(1100,'PostToolUse','codex')
  r=session_metrics.snapshot(self.home,provider='codex',now=1200)
  self.assertEqual(r['unknown_tool_results'],1);self.assertIsNone(r['observed_tool_errors'])
  self.event(900,'SessionEnd','codex')
  self.assertEqual(session_metrics.snapshot(self.home,now=1200)['clock_anomaly_sessions'],1)
  with self.assertRaises(WorkflowError):session_metrics.snapshot(self.home,days='bad')

 def test_clock_reversal_excludes_duration_but_preserves_valid_outcomes(self):
  self.event(1000,'UserPromptSubmit');self.event(1100,'PostToolUseFailure');self.event(1050,'SessionEnd')
  r=session_metrics.snapshot(self.home,now=1200)
  self.assertEqual(r['clock_anomaly_sessions'],1)
  self.assertEqual(sum(r['durations_seconds'].values()),0)
  self.assertEqual(r['known_tool_results'],1);self.assertEqual(r['observed_tool_errors'],1)
  self.assertEqual(r['tool_stats']['Bash'],dict(results=1,errors=1))

 def test_session_retention_limit_warns_even_in_other_provider_filter(self):
  for i in range(native.MAX_SESSIONS+1):
   native.record(self.home,'claude',str(self.project),f'retention-{i}',str(self.project),'fixture','ended','SessionEnd')
  r=session_metrics.snapshot(self.home,provider='codex')
  self.assertEqual(r['session_count'],0)
  self.assertEqual(r['retention']['retained_sessions'],500)
  self.assertEqual(r['retention']['retained_events'],500)
  self.assertTrue(r['retention']['session_limit_reached'])
  self.assertFalse(r['retention']['event_limit_reached'])
  self.assertTrue(r['retention_limit_reached'])
