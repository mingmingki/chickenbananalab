from pathlib import Path

from django.contrib.auth.decorators import user_passes_test
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .models import ProgramDownload


PROGRAM_DEFINITIONS = [
    {
        "slug": "chickenbananalab-cut",
        "name": "ChickenBananaLabCut",
        "description": "영상 편집 프로그램",
        "order": 10,
    },
    {
        "slug": "chickenbananalab-pdf",
        "name": "ChickenBananaLabPDF",
        "description": "PDF 뷰어 프로그램",
        "order": 20,
    },
    {
        "slug": "chickenbananalab-ss",
        "name": "ChickenBananaLabSS",
        "description": "화면 캡처·녹화 프로그램",
        "order": 30,
    },
    {
        "slug": "chickenbananalab-zip",
        "name": "ChickenBananaLabZIP",
        "description": "압축파일 생성·해제 프로그램",
        "order": 40,
    },
    {
        "slug": "chickenbananalab-viewer",
        "name": "ChickenBananaLabViewer",
        "description": "AutoCAD 파일 뷰어",
        "order": 50,
    },
]

ALLOWED_EXTENSIONS = {
    "mac": {".dmg", ".pkg", ".zip"},
    "windows": {".exe", ".msi", ".zip"},
}

MAX_FILE_SIZE = 500 * 1024 * 1024


def _is_staff(user):
    return bool(user.is_authenticated and user.is_staff)


def _ensure_programs():
    programs = []

    for definition in PROGRAM_DEFINITIONS:
        program, _ = ProgramDownload.objects.get_or_create(
            slug=definition["slug"],
            defaults={
                "name": definition["name"],
                "description": definition["description"],
                "order": definition["order"],
            },
        )

        changed = False

        for field in ("name", "description", "order"):
            value = definition[field]

            if getattr(program, field) != value:
                setattr(program, field, value)
                changed = True

        if changed:
            program.save(update_fields=["name", "description", "order", "updated_at"])

        programs.append(program)

    return programs


def _file_payload(file_field):
    if not file_field:
        return {
            "ready": False,
            "url": "",
            "filename": "",
        }

    try:
        url = file_field.url
    except ValueError:
        url = ""

    return {
        "ready": bool(url),
        "url": url,
        "filename": Path(file_field.name).name,
    }


@require_GET
def program_download_status(request):
    programs = _ensure_programs()

    return JsonResponse({
        "ok": True,
        "is_staff": _is_staff(request.user),
        "programs": [
            {
                "slug": program.slug,
                "name": program.name,
                "description": program.description,
                "mac": _file_payload(program.mac_file),
                "windows": _file_payload(program.windows_file),
            }
            for program in programs
        ],
    })


@require_POST
@user_passes_test(_is_staff)
def program_download_upload(request, slug, platform):
    if platform not in ALLOWED_EXTENSIONS:
        return JsonResponse({
            "ok": False,
            "message": "지원하지 않는 운영체제입니다.",
        }, status=400)

    program = ProgramDownload.objects.filter(slug=slug).first()

    if not program:
        _ensure_programs()
        program = ProgramDownload.objects.filter(slug=slug).first()

    if not program:
        return JsonResponse({
            "ok": False,
            "message": "프로그램 정보를 찾을 수 없습니다.",
        }, status=404)

    uploaded_file = request.FILES.get("file")

    if not uploaded_file:
        return JsonResponse({
            "ok": False,
            "message": "업로드할 파일을 선택해 주세요.",
        }, status=400)

    extension = Path(uploaded_file.name).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS[platform]:
        allowed_text = ", ".join(sorted(ALLOWED_EXTENSIONS[platform]))

        return JsonResponse({
            "ok": False,
            "message": f"허용되지 않는 파일입니다. 지원 형식: {allowed_text}",
        }, status=400)

    if uploaded_file.size > MAX_FILE_SIZE:
        return JsonResponse({
            "ok": False,
            "message": "파일 크기는 최대 500MB까지 업로드할 수 있습니다.",
        }, status=400)

    field_name = "mac_file" if platform == "mac" else "windows_file"
    old_file = getattr(program, field_name)

    if old_file:
        old_file.delete(save=False)

    setattr(program, field_name, uploaded_file)
    program.save(update_fields=[field_name, "updated_at"])

    saved_file = getattr(program, field_name)

    return JsonResponse({
        "ok": True,
        "message": f"{program.name} {platform}용 파일을 업로드했습니다.",
        "file": _file_payload(saved_file),
    })


@require_POST
@user_passes_test(_is_staff)
def program_download_delete(request, slug, platform):
    if platform not in ALLOWED_EXTENSIONS:
        return JsonResponse({
            "ok": False,
            "message": "지원하지 않는 운영체제입니다.",
        }, status=400)

    program = ProgramDownload.objects.filter(slug=slug).first()

    if not program:
        return JsonResponse({
            "ok": False,
            "message": "프로그램 정보를 찾을 수 없습니다.",
        }, status=404)

    field_name = "mac_file" if platform == "mac" else "windows_file"
    target_file = getattr(program, field_name)

    if target_file:
        target_file.delete(save=False)
        setattr(program, field_name, None)
        program.save(update_fields=[field_name, "updated_at"])

    return JsonResponse({
        "ok": True,
        "message": f"{program.name} {platform}용 파일을 삭제했습니다.",
    })
