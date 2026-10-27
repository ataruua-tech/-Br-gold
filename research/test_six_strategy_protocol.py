import copy
import json
import sys
import unittest
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import freeze_six_strategy_study as study


class StudyProtocolTests(unittest.TestCase):
    def test_frozen_baseline_uses_live_engine_and_never_calls_old_holdout_unseen(self):
        with TemporaryDirectory() as d,patch.object(study,'OUT',Path(d)):
            passport,protocol=study.build(datetime(2026,10,27,11,39,tzinfo=timezone.utc))
            self.assertEqual(passport['financial_engine_sha256'],'ce4b80188eefa457179bb2b5129e6fbc762d701bd6deea6585467e2ac518f932')
            self.assertEqual(passport['signal_workflow_blob'],'38e147112ad48c3959b4dff6838fbec88d66f576')
            self.assertIn('ALREADY_INSPECTED',protocol['chronological_testing']['historical_status'])
            self.assertIsNone(protocol['data_status']['exact_historical_return'])
            self.assertFalse(protocol['data_status']['comparison_complete'])
            self.assertFalse(protocol['operational_safety']['alternatives_live_enabled'])

    def test_six_hypotheses_are_registered_before_results_and_missing_metrics_are_blank(self):
        with TemporaryDirectory() as d,patch.object(study,'OUT',Path(d)):
            _,p=study.build(datetime(2026,10,27,11,39,tzinfo=timezone.utc))
            self.assertEqual(len(p['candidates']),6)
            self.assertEqual(len({s['mechanism'] for s in p['candidates']}),6)
            records=[json.loads(s) for s in Path(d,'trial-ledger.jsonl').read_text().splitlines()]
            self.assertEqual(len(records),6)
            self.assertTrue(all(not s['results_inspected'] for s in records))
            self.assertEqual(p['search_accounting']['prior_trials_total'],'UNKNOWN')
            self.assertNotIn(',0,0,0,0',Path(d,'comparison-status.csv').read_text())

    def test_trial_ledger_detects_tampering_and_rejects_duplicate_or_final_test_selection(self):
        trial={'trial_id':'S0-v1','selection_on_final_test':False}
        with TemporaryDirectory() as d:
            path=Path(d,'trials.jsonl');study.add_trial(path,trial)
            with self.assertRaisesRegex(ValueError,'already'):study.add_trial(path,trial)
            with self.assertRaisesRegex(ValueError,'Final'):study.add_trial(path,{'trial_id':'S0-v2','selection_on_final_test':True})
            path.write_text(path.read_text().replace('S0-v1','S0-v9'))
            with self.assertRaisesRegex(ValueError,'integrity'):study.add_trial(path,{'trial_id':'S1-v1','selection_on_final_test':False})

    def test_missing_results_and_unregistered_trials_cannot_enter_ranking(self):
        valid={'status':'COMPLETED','common_data_verified':True,'execution_scope':'EXACT_OBSERVED_REPLAY',
            'trial_registered':True,'selection_on_final_test':False}
        self.assertTrue(study.validation_result_can_be_ranked(valid))
        for key,value in [('status','BLOCKED_DATA'),('common_data_verified',False),('trial_registered',False),
                          ('selection_on_final_test',True),('execution_scope','UNLABELLED_H1_PROXY')]:
            row={**valid,key:value}
            with self.subTest(key=key):self.assertFalse(study.validation_result_can_be_ranked(row))


if __name__=='__main__':unittest.main()
