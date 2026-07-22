from pathlib import Path

from django.contrib.auth.decorators import user_passes_test
from django.db.models import Max
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .models import HomeProgramDownload


ALLOWED_EXTENSIONS = {
    ".zip",
    ".dmg",
    ".pkg",
    ".exe",
    ".msi",
    ".pdf",
}

MAX_FILE_SIZE = 500 * 1024 * 1024


def _is_staff(user):
    return bool(user.is_authenticated and user.is_staff)


def _file_payload(file_field):
    if not file_field:
        return {"ready": False, "url": "", "filename": ""}

    try:
        url = file_field.url
    except ValueError:
        url = ""

    return {
        "ready": bool(url),
        "url": url,
        "filename": Path(file_field.name).name,
    }


def _program_payload(program, *, is_staff=False):
    file_info = _file_payload(program.file)

    return {
        "id": program.id,
        "title": program.title,
        "badge": program.badge or "APP",
        "description": program.description,
        "is_public": bool(program.is_public),
        "order": program.order,
        "file": file_info,
        "downloadable": bool(file_info["ready"] and (program.is_public or is_staff)),
    }


@require_GET
def home_program_status(request):
    is_staff = _is_staff(request.user)

    programs = HomeProgramDownload.objects.all()
    if not is_staff:
        programs = programs.filter(is_public=True, file__isnull=False).exclude(file="")

    return JsonResponse({
        "ok": True,
        "is_staff": is_staff,
        "programs": [
            _program_payload(program, is_staff=is_staff)
            for program in programs
        ],
    })


@require_POST
@user_passes_test(_is_staff)
def home_program_upload(request):
    uploaded_file = request.FILES.get("file")
    title = (request.POST.get("title") or "").strip()
    badge = (request.POST.get("badge") or "").strip()[:20]
    description = (request.POST.get("description") or "").strip()

    if not title:
        return JsonResponse({"ok": False, "message": "프로그램명을 입력해 주세요."}, status=400)

    if not uploaded_file:
        return JsonResponse({"ok": False, "message": "업로드할 파일을 선택해 주세요."}, status=400)

    extension = Path(uploaded_file.name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        return JsonResponse({"ok": False, "message": f"지원하지 않는 파일 형식입니다. 지원 형식: {allowed}"}, status=400)

    if uploaded_file.size > MAX_FILE_SIZE:
        return JsonResponse({"ok": False, "message": "파일 크기는 최대 500MB까지 업로드할 수 있습니다."}, status=400)

    max_order = HomeProgramDownload.objects.aggregate(value=Max("order")).get("value") or 0

    program = HomeProgramDownload.objects.create(
        title=title,
        badge=badge or title[:3].upper(),
        description=description,
        file=uploaded_file,
        is_public=False,
        order=max_order + 10,
    )

    return JsonResponse({
        "ok": True,
        "message": "업로드했습니다. 아직 비공개 상태입니다.",
        "program": _program_payload(program, is_staff=True),
    })


@require_POST
@user_passes_test(_is_staff)
def home_program_toggle_public(request, pk):
    program = HomeProgramDownload.objects.filter(pk=pk).first()
    if not program:
        return JsonResponse({"ok": False, "message": "프로그램을 찾을 수 없습니다."}, status=404)

    if not program.file:
        program.is_public = False
        program.save(update_fields=["is_public", "updated_at"])
        return JsonResponse({"ok": False, "message": "파일이 없어서 공개할 수 없습니다."}, status=400)

    program.is_public = not program.is_public
    program.save(update_fields=["is_public", "updated_at"])

    return JsonResponse({
        "ok": True,
        "message": "공개 처리했습니다." if program.is_public else "숨김 처리했습니다.",
        "program": _program_payload(program, is_staff=True),
    })


@require_POST
@user_passes_test(_is_staff)
def home_program_delete(request, pk):
    program = HomeProgramDownload.objects.filter(pk=pk).first()
    if not program:
        return JsonResponse({"ok": False, "message": "프로그램을 찾을 수 없습니다."}, status=404)

    if program.file:
        program.file.delete(save=False)

    program.delete()

    return JsonResponse({"ok": True, "message": "삭제했습니다."})
