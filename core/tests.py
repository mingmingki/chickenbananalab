import io
import json
import struct
import unicodedata
import zipfile
import zlib
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
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
    _merge_uploaded_cad_sets,
    _OverviewClassificationResult,
    _OVERVIEW_CLASSIFICATION_CACHE,
    _OVERVIEW_CLASSIFICATION_LOCK,
    OverviewLocatorTimeout,
    _parse_explicit_floor_count,
    _review_file_hashes,
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
