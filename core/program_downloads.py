from pathlib import Path

from django.contrib.auth.decorators import user_passes_test
from django.db.models import F
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.urls import reverse
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
    return bool(user.is_authenticated and (user.is_staff or user.is_superuser))


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


def _file_payload(file_field, is_public=False, slug="", platform="", download_count=0):
    if not file_field:
        return {
            "has_file": False,
            "ready": False,
            "is_public": False,
            "can_download": False,
            "url": "",
            "download_url": "",
            "filename": "",
            "download_count": 0,
        }

    try:
        url = file_field.url
    except ValueError:
        url = ""

    has_file = bool(url)
    is_public = bool(is_public and has_file)
    can_download = bool(has_file and is_public)

    download_url = ""
    if can_download and slug and platform:
        download_url = reverse(
            "program_download_file",
            kwargs={"slug": slug, "platform": platform},
        )

    return {
        "has_file": has_file,
        "ready": has_file,
        "is_public": is_public,
        "can_download": can_download,
        "url": url,
        "download_url": download_url or url,
        "filename": Path(file_field.name).name,
        "download_count": int(download_count or 0),
    }


def _public_attr(platform):
    return "mac_is_public" if platform == "mac" else "windows_is_public"


def _file_attr(platform):
    return "mac_file" if platform == "mac" else "windows_file"


def _count_attr(platform):
    return "mac_download_count" if platform == "mac" else "windows_download_count"


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
                "mac": _file_payload(
                    program.mac_file,
                    getattr(program, "mac_is_public", False),
                    slug=program.slug,
                    platform="mac",
                    download_count=getattr(program, "mac_download_count", 0),
                ),
                "windows": _file_payload(
                    program.windows_file,
                    getattr(program, "windows_is_public", False),
                    slug=program.slug,
                    platform="windows",
                    download_count=getattr(program, "windows_download_count", 0),
                ),
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

    field_name = _file_attr(platform)
    public_field_name = _public_attr(platform)
    old_file = getattr(program, field_name)

    if old_file:
        old_file.delete(save=False)

    setattr(program, field_name, uploaded_file)

    update_fields = [field_name, "updated_at"]
    if hasattr(program, public_field_name):
        # 업로드 직후에는 자동 공개하지 않습니다. 관리자가 별도로 '공개'를 눌러야 합니다.
        setattr(program, public_field_name, False)
        update_fields.insert(1, public_field_name)

    program.save(update_fields=update_fields)

    saved_file = getattr(program, field_name)
    is_public = getattr(program, public_field_name, False)

    return JsonResponse({
        "ok": True,
        "message": f"{program.name} {platform}용 파일을 업로드했습니다. 공개하려면 '공개' 버튼을 눌러 주세요.",
        "file": _file_payload(saved_file, is_public),
    })


@require_POST
@user_passes_test(_is_staff)
def program_download_publish(request, slug, platform):
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

    field_name = _file_attr(platform)
    public_field_name = _public_attr(platform)
    target_file = getattr(program, field_name)

    if not target_file:
        return JsonResponse({
            "ok": False,
            "message": "파일이 없어서 공개할 수 없습니다.",
        }, status=400)

    action = request.POST.get("action", "public")
    is_public = action == "public"

    if not hasattr(program, public_field_name):
        return JsonResponse({
            "ok": False,
            "message": "공개/비공개 필드가 아직 적용되지 않았습니다. makemigrations와 migrate를 실행해 주세요.",
        }, status=400)

    setattr(program, public_field_name, is_public)
    program.save(update_fields=[public_field_name, "updated_at"])

    return JsonResponse({
        "ok": True,
        "message": f"{program.name} {platform}용 파일을 {'공개' if is_public else '비공개'} 처리했습니다.",
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

    field_name = _file_attr(platform)
    public_field_name = _public_attr(platform)
    target_file = getattr(program, field_name)

    if target_file:
        target_file.delete(save=False)
        setattr(program, field_name, None)

    update_fields = [field_name, "updated_at"]
    if hasattr(program, public_field_name):
        setattr(program, public_field_name, False)
        update_fields.insert(1, public_field_name)

    program.save(update_fields=update_fields)

    return JsonResponse({
        "ok": True,
        "message": f"{program.name} {platform}용 파일을 삭제했습니다.",
    })


@require_GET
def program_download_file(request, slug, platform):
    if platform not in ALLOWED_EXTENSIONS:
        raise Http404("지원하지 않는 운영체제입니다.")

    program = ProgramDownload.objects.filter(slug=slug).first()

    if not program:
        raise Http404("프로그램 정보를 찾을 수 없습니다.")

    field_name = _file_attr(platform)
    public_field_name = _public_attr(platform)
    count_field_name = _count_attr(platform)

    target_file = getattr(program, field_name)
    is_public = getattr(program, public_field_name, False)

    if not target_file or not is_public:
        raise Http404("다운로드할 수 없는 파일입니다.")

    try:
        file_url = target_file.url
    except ValueError:
        raise Http404("다운로드할 수 없는 파일입니다.")

    # 실제 파일은 그대로 media/정적 서빙 경로로 리다이렉트하고,
    # 카운트만 원자적으로(F 표현식) 증가시켜 동시 다운로드에도 정확히 집계한다.
    ProgramDownload.objects.filter(pk=program.pk).update(
        **{count_field_name: F(count_field_name) + 1}
    )

    return HttpResponseRedirect(file_url)
