import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, RequestFactory, override_settings

from core.quantity_state import QuantityStateStore
from core import quantity_views as views
from core.quantity_calc import _resolve_count, calc_foundations


class QuantityStateStoreTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.sqlite3'
        self.store = QuantityStateStore(self.path)

    def test_another_instance_reads_saved_review(self):
        self.store.put('review', 'r', {'confirmed': True}, ttl=60)
        self.assertEqual(QuantityStateStore(self.path).get('review', 'r'), {'confirmed': True})

    def test_separate_process_reads_saved_result(self):
        self.store.put('result', 'j', {'amount': 3}, ttl=60)
        code = 'import json,sys; from core.quantity_state import QuantityStateStore; print(json.dumps(QuantityStateStore(sys.argv[1]).get("result","j")))'
        result = subprocess.check_output([sys.executable, '-c', code, str(self.path)], text=True)
        self.assertEqual(json.loads(result), {'amount': 3})

    def test_expired_record_is_not_returned(self):
        self.store.put('result', 'j', {'x': 1}, ttl=-1)
        self.assertIsNone(self.store.get('result', 'j'))

    def test_pop_removes_saved_extraction(self):
        self.store.put('extraction', 'j', {'members': {}}, ttl=60)
        self.assertEqual(self.store.pop('extraction', 'j'), {'members': {}})
        self.assertIsNone(self.store.get('extraction', 'j'))

    def test_update_preserves_existing_fields(self):
        self.store.put('review', 'r', {'a': 1}, ttl=60)
        self.assertTrue(self.store.update('review', 'r', {'b': 2}, ttl=60))
        self.assertEqual(self.store.get('review', 'r'), {'a': 1, 'b': 2})
        self.assertFalse(self.store.update('review', 'missing', {'b': 2}, ttl=60))

    def test_job_start_is_atomic_and_bound_to_owner_and_input(self):
        self.assertEqual(self.store.begin_job('j', '1', 'overview', 'hash'), 'new')
        self.assertEqual(QuantityStateStore(self.path).begin_job('j', '1', 'overview', 'hash'), 'duplicate')
        self.assertEqual(self.store.begin_job('j', '2', 'overview', 'hash'), 'forbidden')
        self.assertEqual(self.store.begin_job('j', '1', 'overview', 'changed'), 'conflict')

    def test_invalid_kickoff_can_release_job_for_retry(self):
        self.store.begin_job('j', '1', 'overview', 'hash')
        self.store.release_job('j')
        self.assertEqual(self.store.begin_job('j', '1', 'overview', 'hash'), 'new')

    def test_nonfinite_payload_is_rejected_without_overwriting(self):
        self.store.put('result', 'j', {'amount': 2}, ttl=60)
        with self.assertRaises(ValueError):
            self.store.put('result', 'j', {'amount': float('nan')}, ttl=60)
        self.assertEqual(self.store.get('result', 'j'), {'amount': 2})

    def test_confirmation_commit_is_atomic_when_final_result_write_fails(self):
        self.store.put('extraction', 'j', {'members': {}}, ttl=60)
        self.store.put('result', 'j', {'results': {'structural': {'review_required': True}}}, ttl=60)
        self.store.claim_confirmation('j', 'hash')
        original_write = self.store.write
        def fail_result(connection, namespace, key, value, ttl):
            if namespace == 'result':
                raise RuntimeError('simulated write failure')
            original_write(connection, namespace, key, value, ttl)
        with patch.object(self.store, 'write', side_effect=fail_result), self.assertRaises(RuntimeError):
            self.store.finish_confirmation('j', 'hash', {'ok': True, 'results': {'structural': {'items': []}}})
        self.assertIsNone(self.store.get('confirmation', 'j'))
        self.assertIsNotNone(self.store.get('extraction', 'j'))
        self.assertTrue(self.store.get('result', 'j')['results']['structural']['review_required'])

    def test_confirmation_commit_preserves_other_results_and_removes_extraction(self):
        self.store.put('result', 'j', {'results': {'architectural': {'items': [1]}}}, ttl=60)
        self.store.put('extraction', 'j', {'members': {}}, ttl=60)
        self.store.claim_confirmation('j', 'hash')
        self.store.finish_confirmation('j', 'hash', {'ok': True, 'results': {'structural': {'items': [2]}}})
        self.assertEqual(self.store.get('result', 'j')['results'], {'architectural': {'items': [1]}, 'structural': {'items': [2]}})
        self.assertIsNone(self.store.get('extraction', 'j'))
        self.assertIsNone(self.store.get('confirmation_lock', 'j'))


class QuantityApiStateTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.override = override_settings(QUANTITY_STATE_DB=str(Path(self.tmp.name) / 'state.sqlite3'))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.factory = RequestFactory()
        self.user = SimpleNamespace(pk=7, is_staff=True, is_superuser=False, is_authenticated=True)
        self.store = QuantityStateStore(Path(self.tmp.name) / 'state.sqlite3')

    def request(self, method, path, data=None, user=None):
        req = getattr(self.factory, method)(path, data or {})
        req.user = user or self.user
        return req

    def bind(self, job='job'):
        self.store.begin_job(job, str(self.user.pk), 'api_run_quantity', 'input')

    def test_progress_get_matches_home_polling(self):
        self.bind()
        views._result_set('job', {'ok': True, 'results': {'value': 3}})
        response = views.api_quantity_progress(self.request('get', '/api/quantity/progress/', {'job_id': 'job'}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['results'], {'value': 3})

    def test_progress_post_remains_supported(self):
        self.bind()
        views._progress_set('job', 'structural', 2, 5, 'batch')
        response = views.api_quantity_progress(self.request('post', '/api/quantity/progress/', {'job_id': 'job'}))
        self.assertEqual(json.loads(response.content)['current'], 2)

    def test_other_staff_cannot_read_or_cancel_job(self):
        self.bind()
        other = SimpleNamespace(pk=8, is_staff=True, is_superuser=False, is_authenticated=True)
        for name, method in [('api_quantity_progress', 'get'), ('api_quantity_cancel', 'post'), ('api_quantity_confirm_review', 'post')]:
            with self.subTest(name=name):
                response = getattr(views, name)(self.request(method, '/', {'job_id': 'job'}, other))
                self.assertEqual(response.status_code, 403)

    def test_unknown_job_is_not_bound_by_a_reader(self):
        response = views.api_quantity_progress(self.request('get', '/', {'job_id': 'unknown'}))
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(self.store.get('job', 'unknown'))

    def test_result_and_review_survive_memory_reset(self):
        views._review_ensure('review', self.user.pk, {'pdf': 'sha'})
        views._review_update('review', overview={'title': 'site'}, overview_confirmed=True)
        views._result_set('job', {'ok': True})
        for name in ['_REVIEW_STORE', '_RESULT_STORE']:
            value = getattr(views, name, None)
            if value is not None:
                value.clear()
        self.assertTrue(views._review_get('review')['overview_confirmed'])
        self.assertTrue(views._result_get('job')['ok'])

    def test_cancel_is_shared_across_store_instances(self):
        views._cancel_request('job')
        self.assertTrue(QuantityStateStore(self.store.path).get('cancel', 'job'))
        views._cancel_clear('job')
        self.assertFalse(views._is_cancelled('job'))

    def test_confirm_requires_post(self):
        self.bind()
        response = views.api_quantity_confirm_review(self.request('get', '/', {'job_id': 'job'}))
        self.assertEqual(response.status_code, 405)

    def test_malformed_corrections_do_not_silently_confirm(self):
        self.bind()
        views._extraction_store_set('job', {'foundations': []}, None)
        for value in ['{bad', '{}', 'null']:
            with self.subTest(value=value):
                response = views.api_quantity_confirm_review(self.request('post', '/', {'job_id': 'job', 'corrections': value}))
                self.assertEqual(response.status_code, 400)
                self.assertIsNotNone(views._extraction_store_get('job'))

    def test_confirmation_retry_returns_same_result_without_recalculation(self):
        self.bind()
        views._extraction_store_set('job', {'foundations': []}, None)
        with patch.object(views, 'compute_structural_quantities', return_value={'items': [], 'totals': {'concrete_m3': 0}}) as calc, patch.object(views, 'compute_massing_model', return_value={}):
            first = views.api_quantity_confirm_review(self.request('post', '/', {'job_id': 'job', 'corrections': '[]'}))
            second = views.api_quantity_confirm_review(self.request('post', '/', {'job_id': 'job', 'corrections': '[]'}))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(json.loads(first.content), json.loads(second.content))
        self.assertEqual(calc.call_count, 1)

    def test_confirmation_with_changed_corrections_does_not_return_old_result(self):
        self.bind()
        views._extraction_store_set('job', {'foundations': []}, None)
        with patch.object(views, 'compute_structural_quantities', return_value={'items': []}), patch.object(views, 'compute_massing_model', return_value={}):
            views.api_quantity_confirm_review(self.request('post', '/', {'job_id': 'job', 'corrections': '[]'}))
            response = views.api_quantity_confirm_review(self.request('post', '/', {'job_id': 'job', 'corrections': '[{"action":"remove","review_id":"foundations:0"}]'}))
        self.assertEqual(response.status_code, 409)

    def test_dead_worker_is_reported_as_interrupted(self):
        self.bind()
        self.store.update('job', 'job', {'pid': 2147483647}, ttl=60)
        views._progress_set('job', 'queued', 0, 1)
        response = views.api_quantity_progress(self.request('get', '/', {'job_id': 'job'}))
        payload = json.loads(response.content)
        self.assertTrue(payload['done'])
        self.assertTrue(payload['interrupted'])

    def test_history_only_lists_current_users_main_analysis_jobs(self):
        self.bind('mine')
        self.store.begin_job('other', '8', 'api_run_quantity', 'input')
        self.store.begin_job('overview', '7', 'api_quantity_overview_check', 'input')
        response = views.api_quantity_jobs(self.request('get', '/api/quantity/jobs/'))
        payload = json.loads(response.content)
        self.assertEqual([job['job_id'] for job in payload['jobs']], ['mine'])
        self.assertNotIn('fingerprint', payload['jobs'][0])

    def test_identical_kickoff_starts_one_background_worker(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        with patch.object(views, '_merge_uploaded_pdfs', return_value=(b'pdf', {})), patch.object(views, '_merge_uploaded_cad_sets', return_value=(None, None, {})), patch.object(views.threading, 'Thread') as thread:
            responses = []
            for _ in range(2):
                req = self.request('post', '/', {'job_id': 'overview-job', 'structural_pdf': SimpleUploadedFile('S.pdf', b'pdf')})
                responses.append(views.api_quantity_overview_check(req))
        self.assertEqual(responses[0].status_code, 200)
        self.assertEqual(responses[1].status_code, 200)
        self.assertEqual(json.loads(responses[0].content), json.loads(responses[1].content))
        self.assertEqual(thread.return_value.start.call_count, 1)

    def test_nonfinite_correction_is_rejected_and_keeps_extraction(self):
        self.bind()
        views._extraction_store_set('job', {'foundations': []}, None)
        response = views.api_quantity_confirm_review(self.request('post', '/', {'job_id': 'job', 'corrections': '[{"fields":{"count":NaN}}]'}))
        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(views._extraction_store_get('job'))

    def test_saved_work_controls_and_assets_are_rendered(self):
        from django.template.loader import render_to_string
        request = self.request('get', '/')
        rendered = render_to_string('core/home.html', {'request': request, 'user': self.user})
        self.assertIn('id="qty-resume"', rendered)
        self.assertIn('src="/static/core/js/quantity_resume.js?v=', rendered)
        self.assertIn('href="/static/core/css/quantity_resume.css"', rendered)

    def test_standalone_entry_opens_the_current_quantity_workflow(self):
        self.user.username = "admin"
        response = views.quantity_start(self.request('get', '/quantity/start/'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/?quantity=1')

    def test_analysis_warning_is_preserved_in_export(self):
        import io
        import openpyxl
        payload = {'results': {'structural': {'items': [], 'extraction_incomplete': True,
                   'input_coverage': [{'file': 'skipped.dwg', 'status': 'incomplete', 'reason': '未処理'}]}}}
        request = self.factory.post('/', json.dumps(payload), content_type='application/json')
        request.user = self.user
        response = views.api_download_excel(request)
        workbook = openpyxl.load_workbook(io.BytesIO(response.content))
        rows = list(workbook['분석 범위'].values)
        self.assertEqual(rows[1][1], '부분 결과 / 확인 필요')
        self.assertEqual(rows[2][2], 'skipped.dwg')

    def test_invalid_correction_objects_keep_extraction_and_do_not_finalize(self):
        self.bind()
        members = {'foundations': [{'mark': 'F1', 'length_m': 2, 'width_m': 3, 'thickness_m': .5, 'count': 1}]}
        views._extraction_store_set('job', members, None)
        cases = [[{}], [{'action': 'edit', 'review_id': 'foundations:99', 'fields': {'count': 2}}],
                 [{'action': 'unknown', 'review_id': 'foundations:0'}],
                 [{'action': 'edit', 'review_id': 'foundations:0', 'fields': {'count': -1}}],
                 [{'action': 'edit', 'review_id': 'foundations:0', 'fields': {'unknown': 1}}]]
        for corrections in cases:
            with self.subTest(corrections=corrections):
                response = views.api_quantity_confirm_review(self.request('post', '/', {'job_id': 'job', 'corrections': json.dumps(corrections)}))
                self.assertEqual(response.status_code, 400)
                self.assertIsNotNone(views._extraction_store_get('job'))
                self.assertIsNone(self.store.get('confirmation', 'job'))

    def test_structural_error_and_partial_results_have_distinct_history_statuses(self):
        for job_id, structural, expected in [('failed', {'error': 'failed parse'}, 'failed'),
                ('partial', {'items': [1], 'extraction_incomplete': True}, 'partial')]:
            self.bind(job_id)
            views._result_set(job_id, {'ok': True, 'results': {'structural': structural}})
        response = views.api_quantity_jobs(self.request('get', '/'))
        statuses = {job['job_id']: job['status'] for job in json.loads(response.content)['jobs']}
        self.assertEqual(statuses, {'failed': 'failed', 'partial': 'partial'})

    def test_confirmation_retry_race_uses_success_receipt_after_claim(self):
        self.bind()
        views._extraction_store_set('job', {'foundations': []}, None)
        receipt = {'ok': True, 'results': {'structural': {'items': [{'quantity': 3}]}}}
        original_claim = QuantityStateStore.claim_confirmation
        def finish_other_request_before_claim(state, key, fingerprint):
            original_claim(state, key, fingerprint)
            state.finish_confirmation(key, fingerprint, receipt)
            return original_claim(state, key, fingerprint)
        with patch.object(QuantityStateStore, 'claim_confirmation', finish_other_request_before_claim), patch.object(views, 'compute_structural_quantities') as calculate:
            response = views.api_quantity_confirm_review(self.request('post', '/', {'job_id': 'job', 'corrections': '[]'}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), receipt)
        calculate.assert_not_called()
        self.assertIsNone(self.store.get('confirmation_lock', 'job'))

    def test_invalid_action_and_rebar_value_types_release_confirmation_lock(self):
        self.bind()
        views._extraction_store_set('job', {'walls': [{'mark': 'W1', 'length_m': 2, 'height_m': 3, 'thickness_m': .2, 'count': 1}]}, None)
        for action, fields in [([], {}), ('edit', {'rebar_size': []}), ('edit', {'rebar_layers': [{'role': [], 'size': 'D13'}]})]:
            with self.subTest(action=action, fields=fields):
                corrections = [{'review_id': 'walls:0', 'action': action, 'fields': fields}]
                response = views.api_quantity_confirm_review(self.request('post', '/', {'job_id': 'job', 'corrections': json.dumps(corrections)}))
                self.assertEqual(response.status_code, 400)
                self.assertIsNotNone(views._extraction_store_get('job'))
                self.assertIsNone(self.store.get('confirmation_lock', 'job'))


class QuantityNumericGuardTests(SimpleTestCase):
    def test_unknown_nonfinite_count_is_excluded_without_exception(self):
        for value in [float('nan'), float('inf'), -float('inf')]:
            with self.subTest(value=value):
                warnings = []
                self.assertIsNone(_resolve_count({'count': value}, '기초', 'F1', warnings))
                self.assertTrue(warnings)

    def test_boolean_is_not_accepted_as_numeric_dimension_or_count(self):
        for field in ['count', 'length_m', 'spacing_m']:
            self.assertFalse(views._validate_correction_field(field, True)[0])

    def test_boolean_dimension_does_not_create_concrete_quantity(self):
        volume, area, _, warnings = calc_foundations([{'mark': 'F1', 'length_m': True, 'width_m': 2, 'thickness_m': .5, 'count': 1}], {})
        self.assertEqual(volume, 0)
        self.assertEqual(area, 0)
        self.assertTrue(warnings)


class QuantityCoverageTests(SimpleTestCase):
    def test_provider_failure_marks_extraction_as_incomplete(self):
        self.assertTrue(views._extraction_incomplete_reasons({'notes': ['[배치 2/3] Gemini 부재 추출 요청 중 오류가 발생했습니다: timeout']}))

    def test_dxf_unit_metadata_and_raw_measurement_tokens_are_preserved(self):
        import ezdxf
        doc = ezdxf.new()
        doc.header['$INSUNITS'] = 4
        doc.modelspace().add_text('400×600 / D13@200')
        data = views._extract_dxf_quantities(doc)
        self.assertEqual(data['drawing_unit'], 'mm')
        self.assertEqual(data['meters_per_unit'], .001)
        self.assertTrue(data['text_measurement_tokens'])
        self.assertTrue(all(token['raw_substring'] for token in data['text_measurement_tokens']))

    def test_cad_file_cap_reports_each_unparsed_file(self):
        import io
        import zipfile
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            for i in range(3):
                archive.writestr(str(i) + '.dxf', b'placeholder')
        with patch.object(views, 'DWG_ZIP_PARSE_MAX_FILES', 1), patch.object(views.ezdxf, 'read', return_value=object()), patch.object(views, '_extract_dxf_quantities', return_value={'layers': []}):
            result = views.parse_dwg_from_zip(data.getvalue())
        self.assertEqual(len(result), 3)
        self.assertTrue(result['1.dxf']['skipped'])
        self.assertTrue(result['2.dxf']['skipped'])
