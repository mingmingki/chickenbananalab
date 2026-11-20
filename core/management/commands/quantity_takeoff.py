"""Free CAD quantity takeoff of a drawing folder, without the web page and without AI.

    python manage.py quantity_takeoff <folder or files ...> --out 수량산출.xlsx

Takes the structural and architectural CAD of one project (DWG, DXF or ZIP; folders are read
recursively) and runs exactly what the page's free CAD path runs: the floor job (DWG → DXF with
the free converter, plans by floor, the schedules and the window and door schedule), then the
floor takeoff, the rebar and the architectural takeoff, and writes the same Excel the page
downloads (요약, 층별 집계, 층별 철근, 건축 집계, 창호 집계, 임의로 정한 것, 검토 사항).

Where the drawings say nothing the takeoff goes on with an assumed value and lists it in
'임의로 정한 것'. ``--set key=value`` changes one (the keys of quantity_assumed.EDITABLE, e.g.
slab_bars=HD13@200, plain_wall_bars=D10@250, foundation_bars=HD19@300, slab_thickness=210) and
``--foundation 101동=1.5`` gives a building's mat thickness in metres.
"""
import json
import os
import shutil
import tempfile
import time

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory, override_settings

CAD_EXTENSIONS = (".dwg", ".dxf", ".zip")


def collect(paths):
    """The CAD files of the given files and folders (folders recursively, macOS ._ files left out)."""
    found = []
    for path in paths:
        if os.path.isdir(path):
            for root, dirs, names in os.walk(path):
                dirs[:] = sorted(d for d in dirs if not d.startswith((".", "__MACOSX")))
                for name in sorted(names):
                    if name.lower().endswith(CAD_EXTENSIONS) and not name.startswith("._"):
                        found.append(os.path.join(root, name))
        elif os.path.isfile(path) and path.lower().endswith(CAD_EXTENSIONS):
            found.append(path)
        elif not os.path.exists(path):
            raise CommandError(f"없는 경로예요: {path}")
    return found


def parse_settings(pairs):
    """{key: value} from "key=value" texts, checked like the page's edit boxes (clean_overrides)."""
    from core.quantity_assumed import EDITABLE, clean_overrides
    raw = {}
    for pair in pairs or []:
        key, _sep, value = pair.partition("=")
        key, value = key.strip(), value.strip()
        spec = EDITABLE.get(key)
        if not spec:
            raise CommandError(f"바꿀 수 없는 값이에요: {key} (가능: {', '.join(EDITABLE)})")
        if spec["kind"] == "toggle":
            raw[key] = value.lower() in ("1", "true", "yes", "on", "예", "넣기")
        elif spec["kind"] == "number":
            raw[key] = value
        else:
            raw[key] = value
    clean = clean_overrides(raw)
    rejected = sorted(set(raw) - set(clean))
    if rejected:
        raise CommandError("값의 형식이나 범위가 맞지 않아요: " + ", ".join(f"{k}={raw[k]}" for k in rejected))
    return clean


def parse_foundations(pairs):
    out = {}
    for pair in pairs or []:
        name, _sep, value = pair.partition("=")
        try:
            number = float(value)
        except ValueError:
            raise CommandError(f"기초 두께는 '동=m' 형식이에요: {pair}")
        if not 0.1 <= number <= 5.0:
            raise CommandError(f"기초 두께는 0.1~5.0m 사이로 넣어 주세요: {pair}")
        out[name.strip()] = number
    return out


def takeoff(files, overrides=None, foundations=None, workdir=None, log=None):
    """{"floor_views", "links", "openings", "takeoff", "rebar", "arch"} of a project's CAD files."""
    from core import quantity_arch_takeoff, quantity_floor_rebar, quantity_floor_takeoff
    from core import quantity_views as qv
    say = log or (lambda text: None)
    uploads = []
    for path in files:
        with open(path, "rb") as handle:
            uploads.append(SimpleUploadedFile(os.path.basename(path), handle.read()))
    inputs = qv._floor_view_inputs(uploads)
    uploads = None
    if not inputs:
        raise CommandError("평면도·일람표로 쓸 DWG/DXF를 찾지 못했어요.")
    with override_settings(QUANTITY_STATE_DB=os.path.join(workdir, "state.sqlite3")):
        job_id = f"cli-{int(time.time() * 1000)}"
        qv._quantity_state().begin_job(job_id, 0, "api_quantity_floor_views", "cli")
        say(f"도면 {len(inputs)}개를 읽는 중이에요 (평면도 {sum(1 for n, _d in inputs if _is_plan(n))}개)")
        qv._run_floor_views_job(job_id, inputs)
        result = qv._result_get(job_id) or {}
        if not result.get("ok"):
            raise CommandError("평면도를 읽지 못했어요: " + str(result.get("error") or "알 수 없는 오류"))
        floor_views = result["results"]["floor_views"]
        links, openings = qv._floor_links(job_id), qv._floor_openings(job_id)
    model = floor_views.get("model") or {}
    if not model.get("buildings"):
        raise CommandError("구조 평면도에서 층을 찾지 못했어요. 구조 CAD(평면도)가 들어 있는지 확인해 주세요.")
    overrides = overrides or {}
    floor = quantity_floor_takeoff.build_takeoff(model, links, edits=[], foundations=foundations or {}, learned={},
                                                 overrides=overrides)
    rebar = quantity_floor_rebar.build_rebar(model, links, floor, general_spec=None, edits=[], extras=None,
                                             overrides=overrides, learned={})
    arch = quantity_arch_takeoff.build(floor_views.get("views") or [], model, overrides=overrides, learned={},
                                       schedule=openings)
    return {"floor_views": floor_views, "links": links, "openings": openings, "takeoff": floor, "rebar": rebar, "arch": arch}


def _is_plan(name):
    from core import quantity_floor_views as fv
    return fv.is_plan_file(name)


def excel_bytes(result):
    """The Excel the page downloads for the free CAD path."""
    from core import quantity_views as qv
    body = json.dumps({"results": {"floor_takeoff": result["takeoff"], "floor_rebar": result["rebar"],
                                   "floor_arch": result["arch"]}}, ensure_ascii=False, default=str)
    request = RequestFactory().post("/api/quantity/download-excel/", data=body, content_type="application/json")
    response = qv._build_excel_response(request)
    if response.status_code != 200:
        raise CommandError("엑셀을 만들지 못했어요: " + response.content.decode("utf-8", errors="replace")[:300])
    return response.content


def summary_lines(result):
    lines = []
    rebar = {b["name"]: b for b in result["rebar"].get("buildings") or []}
    arch = {b["name"]: b for b in (result.get("arch") or {}).get("buildings") or []}
    for building in result["takeoff"].get("buildings") or []:
        totals = building.get("totals") or {}
        rough = building.get("approximate_totals") or {}
        concrete = (totals.get("concrete") or {}).get("total", 0)
        formwork = (totals.get("formwork") or {}).get("total", 0)
        kg = ((rebar.get(building["name"]) or {}).get("totals") or {}).get("total", 0)
        line = f"{building['name']}: 콘크리트 {concrete:,.0f}㎥ · 거푸집 {formwork:,.0f}㎡ · 철근 {kg / 1000:,.1f}t"
        rough_concrete = (rough.get("concrete") or {}).get("total", 0)
        if rough_concrete:
            line += f" (근사로 합계에서 뺀 콘크리트 {rough_concrete:,.0f}㎥)"
        walls = (arch.get(building["name"]) or {}).get("totals") or {}
        if walls:
            line += (f" · 조적 {walls.get('masonry', 0):,.0f}㎡ · ALC {walls.get('alc', 0):,.0f}㎡ · 건식벽 {walls.get('dry', 0):,.0f}㎡"
                     f" · 창 {walls.get('windows', 0)} · 문 {walls.get('doors', 0)}")
        lines.append(line)
    assumed = [a for part in ("takeoff", "rebar", "arch") for a in (result.get(part) or {}).get("assumed") or []]
    if assumed:
        lines.append(f"도면에 없어 정한 값 {len(assumed)}가지 (엑셀 '임의로 정한 것' 시트):")
        for item in assumed:
            lines.append(f"  - {item['label']}: {item['value']}")
    return lines


class Command(BaseCommand):
    help = "도면 폴더(DWG/DXF/ZIP)만으로 무료 CAD 수량산출을 하고 화면과 같은 엑셀을 만들어요. AI는 쓰지 않아요."

    def add_arguments(self, parser):
        parser.add_argument("paths", nargs="+", help="구조·건축 CAD 폴더나 파일 (DWG, DXF, ZIP)")
        parser.add_argument("--out", default="수량산출.xlsx", help="만들 엑셀 파일 (기본: 수량산출.xlsx)")
        parser.add_argument("--json", dest="json_out", help="수량 결과를 JSON으로도 저장")
        parser.add_argument("--set", dest="settings", action="append", metavar="KEY=VALUE",
                            help="임의로 정한 값을 바꿔요 (예: slab_bars=HD13@200, plain_wall_bars=D10@250)")
        parser.add_argument("--foundation", action="append", metavar="동=m", help="기초 두께 (예: 101동=1.5)")
        parser.add_argument("--keep", help="작업 폴더(변환한 도면, 작업 기록)를 지우지 않고 이 경로에 남겨요")

    def handle(self, *args, **options):
        files = collect(options["paths"])
        if not files:
            raise CommandError("DWG/DXF/ZIP 파일이 없어요.")
        overrides = parse_settings(options.get("settings"))
        foundations = parse_foundations(options.get("foundation"))
        workdir = options.get("keep") or tempfile.mkdtemp(prefix="cbl_qty_cli_")
        os.makedirs(workdir, exist_ok=True)
        started = time.time()
        try:
            result = takeoff(files, overrides, foundations, workdir, log=lambda text: self.stdout.write(text))
            with open(options["out"], "wb") as handle:
                handle.write(excel_bytes(result))
            if options.get("json_out"):
                with open(options["json_out"], "w", encoding="utf-8") as handle:
                    json.dump({k: v for k, v in result.items() if k != "floor_views"}, handle, ensure_ascii=False, default=str)
        finally:
            if not options.get("keep"):
                shutil.rmtree(workdir, ignore_errors=True)
        for line in summary_lines(result):
            self.stdout.write(line)
        self.stdout.write(self.style.SUCCESS(f"{options['out']} 를 만들었어요 ({time.time() - started:.0f}초)."))
