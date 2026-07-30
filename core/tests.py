import io
import json
import ast
import os
import struct
import unicodedata
import zipfile
import zlib
import tempfile
import threading
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase
from PIL import Image

from .quantity_views import (
    _canonical_review_id,
    api_check_zip,
    _build_cad_precheck,
    _collect_request_cad_uploads,
    _decode_zip_member_name,
    _cache_validated_overview_pages,
    _classify_overview_page_batch,
    _classification_has_evidence,
    _fill_overview_spec_defaults,
    _find_incremental_overview_pages,
    _general_notes_page_candidates,
    _merge_general_notes_page_candidates,
    _empty_member_rebar_check_state,
    _cbl_v5_parse_source,
    _cbl_v5_select_material_page,
    extract_general_notes,
    _validate_general_notes_result,
    _run_general_notes_job,
    _merge_uploaded_cad_sets,
    _OverviewClassificationResult,
    _OVERVIEW_CLASSIFICATION_CACHE,
    _OVERVIEW_CLASSIFICATION_LOCK,
    OverviewLocatorTimeout,
    _parse_explicit_floor_count,
    _review_file_hashes,
    _review_ensure,
    _review_update,
    api_quantity_general_notes_check,
    _coordination_target_pages,
    _coordination_cross_check,
    _cbl_v5_parse_source,
)
from .cbl_category_policy import (
    CBL_PUBLIC_CATEGORY_CHOICES,
    cbl_resolve_auto_post_category,
)
from .forms import PostForm
from . import views as core_views
from .management.commands.run_ai_auto_writer import (
    AUTO_NAVER_CATEGORY_ORDER,
    save_ai_data_to_post,
)
from .models import CalendarEvent, Post


class CblCadDwgDxfReadthroughCacheTests(SimpleTestCase):
    DXF = "0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n" + (" " * 600)

    def setUp(self):
        self.source_dir = tempfile.TemporaryDirectory(prefix="cblcad-cache-source-")
        self.work_dir = tempfile.TemporaryDirectory(prefix="cblcad-cache-work-")
        self.cache_dir = tempfile.TemporaryDirectory(prefix="cblcad-cache-")
        self.source = Path(self.source_dir.name) / "same-name.dwg"
        self.source.write_bytes(b"same DWG bytes for cache regression")

    def tearDown(self):
        self.source_dir.cleanup()
        self.work_dir.cleanup()
        self.cache_dir.cleanup()

    def _call(self, destination, producer, **kwargs):
        return core_views._cbl_dwg_dxf_readthrough_v1(
            self.source,
            destination,
            version=kwargs.get("version", "ACAD2004"),
            output_type=kwargs.get("output_type", "DXF"),
            options=kwargs.get("options", ("0", "1")),
            endpoint=kwargs.get("endpoint", "test"),
            producer=producer,
        )

    def test_same_bytes_across_endpoints_call_oda_once(self):
        calls = []

        def produce():
            calls.append("oda")
            output = Path(self.work_dir.name) / f"out-{len(calls)}.dxf"
            output.write_text(self.DXF, encoding="utf-8")
            return {"path": str(output), "returncode": 0}

        with patch.object(core_views, "_cbl_dwg_dxf_cache_dir_v1", return_value=Path(self.cache_dir.name)):
            first = self._call(Path(self.work_dir.name) / "v29.dxf", produce, endpoint="v29/open-session")
            second = self._call(Path(self.work_dir.name) / "display.dxf", produce, endpoint="dwg-to-dxf")

        self.assertEqual(calls, ["oda"])
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertTrue((Path(self.work_dir.name) / "v29.dxf").is_file())
        self.assertTrue((Path(self.work_dir.name) / "display.dxf").is_file())

    def test_content_version_and_options_change_cache_key(self):
        calls = []

        def produce():
            calls.append(1)
            output = Path(self.work_dir.name) / f"variant-{len(calls)}.dxf"
            output.write_text(self.DXF, encoding="utf-8")
            return {"path": str(output), "returncode": 0}

        with patch.object(core_views, "_cbl_dwg_dxf_cache_dir_v1", return_value=Path(self.cache_dir.name)):
            self._call(Path(self.work_dir.name) / "a.dxf", produce)
            self._call(Path(self.work_dir.name) / "b.dxf", produce, version="ACAD2013")
            self._call(Path(self.work_dir.name) / "c.dxf", produce, options=("1", "1"))
            self.source.write_bytes(b"different DWG bytes")
            self._call(Path(self.work_dir.name) / "d.dxf", produce)

        self.assertEqual(len(calls), 4)

    def test_corrupt_cache_is_rebuilt_and_failure_is_not_saved(self):
        calls = []

        def produce():
            calls.append(1)
            output = Path(self.work_dir.name) / f"corrupt-{len(calls)}.dxf"
            output.write_text(self.DXF if len(calls) > 1 else "bad", encoding="utf-8")
            return {"path": str(output), "returncode": 0}

        with patch.object(core_views, "_cbl_dwg_dxf_cache_dir_v1", return_value=Path(self.cache_dir.name)):
            with self.assertRaises(RuntimeError):
                self._call(Path(self.work_dir.name) / "failed.dxf", produce)
            self.assertEqual(list(Path(self.cache_dir.name).glob("*.dxf")), [])
            self._call(Path(self.work_dir.name) / "good.dxf", produce)
            key, _sha, _raw = core_views._cbl_dwg_dxf_cache_key_v1(self.source, "DXF", "ACAD2004", ("0", "1"))
            (Path(self.cache_dir.name) / f"{key}.dxf").write_text("corrupt", encoding="utf-8")
            self._call(Path(self.work_dir.name) / "rebuilt.dxf", produce)

        self.assertEqual(len(calls), 3)

    def test_concurrent_same_key_runs_oda_once(self):
        calls = []
        barrier = threading.Barrier(2)

        def produce():
            calls.append(1)
            time.sleep(0.05)
            output = Path(self.work_dir.name) / "concurrent.dxf"
            output.write_text(self.DXF, encoding="utf-8")
            return {"path": str(output), "returncode": 0}

        def run(index):
            barrier.wait()
            return self._call(Path(self.work_dir.name) / f"thread-{index}.dxf", produce)

        with patch.object(core_views, "_cbl_dwg_dxf_cache_dir_v1", return_value=Path(self.cache_dir.name)):
            results = [None, None]
            threads = [threading.Thread(target=lambda i=i: results.__setitem__(i, run(i))) for i in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(len(calls), 1)
        self.assertEqual(sum(bool(result["cache_wait"]) for result in results), 1)

    def test_v29_cache_hit_still_creates_independent_session_copy(self):
        calls = []

        def fake_oda(cmd, **kwargs):
            calls.append(cmd)
            output_dir = Path(cmd[2])
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "same-name.dxf").write_text(self.DXF, encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        first_dir = Path(self.work_dir.name) / "session-one"
        second_dir = Path(self.work_dir.name) / "session-two"
        with patch.object(core_views, "_cbl_dwg_dxf_cache_dir_v1", return_value=Path(self.cache_dir.name)), \
             patch.object(core_views, "_cbl_v29_find_oda", return_value="/mock/ODAFileConverter"), \
             patch("subprocess.run", side_effect=fake_oda):
            first = core_views._cbl_v29_oda_convert(self.source, first_dir, output_type="DXF")
            second = core_views._cbl_v29_oda_convert(self.source, second_dir, output_type="DXF")

        self.assertEqual(len(calls), 1)
        self.assertNotEqual(first["output"], second["output"])
        self.assertEqual(Path(first["output"]).read_text(encoding="utf-8"), self.DXF)
        self.assertEqual(Path(second["output"]).read_text(encoding="utf-8"), self.DXF)

    def test_validation_reads_only_small_head_and_tail_for_50mb_dxf(self):
        path = Path(self.work_dir.name) / "large.dxf"
        with path.open("wb") as stream:
            stream.write(b"0\nSECTION\n2\nHEADER\n")
            stream.write(b"X" * (50 * 1024 * 1024))
            stream.write(b"\n0\nEOF\n")

        with patch.object(Path, "read_bytes", side_effect=AssertionError("full read forbidden")):
            self.assertTrue(core_views._cbl_dwg_dxf_cache_valid_v1(path, path.stat().st_size))

    def test_cleanup_removes_stale_temp_but_preserves_active_cache_lock(self):
        import fcntl

        cache = Path(self.cache_dir.name)
        old_time = time.time() - core_views._CBL_DWG_DXF_CACHE_TTL_SECONDS_V1 - 10
        stale_temp = cache / ".stale.tmp"
        stale_temp.write_text("partial", encoding="utf-8")
        os.utime(stale_temp, (old_time, old_time))

        active_dxf = cache / ("a" * 64 + ".dxf")
        active_dxf.write_text(self.DXF, encoding="utf-8")
        active_lock = active_dxf.with_suffix(".lock")
        with active_lock.open("a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            os.utime(active_dxf, (old_time, old_time))
            core_views._cbl_dwg_dxf_cache_cleanup_v1(cache, now=time.time(), protected_lock=cache / "other.lock")
            self.assertTrue(active_dxf.exists())
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

        self.assertFalse(stale_temp.exists())

    def test_non_dxf_output_bypasses_readthrough_cache(self):
        calls = []

        def produce():
            calls.append(1)
            output = Path(self.work_dir.name) / "output.dwg"
            output.write_bytes(b"D" * 800)
            return {"path": str(output), "returncode": 0}

        with patch.object(core_views, "_cbl_dwg_dxf_cache_dir_v1", return_value=Path(self.cache_dir.name)):
            result = self._call(Path(self.work_dir.name) / "copy.dwg", produce, output_type="DWG")

        self.assertEqual(calls, [1])
        self.assertEqual(result["invalidation_reason"], "output_type_not_dxf")
        self.assertFalse(list(Path(self.cache_dir.name).glob("*.dxf")))

    def test_cache_miss_hit_and_wait_logs_are_captured_on_stdout(self):
        def produce():
            output = Path(self.work_dir.name) / "log.dxf"
            output.write_text(self.DXF, encoding="utf-8")
            return {"path": str(output), "returncode": 0}

        with patch("builtins.print") as print_mock:
            with patch.object(core_views, "_cbl_dwg_dxf_cache_dir_v1", return_value=Path(self.cache_dir.name)):
                self._call(Path(self.work_dir.name) / "miss.dxf", produce)
                self._call(Path(self.work_dir.name) / "hit.dxf", produce)

        output = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertIn("CBLCAD_DWG_DXF_CACHE", output)
        self.assertIn("event=cache_miss", output)
        self.assertIn("event=cache_hit", output)

        wait_cache = tempfile.TemporaryDirectory(prefix="cblcad-log-wait-cache-")
        barrier = threading.Barrier(2)

        def slow_produce():
            time.sleep(0.05)
            output = Path(self.work_dir.name) / "wait-log.dxf"
            output.write_text(self.DXF, encoding="utf-8")
            return {"path": str(output), "returncode": 0}

        def run_wait(index):
            barrier.wait()
            return self._call(Path(self.work_dir.name) / f"wait-{index}.dxf", slow_produce)

        with patch("builtins.print") as wait_print:
            with patch.object(core_views, "_cbl_dwg_dxf_cache_dir_v1", return_value=Path(wait_cache.name)):
                threads = [threading.Thread(target=run_wait, args=(index,)) for index in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
        wait_output = "\n".join(str(call.args[0]) for call in wait_print.call_args_list if call.args)
        self.assertIn("event=cache_wait_hit", wait_output)
        wait_cache.cleanup()

    def test_real_display_and_v29_functions_share_cache_in_both_orders(self):
        for first in ("display", "v29"):
            cache = tempfile.TemporaryDirectory(prefix="cblcad-order-cache-")
            display_dir = Path(self.work_dir.name) / f"display-{first}"
            v29_dir = Path(self.work_dir.name) / f"v29-{first}"
            calls = []

            def fake_oda(cmd, **kwargs):
                calls.append(cmd)
                output_dir = Path(cmd[2])
                output_dir.mkdir(parents=True, exist_ok=True)
                source_names = list(Path(cmd[1]).glob("*.dwg"))
                name = source_names[0].stem if source_names else "same-name"
                (output_dir / f"{name}.dxf").write_text(self.DXF, encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch.object(core_views, "_cbl_dwg_dxf_cache_dir_v1", return_value=Path(cache.name)), \
                 patch.object(core_views, "_cbl_v29_find_oda", return_value="/mock/ODAFileConverter"), \
                 patch("subprocess.run", side_effect=fake_oda):
                if first == "display":
                    display_result = core_views._cbl_run_oda_to_dxf_version_v1(
                        "/mock/ODAFileConverter", self.source, "ACAD2004", display_dir, "XR-FORM-A(A3)"
                    )
                    v29_result = core_views._cbl_v29_oda_convert(self.source, v29_dir, output_type="DXF")
                    first_hit = display_result[0].get("cache_hit")
                    second_hit = v29_result.get("cache_hit")
                else:
                    v29_result = core_views._cbl_v29_oda_convert(self.source, v29_dir, output_type="DXF")
                    display_result = core_views._cbl_run_oda_to_dxf_version_v1(
                        "/mock/ODAFileConverter", self.source, "ACAD2004", display_dir, "XR-FORM-A(A3)"
                    )
                    first_hit = v29_result.get("cache_hit")
                    second_hit = display_result[0].get("cache_hit")

            cache.cleanup()
            self.assertEqual(len(calls), 1)
            self.assertFalse(first_hit)
            self.assertTrue(second_hit)
            self.assertTrue(Path(v29_result["output"]).exists())
            self.assertTrue(Path(display_result[2]).exists())

    def test_endpoint_response_shape_stays_compatible_on_hit_and_miss(self):
        factory = RequestFactory()
        base_endpoint = core_views._CBL_V21_3_ORIGINAL_BEST_DWG_TO_DXF_API
        calls = []

        def fake_oda(cmd, **kwargs):
            calls.append(cmd)
            output_dir = Path(cmd[2])
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "same-name.dxf").write_text(self.DXF, encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def post_display():
            upload = SimpleUploadedFile("same-name.dwg", self.source.read_bytes(), content_type="application/acad")
            return base_endpoint(factory.post("/api/cblcad/dwg-to-dxf/", {"file": upload}))

        session_root = Path(self.work_dir.name) / "sessions"
        with patch.object(core_views, "_cbl_dwg_dxf_cache_dir_v1", return_value=Path(self.cache_dir.name)), \
             patch.object(core_views, "_cbl_v29_root", return_value=session_root), \
             patch.object(core_views, "_cbl_v29_find_oda", return_value="/mock/ODAFileConverter"), \
             patch("subprocess.run", side_effect=fake_oda):
            display_miss = post_display()
            display_hit = post_display()

            def post_v29():
                upload = SimpleUploadedFile("same-name.dwg", self.source.read_bytes(), content_type="application/acad")
                return core_views.cblcad_v29_open_session(
                    factory.post("/api/cblcad/v29/open-session/", {"file": upload})
                )

            v29_miss = post_v29()
            v29_hit = post_v29()

        self.assertEqual(display_miss.status_code, display_hit.status_code)
        self.assertEqual(display_miss["Content-Type"], display_hit["Content-Type"])
        display_miss_json = json.loads(display_miss.content)
        display_hit_json = json.loads(display_hit.content)
        self.assertEqual(set(display_miss_json.keys()), set(display_hit_json.keys()))
        self.assertIn("dxf", display_miss_json)
        self.assertEqual(v29_miss.status_code, v29_hit.status_code)
        self.assertEqual(v29_miss["Content-Type"], v29_hit["Content-Type"])
        v29_miss_json = json.loads(v29_miss.content)
        v29_hit_json = json.loads(v29_hit.content)
        self.assertEqual(set(v29_miss_json.keys()), set(v29_hit_json.keys()))
        self.assertEqual(
            set(json.loads((session_root / v29_miss_json["session_id"] / "meta.json").read_text()).keys()),
            {"session_id", "original_name", "original_bytes", "base_dxf_bytes", "base_dxf"},
        )
        self.assertEqual(len(calls), 1)


class DrawingCoordinationRegressionTests(SimpleTestCase):
    def test_target_pages_keep_each_drawing_type_balanced(self):
        rows = []
        for kind, start in (("structural_plan", 10), ("elevation", 100), ("ramp", 200)):
            rows.extend({"pdf_page": start + index, "drawing_type": kind}
                        for index in range(20))
        selected = _coordination_target_pages(rows)
        self.assertIn(10, selected)
        self.assertIn(29, selected)
        self.assertIn(100, selected)
        self.assertIn(119, selected)
        self.assertIn(200, selected)
        self.assertIn(219, selected)

    def test_cross_check_reports_level_conflict_without_overwriting(self):
        structural = [{
            "discipline": "구조", "pdf_page": 20, "drawing_number": "S-111",
            "building_scope": "101동", "floor_scope": "1층",
            "levels": [{"label": "1FL", "elevation_m": 0.0, "quote": "1FL ±0"}],
        }]
        architectural = [{
            "discipline": "건축", "pdf_page": 80, "drawing_number": "A-401",
            "building_scope": "101동", "floor_scope": "1층",
            "levels": [{"label": "1FL", "elevation_m": 0.15, "quote": "1FL +150"}],
        }]
        conflicts, unconfirmed = _coordination_cross_check(structural, architectural)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(len(conflicts[0]["evidence"]), 2)
        self.assertEqual(unconfirmed, [])

    def test_stage_does_not_generate_quantities_or_geometry(self):
        template = Path(__file__).with_name("templates").joinpath("core", "home.html").read_text()
        self.assertIn("동·층·층고·코어·개구부 확인", template)
        self.assertIn("/api/quantity/drawing-coordination-check/", template)


class GeneralNotesGeneralizationTests(SimpleTestCase):
    def test_extract_general_notes_has_single_definition(self):
        source = Path(__file__).with_name("quantity_views.py").read_text()
        tree = ast.parse(source)
        self.assertEqual(
            sum(node.name == "extract_general_notes"
                for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)),
            1,
        )

    def test_quantity_entrypoints_have_single_definitions(self):
        source = Path(__file__).with_name("quantity_views.py").read_text()
        tree = ast.parse(source)
        names = (
            "extract_general_notes", "_run_general_notes_job",
            "_run_overview_check_job", "api_quantity_overview_check",
            "api_quantity_general_notes_check",
            "api_quantity_drawing_coordination_check",
        )
        for name in names:
            self.assertEqual(
                sum(isinstance(node, ast.FunctionDef) and node.name == name
                    for node in ast.walk(tree)),
                1, name,
            )

    def test_material_parser_does_not_require_drawing_or_toc_numbers(self):
        source = """구조재료 및 강도
콘크리트 적용: 아파트 전부재 fck=35MPa
철근 재료: D13 이하 SD400, fy=400MPa
"""
        result = _cbl_v5_parse_source(source, pdf_page=9, drawing_number="S-100")
        self.assertEqual(result["concrete_materials"][0]["fck_mpa"], 35)
        self.assertEqual(result["rebar_materials"][0]["grade"], "SD400")
        self.assertEqual(result["rebar_materials"][0]["evidence"]["drawing_number"], "S-100")


class CadPrecheckRegressionTests(SimpleTestCase):
    def _zip_info(self, filename, flag_bits=0):
        info = Mock()
        info.filename = filename
        info.flag_bits = flag_bits
        return info

    def _zip(self, name, members):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            for path, data in members:
                zf.writestr(path, data)
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="application/zip")

    def _legacy_zip(self, name, members, encoding="utf-8", nfd=False):
        """UTF-8 플래그 없이 지정 인코딩의 원시 파일명 바이트를 가진 ZIP을 만든다."""
        local_parts = []
        central_parts = []
        offset = 0
        for path, data in members:
            stored_path = unicodedata.normalize("NFD", path) if nfd else path
            filename_bytes = stored_path.encode(encoding)
            crc = zlib.crc32(data) & 0xffffffff
            local = struct.pack(
                "<IHHHHHIIIHH",
                0x04034B50, 20, 0, 0, 0, 0, crc, len(data), len(data),
                len(filename_bytes), 0,
            ) + filename_bytes + data
            central = struct.pack(
                "<IHHHHHHIIIHHHHHII",
                0x02014B50, 20, 20, 0, 0, 0, 0, crc, len(data), len(data),
                len(filename_bytes), 0, 0, 0, 0, 0, offset,
            ) + filename_bytes
            local_parts.append(local)
            central_parts.append(central)
            offset += len(local)
        body = b"".join(local_parts)
        central = b"".join(central_parts)
        end = struct.pack(
            "<IHHHHIIH",
            0x06054B50, 0, 0, len(members), len(members),
            len(central), len(body), 0,
        )
        return SimpleUploadedFile(
            name, body + central + end, content_type="application/zip",
        )

    def _mock_parsed(self, records):
        return (
            {
                record["content_sha256"]: {"texts": [record["filename"]]}
                for record in records
            },
            {record["content_sha256"] for record in records},
            len(records) > 60,
        )

    def test_recursive_structure_and_xref_paths_are_preserved(self):
        uploaded = self._zip("도면.zip", [
            ("프로젝트/구조/S-011~022 구조일반사항.dwg", b"spec"),
            ("프로젝트/XRef/깊은폴더/S-301 아파트 기둥 일람표.dwg", b"column"),
        ])
        with patch("core.quantity_views._parse_precheck_candidates", side_effect=self._mock_parsed):
            result = _build_cad_precheck([uploaded])
        general = next(item for item in result["structural_checklist"] if item["key"] == "general_spec")
        column = next(item for item in result["structural_checklist"] if item["key"] == "building_column_schedule")
        self.assertEqual(general["files"][0]["path"], "프로젝트/구조/S-011~022 구조일반사항.dwg")
        self.assertEqual(general["files"][0]["source_folder"], "구조")
        self.assertEqual(column["files"][0]["source_folder"], "XRef")

    def test_separate_structure_and_xref_zips_are_merged(self):
        structure_zip = self._zip("구조.zip", [
            ("S-111~130 동 구조평면도.dwg", b"plan"),
        ])
        xref_zip = self._zip("XRef.zip", [
            ("S-311~324 벽체일람표.dwg", b"wall"),
        ])
        with patch("core.quantity_views._parse_precheck_candidates", side_effect=self._mock_parsed):
            result = _build_cad_precheck([structure_zip, xref_zip])
        self.assertEqual(result["scan"]["upload_count"], 2)
        wall = next(item for item in result["structural_checklist"] if item["key"] == "building_wall_schedule")
        self.assertEqual(wall["files"][0]["source_folder"], "XRef")

    def test_actual_structural_filenames_cover_all_thirteen_items(self):
        names = [
            "S-011~022 구조일반사항.dwg",
            "S-101~112 단위세대 구조평면도.dwg",
            "S-201~202 동기초 구조평면도.dwg",
            "S-501 지하주차장 기초 구조평면도.dwg",
            "S-401~403 지하주차장 구조평면도.dwg",
            "S-301 아파트 기둥 일람표.dwg",
            "S-611 주차장 기둥 일람표.dwg",
            "S-311~324 벽체일람표.dwg",
            "S-621~622 주차장 지하외벽 배근도.dwg",
            "S-331~332 아파트 보 일람표.dwg",
            "S-631 주차장 보 일람표.dwg",
            "S-211~222 단위세대 슬래브배근도.dwg",
            "S-641 주차장 슬래브 배근도.dwg",
        ]
        uploaded = self._zip("구조.zip", [(f"구조/{name}", name.encode()) for name in names])
        with patch(
            "core.quantity_views._parse_precheck_candidates",
            return_value=({}, set(), False),
        ):
            result = _build_cad_precheck([uploaded])
        self.assertNotIn("missing", [item["status"] for item in result["structural_checklist"]])

    def test_actual_architectural_filenames_cover_all_thirteen_items(self):
        names = [
            "A-301~324 단위세대 평면도.dwg",
            "A-401~419 주동 평면도.dwg",
            "A-407~423 동입면도.dwg",
            "A-410,424 주동 단면도.dwg",
            "A-501~530 코아 확대평면도.dwg",
            "A-505~531 코아 단면도.dwg",
            "A-701,702 지하주차장 평면도.dwg",
            "A-703,704 주차장 종횡 단면도.dwg",
            "A-705~708 주차장 경사로 상세도.dwg",
            "A-801~814 부대시설 평입단면도.dwg",
            "A-212,213 실내재료마감표.dwg",
            "A-214 창호일람표.dwg",
            "A-015,016 사업개요,동별개요.dwg",
            "A-101~126 면적산출근거표.dwg",
        ]
        uploaded = self._zip("건축.zip", [(f"건축/{name}", name.encode()) for name in names])
        with patch(
            "core.quantity_views._parse_precheck_candidates",
            return_value=({}, set(), False),
        ):
            result = _build_cad_precheck([uploaded])
        self.assertNotIn("missing", [item["status"] for item in result["architectural_checklist"]])

    def test_building_and_parking_schedules_are_independent(self):
        cases = [
            ("S-301 아파트 기둥 일람표.dwg", "building_column_schedule", "parking_column_schedule"),
            ("S-611 주차장 기둥 일람표.dwg", "parking_column_schedule", "building_column_schedule"),
            ("S-311~324 벽체일람표.dwg", "building_wall_schedule", "parking_wall_schedule"),
            ("S-623 주차장 벽체 일람표.dwg", "parking_wall_schedule", "building_wall_schedule"),
            ("S-331~332 아파트 보 일람표.dwg", "building_beam_schedule", "parking_beam_schedule"),
            ("S-631 주차장 보 일람표.dwg", "parking_beam_schedule", "building_beam_schedule"),
            ("S-211~222 단위세대 슬래브배근도.dwg", "building_slab_rebar", "parking_slab_rebar"),
            ("S-641 주차장 슬래브 배근도.dwg", "parking_slab_rebar", "building_slab_rebar"),
        ]
        for filename, found_key, missing_key in cases:
            with self.subTest(filename=filename):
                uploaded = self._zip("구조.zip", [(f"구조/{filename}", filename.encode())])
                with patch(
                    "core.quantity_views._parse_precheck_candidates",
                    return_value=({}, set(), False),
                ):
                    result = _build_cad_precheck([uploaded])
                by_key = {item["key"]: item for item in result["structural_checklist"]}
                self.assertEqual(by_key[found_key]["status"], "candidate_unverified")
                self.assertEqual(by_key[missing_key]["status"], "missing")

    def test_architectural_components_are_independent(self):
        cases = [
            ("A-401~419 주동 평면도.dwg", "building_plans", "unit_plans"),
            ("A-301~324 단위세대 평면도.dwg", "unit_plans", "building_plans"),
            ("A-501~530 코아 확대평면도.dwg", "core_plans", "core_sections"),
            ("A-505~531 코아 단면도.dwg", "core_sections", "core_plans"),
            ("A-212,213 실내재료마감표.dwg", "finish_schedule", "window_schedule"),
            ("A-214 창호일람표.dwg", "window_schedule", "finish_schedule"),
        ]
        for filename, found_key, missing_key in cases:
            with self.subTest(filename=filename):
                uploaded = self._zip("건축.zip", [(f"건축/{filename}", filename.encode())])
                with patch(
                    "core.quantity_views._parse_precheck_candidates",
                    return_value=({}, set(), False),
                ):
                    result = _build_cad_precheck([uploaded])
                by_key = {item["key"]: item for item in result["architectural_checklist"]}
                self.assertEqual(by_key[found_key]["status"], "candidate_unverified")
                self.assertEqual(by_key[missing_key]["status"], "missing")

    def test_run_upload_collection_keeps_two_zips_and_one_direct_dwg(self):
        structure_zip = self._zip("구조.zip", [
            ("구조/S-301 아파트 기둥 일람표.dwg", b"column"),
        ])
        xref_zip = self._zip("XRef.zip", [
            ("XRef/S-311~324 벽체일람표.dwg", b"wall"),
        ])
        direct_dwg = SimpleUploadedFile(
            "S-331~332 아파트 보 일람표.dwg", b"beam",
            content_type="application/octet-stream",
        )
        request = Mock()
        request.FILES.getlist.side_effect = (
            lambda field: [structure_zip, xref_zip, direct_dwg] if field == "cad_files" else []
        )
        uploads = _collect_request_cad_uploads(request)
        self.assertEqual(len(uploads), 3)
        structural_zip_bytes, architectural_zip_bytes, info = _merge_uploaded_cad_sets(uploads)
        self.assertIsNotNone(structural_zip_bytes)
        self.assertIsNone(architectural_zip_bytes)
        self.assertEqual(info["upload_count"], 3)
        self.assertEqual(info["structural_count"], 3)
        with zipfile.ZipFile(io.BytesIO(structural_zip_bytes)) as merged:
            merged_names = merged.namelist()
        self.assertEqual(len(merged_names), 3)
        self.assertTrue(any("XRef" in name for name in merged_names))

    def test_required_candidate_after_sixtieth_non_candidate_is_still_found(self):
        members = [(f"기타/{index:03d} 참고도면.dwg", str(index).encode()) for index in range(70)]
        members.append(("구조/S-011~022 구조일반사항.dwg", b"required"))
        uploaded = self._zip("대형도면.zip", members)
        with patch("core.quantity_views._parse_precheck_candidates", side_effect=self._mock_parsed):
            result = _build_cad_precheck([uploaded])
        general = next(item for item in result["structural_checklist"] if item["key"] == "general_spec")
        self.assertNotEqual(general["status"], "missing")
        self.assertEqual(general["files"][0]["filename"], "S-011~022 구조일반사항.dwg")

    def test_nfd_korean_filename_matches(self):
        nfd_name = unicodedata.normalize("NFD", "S-011~022 구조일반사항.dwg")
        uploaded = self._zip("도면.zip", [(f"구조/{nfd_name}", b"nfd")])
        with patch(
            "core.quantity_views._parse_precheck_candidates",
            return_value=({}, set(), False),
        ):
            result = _build_cad_precheck([uploaded])
        general = next(item for item in result["structural_checklist"] if item["key"] == "general_spec")
        self.assertEqual(general["status"], "candidate_unverified")
        self.assertEqual(general["files"][0]["filename"], "S-011~022 구조일반사항.dwg")

    def test_cp437_misdecoded_nfd_utf8_structural_name_is_recovered(self):
        expected = "구조/S-011~022 구조일반사항.dwg"
        raw = unicodedata.normalize("NFD", expected).encode("utf-8").decode("cp437")
        decoded = _decode_zip_member_name(self._zip_info(raw))
        self.assertEqual(decoded["decoded_name"], expected)
        self.assertEqual(decoded["decode_method"], "legacy_cp437_to_utf8")

    def test_cp437_misdecoded_nfd_utf8_architectural_name_is_recovered(self):
        expected = "건축/A-401~419 주동 평면도.dwg"
        raw = unicodedata.normalize("NFD", expected).encode("utf-8").decode("cp437")
        decoded = _decode_zip_member_name(self._zip_info(raw))
        self.assertEqual(decoded["decoded_name"], expected)
        self.assertEqual(decoded["decode_method"], "legacy_cp437_to_utf8")

    def test_legacy_nfd_utf8_structural_zip_recovers_all_thirteen_candidates(self):
        names = [
            "S-011~022 구조일반사항.dwg",
            "S-101~112 단위세대 구조평면도.dwg",
            "S-201~202 동기초 구조평면도.dwg",
            "S-501 지하주차장 기초 구조평면도.dwg",
            "S-401~403 지하주차장 구조평면도.dwg",
            "S-301 아파트 기둥 일람표.dwg",
            "S-611 주차장 기둥 일람표.dwg",
            "S-311~324 벽체일람표.dwg",
            "S-621~622 주차장 지하외벽 배근도.dwg",
            "S-331~332 아파트 보 일람표.dwg",
            "S-631 주차장 보 일람표.dwg",
            "S-211~222 단위세대 슬래브배근도.dwg",
            "S-641 주차장 슬래브 배근도.dwg",
        ]
        uploaded = self._legacy_zip(
            "구조.zip", [(f"구조/{item}", item.encode()) for item in names], nfd=True,
        )
        with patch(
            "core.quantity_views._parse_precheck_candidates",
            return_value=({}, set(), False),
        ):
            result = _build_cad_precheck([uploaded])
        self.assertNotIn("missing", [item["status"] for item in result["structural_checklist"]])
        self.assertTrue(all(
            file["decode_method"] == "legacy_cp437_to_utf8"
            for item in result["structural_checklist"] for file in item["files"]
        ))

    def test_legacy_nfd_utf8_architectural_zip_recovers_all_thirteen_candidates(self):
        names = [
            "A-401~419 주동 평면도.dwg",
            "A-301~324 단위세대 평면도.dwg",
            "A-407~423 동입면도.dwg",
            "A-410,424 주동 단면도.dwg",
            "A-501~530 코아 확대평면도.dwg",
            "A-505~531 코아 단면도.dwg",
            "A-701,702 지하주차장 평면도.dwg",
            "A-703,704 주차장 종횡 단면도.dwg",
            "A-705~708 주차장 경사로 상세도.dwg",
            "A-801~814 부대시설 평입단면도.dwg",
            "A-212,213 실내재료마감표.dwg",
            "A-214 창호일람표.dwg",
            "A-015,016 사업개요,동별개요,면적산출표.dwg",
        ]
        uploaded = self._legacy_zip(
            "건축.zip", [(f"건축/{item}", item.encode()) for item in names], nfd=True,
        )
        with patch(
            "core.quantity_views._parse_precheck_candidates",
            return_value=({}, set(), False),
        ):
            result = _build_cad_precheck([uploaded])
        self.assertNotIn("missing", [item["status"] for item in result["architectural_checklist"]])
        self.assertTrue(all(
            file["decode_method"] == "legacy_cp437_to_utf8"
            for item in result["architectural_checklist"] for file in item["files"]
        ))

    def test_utf8_flagged_korean_name_is_only_nfc_normalized(self):
        nfd_name = unicodedata.normalize("NFD", "구조/구조일반사항.dwg")
        decoded = _decode_zip_member_name(self._zip_info(nfd_name, flag_bits=0x800))
        self.assertEqual(decoded["decoded_name"], "구조/구조일반사항.dwg")
        self.assertEqual(decoded["decode_method"], "utf8_flag")

    def test_ascii_zip_name_is_unchanged(self):
        decoded = _decode_zip_member_name(self._zip_info("XRef/S-001.dwg"))
        self.assertEqual(decoded["decoded_name"], "XRef/S-001.dwg")
        self.assertEqual(decoded["decode_method"], "unchanged")

    def test_cp949_legacy_zip_name_falls_back_after_utf8(self):
        expected = "구조/S-301 아파트 기둥 일람표.dwg"
        raw = expected.encode("cp949").decode("cp437")
        decoded = _decode_zip_member_name(self._zip_info(raw))
        self.assertEqual(decoded["decoded_name"], expected)
        self.assertEqual(decoded["decode_method"], "legacy_cp437_to_cp949")

    def test_macos_appledouble_dwg_is_not_counted(self):
        uploaded = self._zip("구조.zip", [
            ("__MACOSX/구조/._S-011~022 구조일반사항.dwg", b"metadata"),
        ])
        result = _build_cad_precheck([uploaded])
        self.assertEqual(result["scan"]["cad_count"], 0)
        self.assertEqual(result["scan"]["excluded"][0]["reason"], "macOS 메타데이터")

    def test_real_dwg_and_appledouble_are_counted_once(self):
        uploaded = self._zip("구조.zip", [
            ("구조/S-011~022 구조일반사항.dwg", b"real"),
            ("__MACOSX/구조/._S-011~022 구조일반사항.dwg", b"metadata"),
        ])
        with patch(
            "core.quantity_views._parse_precheck_candidates",
            return_value=({}, set(), False),
        ):
            result = _build_cad_precheck([uploaded])
        self.assertEqual(result["scan"]["cad_count"], 1)
        general = next(item for item in result["structural_checklist"] if item["key"] == "general_spec")
        self.assertEqual(len(general["files"]), 1)

    def test_bak_only_is_not_accepted_as_drawing(self):
        uploaded = self._zip("구조.zip", [
            ("구조/S-011~022 구조일반사항.bak", b"backup"),
        ])
        result = _build_cad_precheck([uploaded])
        general = next(item for item in result["structural_checklist"] if item["key"] == "general_spec")
        self.assertEqual(general["status"], "missing")
        self.assertEqual(result["scan"]["cad_count"], 0)
        self.assertEqual(result["scan"]["excluded"][0]["reason"], "기본도면 제외 확장자")

    def test_parse_failure_is_candidate_unverified_not_missing(self):
        uploaded = self._zip("구조.zip", [
            ("구조/S-011~022 구조일반사항.dwg", b"broken-dwg"),
        ])

        def failed(records):
            return (
                {record["content_sha256"]: {"error": "mock conversion failure"} for record in records},
                {record["content_sha256"] for record in records},
                False,
            )

        with patch("core.quantity_views._parse_precheck_candidates", side_effect=failed):
            result = _build_cad_precheck([uploaded])
        general = next(item for item in result["structural_checklist"] if item["key"] == "general_spec")
        self.assertEqual(general["status"], "candidate_unverified")
        self.assertIn("실제 내용 확인", general["reason"])

    def test_single_zip_upload_remains_supported(self):
        uploaded = self._zip("구조.zip", [
            ("S-401~403 지하주차장 구조평면도.dwg", b"parking"),
        ])
        with patch(
            "core.quantity_views._parse_precheck_candidates",
            return_value=({}, set(), False),
        ):
            result = _build_cad_precheck([uploaded])
        self.assertEqual(result["scan"]["upload_count"], 1)
        parking = next(item for item in result["structural_checklist"] if item["key"] == "parking_structure_plan")
        self.assertEqual(parking["status"], "candidate_unverified")

    def test_legacy_single_zip_request_field_remains_supported(self):
        uploaded = self._zip("구조.zip", [
            ("S-401~403 지하주차장 구조평면도.dwg", b"parking"),
        ])
        request = Mock(method="POST")
        request.user = Mock(is_authenticated=True, is_staff=True, is_superuser=False)
        request.FILES.getlist.side_effect = lambda field: [uploaded] if field == "zip_file" else []
        with patch(
            "core.quantity_views._build_cad_precheck",
            return_value={"structural_checklist": [], "architectural_checklist": [], "scan": {}},
        ) as build:
            response = api_check_zip(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(build.call_args.args[0]), 1)

    def test_legacy_architectural_zip_role_is_preserved_for_run_merge(self):
        uploaded = self._zip("legacy.zip", [("하위/상세도.dwg", b"architecture")])
        request = Mock()
        request.FILES.getlist.side_effect = (
            lambda field: [uploaded] if field == "architectural_zip" else []
        )
        uploads = _collect_request_cad_uploads(request)
        structural_zip_bytes, architectural_zip_bytes, info = _merge_uploaded_cad_sets(uploads)
        self.assertIsNone(structural_zip_bytes)
        self.assertIsNotNone(architectural_zip_bytes)
        self.assertEqual(info["architectural_count"], 1)


class GeneralNotesRegressionTests(SimpleTestCase):
    def _evidence(self, quote, page=3, drawing="S-011"):
        return {
            "file_type": "구조 PDF", "pdf_page": page,
            "drawing_number": drawing, "drawing_title": "구조일반사항(1)",
            "quote": quote, "method": "pdf_image", "confidence": 0.97,
        }

    def _source_text(self, data):
        quotes = []
        for group in (
            "concrete_materials", "rebar_materials", "cover_requirements",
            "anchorage_splice_requirements", "quantity_notes",
        ):
            for row in data.get(group) or []:
                quote = ((row or {}).get("evidence") or {}).get("quote")
                if quote:
                    quotes.append(quote)
        return "\n".join(quotes)

    @patch("core.quantity_views.os.remove")
    @patch("core.quantity_views.tempfile.NamedTemporaryFile")
    @patch("core.quantity_views.subprocess.run")
    def test_candidate_selection_recognizes_s011_to_s022(self, run, named, _remove):
        handle = Mock()
        handle.name = "/tmp/general-notes.pdf"
        named.return_value.__enter__.return_value = handle
        texts = {
            1: "도면목록",
            2: "S-011 구조일반사항 콘크리트 철근 피복두께",
            3: "S-012 GENERAL NOTES 정착 이음",
        }
        run.side_effect = lambda args, **kwargs: SimpleNamespace(
            returncode=0, stdout=texts.get(int(args[2]), "").encode(),
        )
        result = _general_notes_page_candidates(b"pdf", 3)
        self.assertEqual(result["selected_pages"], [2, 3])
        self.assertGreater(result["pages"][1]["score"], result["pages"][0]["score"])

    @patch("core.quantity_views.os.remove")
    @patch("core.quantity_views.tempfile.NamedTemporaryFile")
    @patch("core.quantity_views.subprocess.run")
    def test_drawing_list_is_rejected_and_image_pages_are_selected(self, run, named, _remove):
        handle = Mock()
        handle.name = "/tmp/general-notes.pdf"
        named.return_value.__enter__.return_value = handle
        drawing_list = "도면목록 도면번호 도면명 비고 S-011~S-022 구조일반사항"
        run.side_effect = lambda args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=(drawing_list if int(args[2]) == 1 else "").encode(),
        )
        scan = _general_notes_page_candidates(b"pdf", 79)
        decisions = [{
            "pdf_page": page,
            "page_type": "general_notes",
            "drawing_number": f"S-{number:03d}",
            "drawing_title": f"구조일반사항({number - 10})",
            "is_general_notes": True,
            "confidence": 0.98,
            "evidence_terms": ["구조일반사항"],
        } for page, number in zip(range(3, 15), range(11, 23))]
        selected, mapping = _merge_general_notes_page_candidates(scan, decisions)
        self.assertEqual(scan["pages"][0]["page_type"], "drawing_list")
        self.assertEqual(scan["pages"][0]["rejection_reason"], "drawing_list_not_content")
        self.assertEqual(
            scan["expected_drawing_numbers"],
            [f"S-{number:03d}" for number in range(11, 23)],
        )
        self.assertNotIn(1, selected)
        self.assertEqual(selected, list(range(3, 15)))
        self.assertEqual(mapping["S-011"], 3)
        self.assertEqual(mapping["S-022"], 14)

    def test_v5_material_page_uses_overview_content_not_drawing_list(self):
        scan = {"drawing_list_pages": [1]}
        decisions = [{
            "pdf_page": 3, "page_type": "general_notes",
            "drawing_number": None, "drawing_title": "",
            "is_general_notes": True,
            "evidence_terms": ["구조재료 및 강도", "콘크리트", "철근"],
        }]
        page = _cbl_v5_select_material_page(scan, decisions, [], {})
        self.assertEqual(page, 3)
        self.assertNotEqual(page, 1)

    def test_v5_actual_material_transcription_is_source_grounded(self):
        source = """A. 구조개요
6. 구조재료 및 강도
6.1 콘크리트
1) 아파트 전부재(기초 제외) : fck = 30MPa
2) 아파트 기초, 주차장 전부재 : fck = 24MPa
6.2 철근
1) D13이하 : SD500, fy=500MPa
2) D16이상 : SD600, fy=600MPa
3) D13이하 : SD500S, fy=500MPa (내진용철근)
4) D16이하 : SD600S, fy=600MPa (내진용철근)
7. 특기사항"""
        parsed = _cbl_v5_parse_source(source, pdf_page=3, drawing_number="S-011")
        result = _validate_general_notes_result(
            parsed, [3], source_text=source,
        )
        self.assertEqual(
            [row["fck_mpa"] for row in result["concrete_materials"]], [30, 24],
        )
        self.assertEqual(
            [row["grade"] for row in result["rebar_materials"]],
            ["SD500", "SD600", "SD500S", "SD600S"],
        )
        self.assertEqual(
            [row["diameter_rule"] for row in result["rebar_materials"]],
            ["D13 이하", "D16 이상", "D13 이하", "D16 이하"],
        )
        self.assertNotIn(35, [row["fck_mpa"] for row in result["concrete_materials"]])
        self.assertNotIn("SD400", [row["grade"] for row in result["rebar_materials"]])

    def test_filename_without_parsed_cad_text_cannot_become_evidence(self):
        result = _validate_general_notes_result({
            "basic_info": {
                "structure_system": {
                    "value": "철근콘크리트조",
                    "evidence": {
                        "file_type": "구조 CAD", "pdf_page": None,
                        "drawing_number": "S-011", "drawing_title": "구조일반사항",
                        "quote": "", "method": "filename", "confidence": 0.9,
                    },
                },
            },
        }, [])
        self.assertNotIn("structure_system", result["basic_info"])

    @patch("core.quantity_views.image_to_jpeg_bytes", return_value=b"jpeg")
    @patch("core.quantity_views._render_pdf_page_range", return_value=[Mock()])
    @patch("core.quantity_views._cbl_v5_classify_general_notes")
    @patch("core.quantity_views._general_notes_page_candidates")
    @patch("core.quantity_views.pdfinfo_from_bytes", return_value={"Pages": 79})
    @patch("core.quantity_views.get_gemini_client")
    def test_extraction_sends_content_pages_not_drawing_list(
        self, get_client, _pdfinfo, candidates, classify, _render, _jpeg,
    ):
        panel = SimpleNamespace(size=(1000, 2000))
        image = SimpleNamespace(size=(2000, 3000), crop=Mock(return_value=panel))
        _render.return_value = [image]
        candidates.return_value = {
            "pages": [{"page": 1, "score": 60, "selected": False,
                       "page_type": "drawing_list", "reasons": [],
                       "drawing_numbers": [f"S-{n:03d}" for n in range(11, 23)],
                       "text_available": True, "text_error": None,
                       "rejection_reason": "drawing_list_not_content"}],
            "scan_range": [1, 40], "selected_pages": [], "text_used": True,
            "image_fallback": False, "drawing_list_pages": [1],
            "expected_drawing_numbers": [f"S-{n:03d}" for n in range(11, 23)],
        }
        classify.return_value = [{
            "pdf_page": page, "page_type": "general_notes",
            "drawing_number": f"S-{number:03d}", "drawing_title": "구조일반사항",
            "is_general_notes": True, "confidence": 0.98,
            "evidence_terms": ["구조일반사항"],
        } for page, number in zip(range(3, 15), range(11, 23))]
        payload = {
            "source_text": "철근콘크리트조",
            "basic_info": {
                "structure_system": {
                    "value": "철근콘크리트조",
                    "evidence": self._evidence("철근콘크리트조", page=3),
                },
            },
        }
        response = SimpleNamespace(text=json.dumps(payload, ensure_ascii=False),
                                   candidates=[], usage_metadata=None)
        client = Mock()
        client.models.generate_content.return_value = response
        get_client.return_value = client

        result = extract_general_notes(b"pdf", job_id="mock-image-locator")

        self.assertEqual(result["selected_pages"], [3])
        contents = client.models.generate_content.call_args.kwargs["contents"]
        labels = [item for item in contents if isinstance(item, str)]
        self.assertFalse(any("실제 1페이지" in item for item in labels))
        self.assertTrue(any("실제 3페이지" in item for item in labels))
        self.assertFalse(any("실제 14페이지" in item for item in labels))

    @patch("core.quantity_views._cbl_v5_classify_general_notes", return_value=[])
    @patch("core.quantity_views._general_notes_page_candidates")
    @patch("core.quantity_views.pdfinfo_from_bytes", return_value={"Pages": 12})
    @patch("core.quantity_views.get_gemini_client")
    def test_failed_image_locator_uses_parsed_cad_text_fallback(
        self, get_client, _pdfinfo, candidates, _classify,
    ):
        candidates.return_value = {
            "pages": [], "scan_range": [1, 12], "selected_pages": [],
            "text_used": False, "image_fallback": False,
            "drawing_list_pages": [1], "expected_drawing_numbers": ["S-011"],
        }
        client = Mock()
        get_client.return_value = client
        cad_text = """6. 구조재료 및 강도
6.1 콘크리트
1) 기초 전 부재 : fck = 30MPa
7. 특기사항"""

        result = extract_general_notes(
            b"pdf", job_id="mock-cad-fallback",
            cad_context=[{"path": "구조/S-011~022 구조일반사항.dwg",
                          "filename": "S-011~022 구조일반사항.dwg",
                          "text": cad_text}],
        )

        self.assertEqual(result["concrete_materials"][0]["fck_mpa"], 30)
        client.models.generate_content.assert_not_called()
        self.assertEqual(result["diagnostics"]["cad_fallback_reason"],
                         "no_pdf_content_candidate")

    def test_empty_result_ui_does_not_offer_confirmation_and_hides_internal_keys(self):
        template = Path(__file__).with_name("templates").joinpath("core", "home.html").read_text()
        self.assertIn('structure_system: "구조방식"', template)
        self.assertIn('foundation_type: "기초형식"', template)
        self.assertIn('askChoice(["다시 확인", "도면 추가/교체", "미확인 항목 직접 입력"]', template)
        empty_branch = template[
            template.index("if (!hasValidValues)"):
            template.index('askChoice(["구조일반사항 확인 완료"', template.index("if (!hasValidValues)"))
        ]
        self.assertNotIn('"구조일반사항 확인 완료"', empty_branch)

    def test_member_rebar_stage_has_independent_locked_cache_slots(self):
        state = _empty_member_rebar_check_state()
        self.assertEqual(
            list(state),
            ["columns", "walls", "beams", "slabs", "foundations", "parking"],
        )
        self.assertTrue(all(item["status"] == "locked" for item in state.values()))
        state["columns"]["status"] = "ready"
        self.assertEqual(state["walls"]["status"], "locked")

    def test_member_rebar_buttons_are_scaffolded_without_ai_calls(self):
        template = Path(__file__).with_name("templates").joinpath("core", "home.html").read_text()
        for label in (
            "기둥 배근 확인", "벽체·전단벽 배근 확인", "보 배근 확인",
            "슬래브 배근 확인", "기초 배근 확인", "지하주차장 배근 확인",
        ):
            self.assertIn(label, template)
        self.assertIn("data-member-rebar-key=", template)
        self.assertIn("구조일반사항 확인 후 사용 가능", template)
        scaffold = template[
            template.index("function showMemberRebarCheckStage"):
            template.index('// "아니요"', template.index("function showMemberRebarCheckStage"))
        ]
        self.assertNotIn("fetch(", scaffold)

    def test_review_steps_are_manual_and_cache_aware(self):
        template = Path(__file__).with_name("templates").joinpath("core", "home.html").read_text()
        overview_confirm = template[
            template.index("if (val === OVERVIEW_CONTINUE_CHOICE)"):
            template.index('} else if (val === "아니요, 수정할게요")')
        ]
        self.assertNotIn(".then(function () { runGeneralNotesCheck", overview_confirm)
        self.assertIn("3. 구조일반사항 확인", overview_confirm)
        self.assertIn("stageCache.overview", template)
        self.assertIn("stageCache.generalSpec", template)
        self.assertIn('fd.append("force", "true")', template)

    def test_material_ranges_and_cover_conditions_remain_separate(self):
        data = {
            "basic_info": {},
            "concrete_materials": [
                {"location": "지하", "member_type": "기둥", "floor_scope": "지하층",
                 "fck_mpa": 30, "evidence": self._evidence("구조재료 및 강도 | 지하층 기둥 fck 30 MPa")},
                {"location": "지상", "member_type": "기둥", "floor_scope": "지상층",
                 "fck_mpa": 27, "evidence": self._evidence("구조재료 및 강도 | 지상층 기둥 fck 27 MPa")},
            ],
            "rebar_materials": [
                {"diameter_min_mm": 10, "diameter_max_mm": 16, "grade": "SD400",
                 "fy_mpa": 400, "member_scope": None,
                 "evidence": self._evidence("D10~D16 SD400 fy 400 MPa")},
                {"diameter_min_mm": 19, "diameter_max_mm": 35, "grade": "SD500",
                 "fy_mpa": 500, "member_scope": None,
                 "evidence": self._evidence("D19~D35 SD500 fy 500 MPa")},
            ],
            "cover_requirements": [
                {"member_type": "기초", "exposure_condition": "토양 접촉", "location": "기초",
                 "thickness_mm": 80, "evidence": self._evidence("기초 토양 접촉 피복 80 mm")},
                {"member_type": "슬래브", "exposure_condition": "내부", "location": "지상",
                 "thickness_mm": 20, "evidence": self._evidence("지상 내부 슬래브 피복 20 mm")},
            ],
            "anchorage_splice_requirements": [],
            "quantity_notes": [], "conflicts": [], "unconfirmed_items": [],
        }
        result = _validate_general_notes_result(data, [3], source_text=self._source_text(data))
        self.assertEqual([r["fck_mpa"] for r in result["concrete_materials"]], [30, 27])
        self.assertEqual([r["grade"] for r in result["rebar_materials"]], ["SD400", "SD500"])
        self.assertEqual([r["thickness_mm"] for r in result["cover_requirements"]], [80, 20])

    def test_only_explicit_material_strength_values_are_applied(self):
        applied = [
            {"location": "아파트", "member_type": "전 부재(기초 제외)",
             "floor_scope": None, "fck_mpa": 30,
             "evidence": self._evidence(
                 "구조재료 및 강도 | 아파트 전 부재(기초 제외) fck 30 MPa"
             )},
            {"location": "아파트 기초 및 지하주차장", "member_type": "전 부재",
             "floor_scope": None, "fck_mpa": 24,
             "evidence": self._evidence(
                 "구조재료 및 강도 | 아파트 기초 및 지하주차장 전 부재 fck 24 MPa"
             )},
        ]
        lookup_noise = [
            {"location": "B급 이음길이표", "member_type": "표", "floor_scope": "lookup",
             "fck_mpa": value, "evidence": self._evidence(f"B급 이음길이표 fck {value} MPa")}
            for value in (35, 40)
        ]
        data = {"concrete_materials": applied + lookup_noise}
        result = _validate_general_notes_result(
            data, [3], source_text=self._source_text({"concrete_materials": applied}),
        )
        self.assertEqual([row["fck_mpa"] for row in result["concrete_materials"]], [30, 24])
        self.assertNotIn(35, [row["fck_mpa"] for row in result["concrete_materials"]])
        self.assertNotIn(40, [row["fck_mpa"] for row in result["concrete_materials"]])

    def test_fck_35_40_and_sd400_are_allowed_when_present_in_source(self):
        data = {
            "concrete_materials": [
                {"location": "타 프로젝트 A동", "member_type": "기둥", "floor_scope": None,
                 "fck_mpa": value,
                 "evidence": self._evidence(
                     f"구조재료 및 강도 | 타 프로젝트 A동 기둥 fck {value} MPa"
                 )}
                for value in (35, 40)
            ],
            "rebar_materials": [{
                "diameter_min_mm": None, "diameter_max_mm": 13, "grade": "SD400",
                "fy_mpa": 400, "member_scope": None,
                "evidence": self._evidence("D13 이하 SD400 fy 400 MPa"),
            }],
        }
        result = _validate_general_notes_result(data, [3], source_text=self._source_text(data))
        self.assertEqual([row["fck_mpa"] for row in result["concrete_materials"]], [35, 40])
        self.assertEqual([row["grade"] for row in result["rebar_materials"]], ["SD400"])

    def test_fabricated_evidence_quote_is_rejected_against_source_text(self):
        data = {"concrete_materials": [{
            "location": "아파트", "member_type": "기둥", "floor_scope": None,
            "fck_mpa": 40,
            "evidence": self._evidence("구조재료 및 강도 | 아파트 기둥 fck 40 MPa"),
        }]}
        result = _validate_general_notes_result(
            data, [3],
            source_text="구조재료 및 강도 | 아파트 기둥 fck 30 MPa",
        )
        self.assertEqual(result["concrete_materials"], [])
        self.assertTrue(any("원문 불일치" in item for item in result["validation_rejections"]))

    def test_rebar_grades_are_split_without_sd400(self):
        rows = [
            (None, 13, "SD500", 500, "D13 이하 SD500 fy 500 MPa"),
            (16, None, "SD600", 600, "D16 이상 SD600 fy 600 MPa"),
            (None, 13, "SD500S", 500, "내진 D13 이하 SD500S fy 500 MPa"),
            (16, None, "SD600S", 600, "내진 D16 이상 SD600S fy 600 MPa"),
            (None, 10, "SD400", 400, "D10 이하 SD400 fy 400 MPa"),
        ]
        data = {"rebar_materials": [{
            "diameter_min_mm": low, "diameter_max_mm": high, "grade": grade,
            "fy_mpa": fy, "member_scope": "내진" if grade.endswith("S") else None,
            "evidence": self._evidence(quote),
        } for low, high, grade, fy, quote in rows]}
        source_without_sd400 = "\n".join(quote for *_, grade, __, quote in rows if grade != "SD400")
        result = _validate_general_notes_result(data, [3], source_text=source_without_sd400)
        self.assertEqual(
            [row["grade"] for row in result["rebar_materials"]],
            ["SD500", "SD600", "SD500S", "SD600S"],
        )
        self.assertNotIn("SD400", [row["grade"] for row in result["rebar_materials"]])

    def test_b_class_length_table_is_internal_lookup_only(self):
        row = {
            "requirement_type": "인장이음", "bar_size": "D25", "position": "상부",
            "concrete_fck_mpa": 35, "value": 1.5, "unit": "m",
            "splice_class": "B", "conditions": "fck 35 MPa",
            "evidence": self._evidence("B급 인장이음 길이표 | D25 | fck 35 MPa | 1.5 m"),
        }
        data = {"anchorage_splice_requirements": [row]}
        result = _validate_general_notes_result(data, [3], source_text=self._source_text(data))
        self.assertEqual(result["anchorage_splice_requirements"], [])
        self.assertEqual(len(result["lookup_data"]["splice_length_rows"]), 1)
        self.assertEqual(result["quantity_notes"][0]["text"], "전 부재 B급 인장이음 적용")
        self.assertEqual(result["quantity_notes"][0]["source_type"], "user_confirmed")

    def test_anchorage_conditions_are_separate_and_evidence_less_value_is_rejected(self):
        good = {
            "requirement_type": "인장이음", "bar_size": "D25", "position": "상부",
            "value": 1.5, "unit": "m", "conditions": "fck 30 MPa",
            "evidence": self._evidence("D25 상부 인장이음 fck 30 MPa 1.5 m"),
        }
        bad = {
            "requirement_type": "압축정착", "bar_size": "D25", "position": "하부",
            "value": 1.1, "unit": "m", "conditions": "fck 30 MPa", "evidence": None,
        }
        data = {
            "anchorage_splice_requirements": [good, bad],
        }
        result = _validate_general_notes_result(data, [3], source_text=self._source_text(data))
        self.assertEqual(len(result["anchorage_splice_requirements"]), 1)
        self.assertTrue(any("구조화 근거 누락" in item for item in result["validation_rejections"]))

    def test_conflicting_values_are_not_overwritten(self):
        conflicts = [{
            "field": "fck_mpa", "scope": "지하 기둥", "values": [30, 35],
            "message": "페이지별 값 충돌",
            "evidences": [self._evidence("30 MPa"), self._evidence("35 MPa", page=4)],
        }]
        result = _validate_general_notes_result({"conflicts": conflicts}, [3, 4])
        self.assertEqual(result["conflicts"][0]["values"], [30, 35])

    def test_converted_cad_text_evidence_is_allowed_without_pdf_page(self):
        evidence = {
            "file_type": "구조 CAD", "pdf_page": None, "drawing_number": "S-011",
            "drawing_title": "구조일반사항(1)", "quote": "구조재료 및 강도 | 기초 fck 30 MPa",
            "method": "cad_text", "confidence": 0.85,
        }
        data = {
            "concrete_materials": [{
                "location": "기초", "member_type": "기초", "floor_scope": None,
                "fck_mpa": 30, "evidence": evidence,
            }],
        }
        result = _validate_general_notes_result(data, [], source_text=self._source_text(data))
        self.assertEqual(result["concrete_materials"][0]["fck_mpa"], 30)

    def test_overview_must_be_confirmed_before_general_notes_endpoint(self):
        review_id = "general-notes-gate"
        _review_ensure(review_id, user_id=7, file_hashes={})
        request = Mock(method="POST")
        request.user = Mock(pk=7, is_authenticated=True, is_staff=True, is_superuser=False)
        request.POST.get.side_effect = lambda key, default=None: {
            "job_id": "job-1", "review_id": review_id,
        }.get(key, default)
        response = api_quantity_general_notes_check(request)
        self.assertEqual(response.status_code, 409)

    @patch("core.quantity_views._general_notes_log")
    @patch("core.quantity_views._result_set")
    @patch("core.quantity_views.extract_general_notes", side_effect=TimeoutError("timeout"))
    def test_timeout_is_recorded(self, _extract, result_set, log):
        review_id = "general-notes-timeout"
        _review_ensure(review_id, user_id=8, file_hashes={})
        _review_update(review_id, overview={"structure_type": "철근콘크리트조"})
        _run_general_notes_job("job-timeout", review_id, b"pdf", {})
        self.assertFalse(result_set.call_args.args[1]["ok"])
        self.assertTrue(any(call.args[0] == "job_timeout" for call in log.call_args_list))


class OjeongOverviewRegressionTests(SimpleTestCase):
    PROJECT_NAME = "오정동 139-5번지 외 12필지 가로주택정비사업"

    def _source(self, quote, page=3, table="사업개요"):
        return {"pdf_type": "건축", "page": page, "table": table, "quote": quote}

    def setUp(self):
        with _OVERVIEW_CLASSIFICATION_LOCK:
            _OVERVIEW_CLASSIFICATION_CACHE.clear()

    def _correct_payload(self):
        source_101 = self._source(
            "101동 | 지상 1~13층 | 건축면적 599.79㎡ | 연면적 5,508.48㎡ | 63세대",
            page=8, table="동별자료",
        )
        source_102 = self._source(
            "102동 | 지상 1~13층 | 건축면적 703.49㎡ | 연면적 5,771.37㎡ | 68세대",
            page=8, table="동별자료",
        )
        facility_names = ["셔틀코어", "차량램프·제연휀룸", "통신실", "창고"]
        return {
            "overview": {
                "project_name": self.PROJECT_NAME,
                "site_location": "경기도 부천시 오정동 139-5번지 일원",
                "usage": "공동주택 및 부대복리시설",
                "structure_type": "철근콘크리트조",
                "basement_floor_count": 2,
                "aboveground_max_floor": 13,
                "household_count": 131,
                "site_area_m2": 4566.90,
                "building_area_m2": 1382.47,
                "apartment_total_floor_area_m2": 11279.85,
                "aboveground_floor_area_m2": 11414.31,
                "basement_floor_area_m2": 6354.58,
                "total_floor_area_m2": 17768.89,
                "buildings": [
                    {"label": "101동", "floor_range": "지상 1~13층", "floor_count": 13,
                     "building_area_m2": 599.79, "total_floor_area_m2": 5508.48,
                     "household_count": 63, "source": source_101},
                    {"label": "102동", "floor_range": "지상 1~13층", "floor_count": 13,
                     "building_area_m2": 703.49, "total_floor_area_m2": 5771.37,
                     "household_count": 68, "source": source_102},
                ],
                "amenity_facilities": [
                    {
                        "label": name,
                        "building_area_m2": 79.19 if name == "셔틀코어" else None,
                        "total_floor_area_m2": None,
                        "source": self._source(
                            f"{name}" + (" | 건축면적 79.19㎡" if name == "셔틀코어" else ""),
                            page=8, table="부대복리시설",
                        ),
                    }
                    for name in facility_names
                ],
                "sources": {
                    "project_name": [
                        self._source(f"사업명칭: {self.PROJECT_NAME}"),
                        self._source(f"PROJECT TITLE: {self.PROJECT_NAME}", page=1, table="표지"),
                    ],
                    "basement_floor_count": self._source("규모: 지하 2층 / 지상 13층"),
                    "aboveground_max_floor": self._source("규모: 지하 2층 / 지상 13층"),
                    "household_count": self._source("세대수 합계 131세대", page=8, table="동별자료"),
                    "building_area_m2": self._source("건축면적 합계 1,382.47㎡", page=7),
                    "aboveground_floor_area_m2": self._source("지상 연면적 11,414.31㎡", page=7),
                    "basement_floor_area_m2": self._source("지하 연면적 6,354.58㎡", page=7),
                    "total_floor_area_m2": self._source("전체 연면적 17,768.89㎡", page=7),
                },
            }
        }

    def test_explicit_floor_phrase_is_parsed(self):
        text = "주요구조/규모: 철근콘크리트조, 지하2층/지상13층"
        self.assertEqual(_parse_explicit_floor_count(text, "basement"), 2)
        self.assertEqual(_parse_explicit_floor_count(text, "aboveground"), 13)

    def test_drawing_numbers_never_create_floors(self):
        self.assertIsNone(_parse_explicit_floor_count("A-101~A-126", "basement"))
        self.assertIsNone(_parse_explicit_floor_count("A-401~A-424", "aboveground"))
        self.assertIsNone(_parse_explicit_floor_count("A-251~A-258", "rooftop"))

        payload = self._correct_payload()
        payload["overview"]["basement_floor_count"] = 26
        payload["overview"]["aboveground_max_floor"] = 24
        payload["overview"]["buildings"][0]["rooftop_floor_count"] = 8
        payload["overview"]["sources"]["basement_floor_count"] = self._source("도면목록 A-101~A-126")
        payload["overview"]["sources"]["aboveground_max_floor"] = self._source("도면목록 A-401~A-424")
        payload["overview"]["sources"]["buildings"] = self._source("101동 A-251~A-258", table="도면목록")
        result = _fill_overview_spec_defaults(payload)["overview"]
        self.assertIsNone(result["basement_floor_count"], "A-126이 지하 26층이 되면 실패")
        self.assertIsNone(result["aboveground_max_floor"], "A-424가 지상 24층이 되면 실패")
        self.assertFalse(
            any(b.get("rooftop_floor_count") == 8 for b in result["buildings"]),
            "A-258이 옥탑 8층이 되면 실패",
        )

    def test_ojeong_answer_is_preserved_with_pdf_evidence(self):
        payload = deepcopy(self._correct_payload())
        # PROJECT TITLE은 보조 교차검증일 뿐이며 사업명칭 셀 하나만 명확해도 인정한다.
        payload["overview"]["sources"]["project_name"] = [
            self._source("사업명칭:\n오정동 139-5번지 외 12필지 가로주택정비사업"),
        ]
        result = _fill_overview_spec_defaults(payload)["overview"]
        self.assertEqual(result["project_name"], self.PROJECT_NAME)
        self.assertEqual(result["basement_floor_count"], 2)
        self.assertEqual(result["aboveground_max_floor"], 13)
        self.assertEqual([b["label"] for b in result["buildings"]], ["101동", "102동"])
        self.assertIn("번지", result["project_name"])
        self.assertEqual(result["building_area_m2"], 1382.47)
        self.assertEqual(result["buildings"][0]["household_count"], 63)
        self.assertEqual(result["buildings"][1]["household_count"], 68)
        self.assertIn("셔틀코어", [row["label"] for row in result["amenity_facilities"]])
        self.assertEqual(result["conflicts"], [])

    def test_direct_building_area_is_not_replaced_or_compared_with_partial_rows(self):
        payload = self._correct_payload()
        payload["overview"]["building_area_m2"] = 1362.47
        payload["overview"]["sources"]["building_area_m2"] = self._source(
            "건축면적 합계 1,362.47㎡", page=7,
        )
        payload["overview"]["buildings"] = []
        payload["overview"]["amenity_facilities"] = [
            {
                "label": label, "building_area_m2": value, "total_floor_area_m2": None,
                "source": self._source(
                    f"{label} | 건축면적 {value:,.2f}㎡",
                    page=7, table="부대복리시설 설치계획",
                ),
            }
            for label, value in (
                ("주민공동시설", 257.38), ("경비실", 19.56),
                ("펌프전기실", 202.32), ("정화조(관리동)", 32.26),
            )
        ]
        result = _fill_overview_spec_defaults(payload)["overview"]
        self.assertEqual(result["building_area_m2"], 1362.47)
        self.assertFalse(any(
            item["field"] == "building_area_m2" for item in result["conflicts"]
        ))

    def test_project_totals_are_never_replaced_by_component_checks(self):
        payload = self._correct_payload()
        payload["overview"]["apartment_total_floor_area_m2"] = 12000.0
        payload["overview"]["household_count"] = 140
        payload["overview"]["total_floor_area_m2"] = 18000.0
        payload["overview"]["sources"]["household_count"] = self._source(
            "세대수 합계 140세대", page=8, table="동별자료",
        )
        payload["overview"]["sources"]["total_floor_area_m2"] = self._source(
            "전체 연면적 18,000.00㎡", page=7,
        )
        result = _fill_overview_spec_defaults(payload)["overview"]
        self.assertEqual(result["apartment_total_floor_area_m2"], 12000.0)
        self.assertEqual(result["household_count"], 140)
        self.assertEqual(result["total_floor_area_m2"], 18000.0)

    def test_facility_numbers_are_validated_individually_against_quote(self):
        payload = self._correct_payload()
        payload["overview"]["amenity_facilities"] = [
            {
                "label": "주민공동시설",
                "building_area_m2": 257.38,
                "total_floor_area_m2": 263.44,
                "household_count": None,
                "source": self._source(
                    "주민공동시설 | 지하2층~지하1층 | 257.38 | - | 257.38",
                    page=7, table="부대복리시설 설치계획",
                ),
            },
            {
                "label": "관리사무소",
                "building_area_m2": None,
                "total_floor_area_m2": 113.15,
                "source": self._source(
                    "관리사무소 | 지상1층 | - | 113.15 | 113.15",
                    page=7, table="부대복리시설 설치계획",
                ),
            },
            {
                "label": "경비실",
                "building_area_m2": 19.56,
                "total_floor_area_m2": 34.81,
                "household_count": 1,
                "source": self._source(
                    "경비실 | 지하1층,지상1층 | 19.56 | 15.25 | 34.81 | 1세대",
                    page=7, table="부대복리시설 설치계획",
                ),
            },
        ]
        result = _fill_overview_spec_defaults(payload)["overview"]
        by_label = {row["label"]: row for row in result["amenity_facilities"]}
        self.assertEqual(by_label["주민공동시설"]["building_area_m2"], 257.38)
        self.assertIsNone(by_label["주민공동시설"]["total_floor_area_m2"])
        self.assertEqual(by_label["관리사무소"]["total_floor_area_m2"], 113.15)
        self.assertEqual(by_label["경비실"]["building_area_m2"], 19.56)
        self.assertEqual(by_label["경비실"]["total_floor_area_m2"], 34.81)
        self.assertEqual(by_label["경비실"]["household_count"], 1)
        self.assertTrue(any(
            "주민공동시설 연면적" in item and "source.quote" in item
            for item in result["unconfirmed_items"]
        ))

    def test_building_row_keeps_only_numbers_present_in_its_quote(self):
        payload = self._correct_payload()
        payload["overview"]["buildings"][0]["total_floor_area_m2"] = 9999.99
        result = _fill_overview_spec_defaults(payload)["overview"]
        building = next(row for row in result["buildings"] if row["label"] == "101동")
        self.assertEqual(building["building_area_m2"], 599.79)
        self.assertIsNone(building["total_floor_area_m2"])
        self.assertEqual(building["household_count"], 63)

    def test_business_name_table_delimiters_are_removed(self):
        for quote in (
            f"사업명칭 | {self.PROJECT_NAME}",
            f"사업명칭: {self.PROJECT_NAME}",
            f"사업명칭 ： {self.PROJECT_NAME}",
            f"사업명칭 — {self.PROJECT_NAME}",
        ):
            with self.subTest(quote=quote):
                payload = deepcopy(self._correct_payload())
                payload["overview"]["sources"]["project_name"] = self._source(quote)
                result = _fill_overview_spec_defaults(payload)["overview"]
                self.assertEqual(result["project_name"], self.PROJECT_NAME)

    def test_titleless_area_table_requires_strong_independent_building_evidence(self):
        strong = {
            "page_type": "building_area_table",
            "title_text": "",
            "evidence_terms": ["101동", "102동", "건축면적", "연면적", "표 행"],
        }
        weak = {
            "page_type": "building_area_table",
            "title_text": "",
            "evidence_terms": ["구분", "건축면적", "연면적", "면적"],
        }
        drawing_list = {
            "page_type": "building_area_table",
            "title_text": "",
            "evidence_terms": ["도면목록", "101동", "102동", "건축면적", "연면적"],
        }
        self.assertTrue(_classification_has_evidence(strong))
        self.assertFalse(_classification_has_evidence(weak))
        self.assertFalse(_classification_has_evidence(drawing_list))

    def test_single_building_titleless_area_table_needs_row_context_and_number(self):
        complete_row = {
            "page_type": "building_area_table",
            "title_text": "",
            "evidence_terms": [
                "101동", "건축면적", "연면적", "지상13층", "63세대", "599.79", "5,508.48",
            ],
        }
        missing_row_context = {
            "page_type": "building_area_table",
            "title_text": "",
            "evidence_terms": ["101동", "건축면적", "연면적"],
        }
        self.assertTrue(_classification_has_evidence(complete_row))
        self.assertFalse(_classification_has_evidence(missing_row_context))

    def test_existing_overview_title_evidence_still_passes(self):
        self.assertTrue(_classification_has_evidence({
            "page_type": "overview",
            "title_text": "사업개요",
            "evidence_terms": ["사업명칭", "대지위치", "규모"],
        }))

    def test_review_id_changes_by_user_or_file_hash(self):
        hashes_a = _review_file_hashes(structural_pdf_bytes=b"a")
        hashes_b = _review_file_hashes(structural_pdf_bytes=b"b")
        self.assertNotEqual(_canonical_review_id(1, hashes_a), _canonical_review_id(2, hashes_a))
        self.assertNotEqual(_canonical_review_id(1, hashes_a), _canonical_review_id(1, hashes_b))

    def test_incremental_classifier_selects_pages_7_and_8_and_stops(self):
        calls = []

        def classify_batch(_pdf, pages):
            calls.append(list(pages))
            return [
                {"page_number": page, "page_type": (
                    "overview" if page == 7 else
                    "building_area_table" if page == 8 else "other"
                ), "confidence": 0.99, "title_text": (
                    "사업개요" if page == 7 else "동별면적표" if page == 8 else ""
                ), "evidence_terms": (
                    ["사업명칭", "대지위치", "규모"] if page == 7 else
                    ["동", "건축면적", "연면적"] if page == 8 else []
                )}
                for page in pages
            ]

        found = _find_incremental_overview_pages(
            b"ojeong-pages-7-8", 40, classify_batch=classify_batch, page_texts={},
        )
        self.assertEqual(found["overview"]["page_number"], 7)
        self.assertEqual(found["area_table"]["page_number"], 8)
        self.assertEqual(calls, [list(range(1, 11))])
        self.assertEqual(found["scanned_pages"], list(range(1, 11)))
        self.assertEqual(found["vision_call_count"], 1)
        self.assertEqual(found["vision_pages"], [list(range(1, 11))])
        self.assertEqual(found["overview"]["selection_reason"], "image_fallback")
        self.assertEqual(found["area_table"]["selection_reason"], "image_fallback")
        repeated = _find_incremental_overview_pages(
            b"ojeong-pages-7-8", 40, classify_batch=classify_batch, page_texts={},
        )
        self.assertEqual(repeated["overview"]["page_number"], 7)
        self.assertEqual(
            calls, [list(range(1, 11)), list(range(1, 11))],
            "실제 핵심 개요값 검증 전 locator 결과를 캐시하면 실패",
        )

    def test_first_ten_pages_use_one_render_and_one_vision_request(self):
        fake_client = Mock()
        fake_client.models.generate_content.return_value = object()
        rendered = [[Image.new("RGB", (100, 50), "white")] for _ in range(10)]
        encoded_sizes = []

        def encode(image, max_size):
            encoded_sizes.append(image.size)
            return b"jpeg"

        with (
            patch("core.quantity_views.get_gemini_client", return_value=fake_client),
            patch("core.quantity_views._render_pdf_page_range", side_effect=rendered) as render,
            patch("core.quantity_views.image_to_jpeg_bytes", side_effect=encode),
            patch(
                "core.quantity_views.types.Part.from_bytes",
                side_effect=lambda **kwargs: ("IMAGE", kwargs["data"]),
            ),
            patch(
                "core.quantity_views._extract_text_from_gemini_response",
                return_value='{"pages":[]}',
            ),
        ):
            _classify_overview_page_batch(b"pdf", list(range(1, 11)))
        self.assertEqual(render.call_count, 10)
        self.assertEqual(
            render.call_args_list,
            [
                ((b"pdf", page, page), {"dpi": 120, "timeout": 60})
                for page in range(1, 11)
            ],
        )
        self.assertEqual(fake_client.models.generate_content.call_count, 1)
        contents = fake_client.models.generate_content.call_args.kwargs["contents"]
        for page in range(1, 11):
            label_index = contents.index(f"[PDF_PAGE={page}]")
            self.assertEqual(contents[label_index + 1], ("IMAGE", b"jpeg"))
        self.assertEqual(sum(item == ("IMAGE", b"jpeg") for item in contents), 10)
        self.assertEqual(len(encoded_sizes), 10)
        self.assertTrue(all(max(size) >= 2000 for size in encoded_sizes))
        config = fake_client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(config.max_output_tokens, 8192)
        self.assertEqual(config.thinking_config.thinking_budget, 512)

    def test_truncated_first_batch_retries_only_missing_pages_and_stops(self):
        calls = []

        def item(page):
            return {
                "page_number": page,
                "page_type": (
                    "overview" if page == 7 else
                    "building_area_table" if page == 8 else "other"
                ),
                "confidence": 0.99,
                "title_text": (
                    "사업개요" if page == 7 else
                    "동별면적표" if page == 8 else ""
                ),
                "evidence_terms": (
                    ["사업명칭", "대지위치", "규모"] if page == 7 else
                    ["101동", "102동", "건축면적", "연면적"] if page == 8 else []
                ),
            }

        def classify(_pdf, pages, _needed_types):
            calls.append(list(pages))
            if pages == list(range(1, 11)):
                return _OverviewClassificationResult(
                    [item(page) for page in range(1, 5)],
                    requested_pages=pages,
                    finish_reason="MAX_TOKENS",
                    missing_page_numbers=list(range(5, 11)),
                    partial_repair_used=True,
                )
            return [item(page) for page in pages]

        found = _find_incremental_overview_pages(
            b"truncated-pages-1-4", 30,
            classify_batch=classify, page_texts={},
        )
        self.assertTrue(found["complete"])
        self.assertEqual(found["overview"]["page_number"], 7)
        self.assertEqual(found["area_table"]["page_number"], 8)
        self.assertEqual(
            calls,
            [list(range(1, 11)), [5, 6, 7], [8, 9, 10]],
        )
        self.assertEqual(found["scanned_pages"], list(range(1, 11)))
        self.assertEqual(found["vision_call_count"], 3)
        self.assertEqual(found["vision_pages"][-2:], [[5, 6, 7], [8, 9, 10]])

    def test_complete_first_batch_does_not_retry_missing_pages(self):
        calls = []

        def classify(_pdf, pages, _needed_types):
            calls.append(list(pages))
            results = [
                {
                    "page_number": page,
                    "page_type": (
                        "overview" if page == 7 else
                        "building_area_table" if page == 8 else "other"
                    ),
                    "confidence": 0.99,
                    "title_text": (
                        "사업개요" if page == 7 else
                        "동별면적표" if page == 8 else ""
                    ),
                    "evidence_terms": (
                        ["사업명칭", "대지위치"] if page == 7 else
                        ["동", "건축면적", "연면적"] if page == 8 else []
                    ),
                }
                for page in pages
            ]
            return _OverviewClassificationResult(
                results, requested_pages=pages, missing_page_numbers=[],
                finish_reason="STOP",
            )

        found = _find_incremental_overview_pages(
            b"complete-first-batch", 30,
            classify_batch=classify, page_texts={},
        )
        self.assertTrue(found["complete"])
        self.assertEqual(calls, [list(range(1, 11))])

    def test_missing_retry_failure_remains_classifier_no_result(self):
        calls = []

        def classify(_pdf, pages, _needed_types):
            calls.append(list(pages))
            if len(calls) == 1:
                return _OverviewClassificationResult(
                    [{
                        "page_number": page, "page_type": "other",
                        "confidence": 0.9, "title_text": "", "evidence_terms": [],
                    } for page in range(1, 5)],
                    requested_pages=pages,
                    missing_page_numbers=list(range(5, 11)),
                    finish_reason="MAX_TOKENS",
                    partial_repair_used=True,
                )
            return []

        with self.assertLogs("core.quantity_views", level="INFO") as captured:
            found = _find_incremental_overview_pages(
                b"missing-retry-fails", 10,
                classify_batch=classify, page_texts={},
            )
        self.assertFalse(found["complete"])
        self.assertEqual(
            calls,
            [list(range(1, 11)), [5, 6, 7], [8, 9, 10]],
        )
        log_text = "\n".join(captured.output)
        self.assertIn("classifier_no_result", log_text)
        self.assertIn("remaining_missing_page_numbers", log_text)

    def test_locator_response_accepts_top_level_json_array(self):
        fake_client = Mock()
        fake_client.models.generate_content.return_value = object()
        with (
            patch("core.quantity_views.get_gemini_client", return_value=fake_client),
            patch(
                "core.quantity_views._render_pdf_page_range",
                return_value=[Image.new("RGB", (100, 50), "white")],
            ),
            patch(
                "core.quantity_views._extract_text_from_gemini_response",
                return_value=(
                    '[{"page_number":1,"page_type":"overview","confidence":0.9,'
                    '"title_text":"사업개요","evidence_terms":["사업명칭","대지위치"]}]'
                ),
            ),
        ):
            result = _classify_overview_page_batch(b"pdf-array", [1])
        self.assertEqual(result[0]["page_number"], 1)
        self.assertEqual(result[0]["page_type"], "overview")

    def test_locator_never_reads_pages_after_first_batch_when_vision_finds_both(self):
        calls = []

        def classify(_pdf, pages, _needed_types):
            calls.append(list(pages))
            return [
                {"page_number": 7, "page_type": "overview", "confidence": 0.99,
                 "title_text": "사업개요", "evidence_terms": ["사업명칭", "대지위치", "규모"]},
                {"page_number": 8, "page_type": "building_area_table", "confidence": 0.99,
                 "title_text": "동별면적표", "evidence_terms": ["동", "건축면적", "연면적"]},
            ]

        with patch("core.quantity_views._extract_pdf_page_texts", return_value={}) as text_extract:
            found = _find_incremental_overview_pages(
                b"stop-after-first-batch", 80, classify_batch=classify,
            )
        text_extract.assert_called_once_with(
            b"stop-after-first-batch", 1, 10, timeout_sec=20,
        )
        self.assertEqual(calls, [list(range(1, 11))])
        self.assertEqual(found["scanned_pages"], list(range(1, 11)))
        self.assertEqual(found["vision_call_count"], 1)

    def test_vision_timeout_stops_locator_with_clear_error(self):
        def timeout(*_args):
            raise TimeoutError("mock timeout")

        with self.assertRaisesRegex(OverviewLocatorTimeout, "Vision 분류"):
            _find_incremental_overview_pages(
                b"vision-timeout", 80, classify_batch=timeout, page_texts={},
            )

    def test_incremental_classifier_scans_only_next_range_when_one_missing(self):
        calls = []

        def classify_batch(_pdf, pages):
            calls.append(list(pages))
            return [
                {"page_number": page, "page_type": (
                    "overview" if page == 7 else
                    "building_area_table" if page == 12 else "other"
                ), "confidence": 0.9,
                 "title_text": (
                     "사업개요" if page == 7 else "동별면적표" if page == 12 else ""
                 ),
                 "evidence_terms": (
                     ["사업명칭", "대지위치"] if page == 7 else
                     ["동", "건축면적", "연면적"] if page == 12 else []
                 )}
                for page in pages
            ]

        found = _find_incremental_overview_pages(
            b"ojeong-area-on-12", 40, classify_batch=classify_batch, page_texts={},
        )
        self.assertTrue(found["complete"])
        self.assertEqual(calls, [list(range(1, 11)), list(range(11, 16))])

    def test_text_candidates_are_verified_by_same_batch_vision(self):
        calls = []

        def classify(_pdf, pages, needed_types):
            calls.append((list(pages), set(needed_types)))
            return [
                {
                    "page_number": page,
                    "page_type": (
                        "drawing_list" if page == 2 else
                        "overview" if page == 7 else
                        "building_area_table" if page == 8 else "other"
                    ),
                    "confidence": 0.99,
                    "title_text": (
                        "사업개요" if page == 7 else
                        "동별면적표" if page == 8 else "도면목록" if page == 2 else ""
                    ),
                    "evidence_terms": (
                        ["사업명칭", "대지위치", "규모"] if page == 7 else
                        ["동", "건축면적", "연면적"] if page == 8 else
                        ["도면번호", "도면명"] if page == 2 else []
                    ),
                }
                for page in pages
            ]

        found = _find_incremental_overview_pages(
            b"ojeong-real-text-layout", 20,
            classify_batch=classify,
            page_texts={
                2: "A-001 건축개요 사업명칭 주요구조 규모 도면목록 DRAWING LIST",
                7: "사업개요 사업명칭 주요구조 규모 대지위치",
                8: "동별자료 동별 면적 101동 102동",
            },
        )
        self.assertTrue(found["complete"])
        self.assertEqual(found["overview"]["page_number"], 7)
        self.assertEqual(found["area_table"]["page_number"], 8)
        self.assertEqual(found["scanned_pages"], list(range(1, 11)))
        self.assertEqual(
            calls,
            [(list(range(1, 11)), {"overview", "building_area_table"})],
        )
        self.assertEqual(found["vision_call_count"], 1)
        self.assertEqual(found["overview"]["selection_reason"], "image_fallback")
        self.assertEqual(found["area_table"]["selection_reason"], "image_fallback")

    def test_failed_locator_result_is_not_cached(self):
        first_calls = []

        def no_result(_pdf, pages):
            first_calls.append(list(pages))
            return []

        first = _find_incremental_overview_pages(
            b"locator-failure-not-cacheable", 10,
            classify_batch=no_result, page_texts={},
        )
        self.assertFalse(first["complete"])
        second_calls = []

        def success(_pdf, pages):
            second_calls.append(list(pages))
            return [
                {"page_number": 7, "page_type": "overview", "confidence": 0.9,
                 "title_text": "사업개요", "evidence_terms": ["사업명칭", "대지위치"]},
                {"page_number": 8, "page_type": "building_area_table", "confidence": 0.9,
                 "title_text": "동별자료", "evidence_terms": ["동", "건축면적", "연면적"]},
            ]

        second = _find_incremental_overview_pages(
            b"locator-failure-not-cacheable", 10,
            classify_batch=success, page_texts={},
        )
        self.assertTrue(second["complete"])
        self.assertTrue(first_calls)
        self.assertTrue(second_calls, "실패 결과가 캐시되어 재탐색이 생략되면 실패")

    def test_image_fallback_classifies_every_page_without_position_inference(self):
        calls = []

        def classify_missing(_pdf, pages, needed_types):
            calls.append((list(pages), set(needed_types)))
            return [
                {"page_number": 7, "page_type": "overview",
                 "confidence": 0.96, "title_text": "사업개요",
                 "evidence_terms": ["사업명칭", "대지위치", "규모"]},
                {"page_number": 8, "page_type": "building_area_table",
                 "confidence": 0.95, "title_text": "동별자료",
                 "evidence_terms": ["동", "건축면적", "연면적"]},
            ]

        found = _find_incremental_overview_pages(
            b"overview-from-text-area-from-image", 20,
            classify_batch=classify_missing,
            page_texts={7: "사업개요 사업명칭 주요구조 규모"},
        )
        self.assertTrue(found["complete"])
        self.assertEqual(found["overview"]["page_number"], 7)
        self.assertEqual(found["area_table"]["page_number"], 8)
        self.assertEqual(calls[0][0], list(range(1, 11)))
        self.assertEqual(calls[0][1], {"overview", "building_area_table"})

    def test_area_only_first_result_retries_same_range_for_overview(self):
        calls = []

        def classify(_pdf, pages, needed_types):
            calls.append((list(pages), set(needed_types)))
            if len(calls) == 1:
                return [{
                    "page_number": 8, "page_type": "building_area_table",
                    "confidence": 0.99, "title_text": "동별면적표",
                    "evidence_terms": ["동", "건축면적", "연면적"],
                }]
            return [{
                "page_number": 7, "page_type": "overview",
                "confidence": 0.98, "title_text": "사업개요",
                "evidence_terms": ["사업명칭", "대지위치", "규모"],
            }]

        found = _find_incremental_overview_pages(
            b"same-range-overview-focus", 50,
            classify_batch=classify, page_texts={},
        )
        self.assertTrue(found["complete"])
        self.assertEqual(found["overview"]["page_number"], 7)
        self.assertEqual(found["area_table"]["page_number"], 8)
        self.assertEqual(
            calls,
            [
                (list(range(1, 11)), {"overview", "building_area_table"}),
                (list(range(1, 11)), {"overview"}),
            ],
        )
        self.assertEqual(found["scanned_pages"], list(range(1, 11)))

    def test_overview_without_title_or_evidence_is_rejected(self):
        calls = []

        def classify(_pdf, pages, needed_types):
            calls.append((list(pages), set(needed_types)))
            return [{
                "page_number": 4, "page_type": "overview",
                "confidence": 1.0, "title_text": "A-001 도면목록",
                "evidence_terms": [],
            }]

        found = _find_incremental_overview_pages(
            b"unsupported-overview", 10,
            classify_batch=classify, page_texts={},
        )
        self.assertFalse(found["complete"])
        self.assertIsNone(found["overview"])

    def test_locator_cache_requires_validated_overview_values(self):
        detection = {
            "complete": True,
            "overview": {"page_number": 7},
            "area_table": {"page_number": 8},
        }
        self.assertFalse(
            _cache_validated_overview_pages(
                b"unvalidated-cache", detection,
                {"project_name": "사업", "basement_floor_count": None,
                 "aboveground_max_floor": 10, "buildings": [{"label": "101동"}]},
            )
        )
        self.assertTrue(
            _cache_validated_overview_pages(
                b"validated-cache", detection,
                {"project_name": "사업", "basement_floor_count": 2,
                 "aboveground_max_floor": 10, "buildings": [{"label": "101동"}]},
            )
        )

    def test_business_name_evidence_does_not_require_colon(self):
        payload = self._correct_payload()
        payload["overview"]["sources"]["project_name"] = [
            self._source(f"사업명칭 {self.PROJECT_NAME}"),
        ]
        result = _fill_overview_spec_defaults(payload)["overview"]
        self.assertEqual(result["project_name"], self.PROJECT_NAME)

    def test_classifier_accepts_drawing_list_as_distinct_content_type(self):
        from .quantity_views import _normalize_page_classification

        result = _normalize_page_classification(
            {"page_number": 2, "page_type": "drawing_list", "confidence": 0.97},
            {1, 2, 3},
        )
        self.assertEqual(result["page_number"], 2)
        self.assertEqual(result["page_type"], "drawing_list")
        self.assertEqual(result["confidence"], 0.97)

    def test_drawing_list_sources_cannot_supply_overview_values(self):
        payload = self._correct_payload()
        drawing_list = self._source(
            "A-001 도면목록 사업명칭: 가짜 사업 / 101동 / 지하 2층 / 지상 13층 / 대지면적 999㎡",
            page=2, table="도면목록",
        )
        payload["overview"]["sources"].update({
            "project_name": [drawing_list],
            "basement_floor_count": drawing_list,
            "aboveground_max_floor": drawing_list,
            "buildings": drawing_list,
            "site_area_m2": drawing_list,
        })
        for building in payload["overview"]["buildings"]:
            building["source"] = drawing_list
        result = _fill_overview_spec_defaults(payload)["overview"]
        self.assertIsNone(result["project_name"])
        self.assertIsNone(result["basement_floor_count"])
        self.assertIsNone(result["aboveground_max_floor"])
        self.assertEqual(result["buildings"], [])
        self.assertIsNone(result["site_area_m2"])


class AutoPostCategoryRegressionTests(TestCase):
    RAY_TITLE = "Ray를 TPU에서 실행하는 방법: 기본 원리부터 핵심까지"
    TUNIX_TITLE = "Tunix로 에이전트 강화 학습 확장: 고성능 훈련의 효율을 높이는 방법"

    def test_ray_and_tunix_legacy_tech_resolve_to_ai_development(self):
        for title in (self.RAY_TITLE, self.TUNIX_TITLE):
            category, diagnostics = cbl_resolve_auto_post_category(
                "테크", title=title,
            )
            self.assertEqual(category, "tech_ai_development")
            self.assertTrue(diagnostics["legacy_mapping_used"])

    def test_canonical_ai_development_is_preserved(self):
        category, diagnostics = cbl_resolve_auto_post_category(
            "tech_ai_development", title=self.RAY_TITLE,
        )
        self.assertEqual(category, "tech_ai_development")
        self.assertFalse(diagnostics["legacy_mapping_used"])

    def test_other_public_categories_are_preserved(self):
        for category, _label in CBL_PUBLIC_CATEGORY_CHOICES:
            resolved, _diagnostics = cbl_resolve_auto_post_category(category)
            self.assertEqual(resolved, category)

    def test_unsupported_category_without_evidence_is_not_first_choice_fallback(self):
        resolved, diagnostics = cbl_resolve_auto_post_category(
            "기술 일반", title="분류 근거가 없는 일반 제목",
        )
        self.assertIsNone(resolved)
        self.assertEqual(
            diagnostics["fallback_reason"],
            "unsupported_category_without_text_evidence",
        )

    def test_admin_form_and_auto_generator_share_public_choices(self):
        form_choices = [
            (value, label)
            for value, label in PostForm().fields["category"].choices
            if value
        ]
        self.assertEqual(form_choices, list(CBL_PUBLIC_CATEGORY_CHOICES))
        self.assertEqual(
            list(AUTO_NAVER_CATEGORY_ORDER),
            list(CBL_PUBLIC_CATEGORY_CHOICES),
        )

    def test_request_wrappers_do_not_collapse_new_category_to_legacy_tech(self):
        self.assertEqual(
            core_views._cbl_row_normalize_category(
                "tech_ai_development", self.RAY_TITLE,
            ),
            "tech_ai_development",
        )
        self.assertEqual(
            core_views._cbl_construction_norm_category(
                "tech", self.TUNIX_TITLE,
            ),
            "tech_ai_development",
        )

    def test_ai_raw_tech_is_canonicalized_before_post_create(self):
        queue_item = SimpleNamespace(
            id=1,
            category="tech_ai_development",
            keyword=self.RAY_TITLE,
        )
        setting = SimpleNamespace(make_thumbnail=False, include_tags=True)
        post = save_ai_data_to_post(
            {
                "category": "TECH",
                "title": self.RAY_TITLE,
                "content": "<p>Ray TPU AI 모델 실행 프레임워크</p>",
                "tags": "AI,Ray,TPU",
            },
            queue_item,
            setting,
        )
        self.assertEqual(post.category, "tech_ai_development")
        self.assertEqual(post.get_category_display(), "AI·개발")

    def test_targeted_fix_is_draft_only_exact_and_idempotent(self):
        ray = Post.objects.create(
            title=self.RAY_TITLE, category="tech",
            content="<p>draft</p>", is_published=False,
        )
        tunix = Post.objects.create(
            title=self.TUNIX_TITLE, category="tech",
            content="<p>draft</p>", is_published=False,
        )
        published = Post.objects.create(
            title=self.RAY_TITLE, category="tech",
            content="<p>published</p>", is_published=True,
        )
        other = Post.objects.create(
            title="다른 테크 글", category="tech",
            content="<p>other</p>", is_published=False,
        )

        first_output = io.StringIO()
        call_command("fix_auto_post_tech_categories", stdout=first_output)
        second_output = io.StringIO()
        call_command("fix_auto_post_tech_categories", stdout=second_output)

        ray.refresh_from_db()
        tunix.refresh_from_db()
        published.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(ray.category, "tech_ai_development")
        self.assertEqual(tunix.category, "tech_ai_development")
        self.assertEqual(published.category, "tech")
        self.assertEqual(other.category, "tech")
        self.assertIn("updated=2", first_output.getvalue())
        self.assertIn("updated=0", second_output.getvalue())


class CalendarWorkflowRegressionTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="calendar-admin",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(self.staff)

    def test_calendar_event_validation_rejects_reverse_date_range(self):
        kwargs, error = core_views._cbl_calendar_build_event_kwargs({
            "title": "검토 일정",
            "event_date": "2026-08-02",
            "end_date": "2026-08-01",
        })
        self.assertIsNone(kwargs)
        self.assertIn("종료일", error)

    def test_calendar_bulk_create_saves_only_valid_selected_items(self):
        response = self.client.post(
            "/api/calendar-events/ai-bulk-create/",
            {
                "items": json.dumps([
                    {
                        "title": "BIM 검토",
                        "event_date": "2026-08-03",
                        "end_date": "2026-08-04",
                        "category": "업무",
                        "is_all_day": True,
                    },
                    {
                        "title": "날짜 없는 일정",
                        "event_date": "",
                    },
                ], ensure_ascii=False),
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["created"]), 1)
        self.assertEqual(len(payload["errors"]), 1)
        event = CalendarEvent.objects.get()
        self.assertEqual(event.title, "BIM 검토")
        self.assertEqual(event.category, "업무")
        self.assertTrue(event.is_all_day)
