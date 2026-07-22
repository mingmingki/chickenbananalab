from datetime import datetime, time, timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.utils import timezone

from .models import GeminiUsageLog


def _number(value):
    return int(value or 0)


def _format_number(value):
    return f"{_number(value):,}"


def _summary(queryset):
    data = queryset.aggregate(
        calls=Count("id"),
        prompt=Sum("prompt_tokens"),
        output=Sum("output_tokens"),
        total=Sum("total_tokens"),
        cached=Sum("cached_tokens"),
        thoughts=Sum("thoughts_tokens"),
        images=Sum("image_inputs"),
        failures=Count("id", filter=Q(is_success=False)),
    )
    data = {key: _number(value) for key, value in data.items()}
    data["successes"] = max(0, data["calls"] - data["failures"])
    data["calls_fmt"] = _format_number(data["calls"])
    data["prompt_fmt"] = _format_number(data["prompt"])
    data["output_fmt"] = _format_number(data["output"])
    data["total_fmt"] = _format_number(data["total"])
    data["cached_fmt"] = _format_number(data["cached"])
    data["thoughts_fmt"] = _format_number(data["thoughts"])
    data["images_fmt"] = _format_number(data["images"])
    return data


@staff_member_required(login_url="login")
def gemini_usage_dashboard(request):
    now = timezone.localtime()
    tz = timezone.get_current_timezone()
    today_start = timezone.make_aware(datetime.combine(now.date(), time.min), tz)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    try:
        days = int(request.GET.get("days", "30"))
    except (TypeError, ValueError):
        days = 30
    if days not in {7, 30, 90}:
        days = 30

    feature = str(request.GET.get("feature", "") or "").strip()
    model_name = str(request.GET.get("model", "") or "").strip()
    status = str(request.GET.get("status", "") or "").strip()

    period_start = today_start - timedelta(days=days - 1)
    period_qs = GeminiUsageLog.objects.filter(created_at__gte=period_start)
    filtered_qs = period_qs

    if feature:
        filtered_qs = filtered_qs.filter(feature=feature)
    if model_name:
        filtered_qs = filtered_qs.filter(model=model_name)
    if status == "success":
        filtered_qs = filtered_qs.filter(is_success=True)
    elif status == "failed":
        filtered_qs = filtered_qs.filter(is_success=False)

    today_summary = _summary(GeminiUsageLog.objects.filter(created_at__gte=today_start))
    month_summary = _summary(GeminiUsageLog.objects.filter(created_at__gte=month_start))
    filtered_summary = _summary(filtered_qs)

    feature_rows = list(
        filtered_qs.values("feature")
        .annotate(
            calls=Count("id"),
            total=Sum("total_tokens"),
            prompt=Sum("prompt_tokens"),
            output=Sum("output_tokens"),
            failures=Count("id", filter=Q(is_success=False)),
            images=Sum("image_inputs"),
        )
        .order_by("-total", "feature")
    )
    feature_labels = dict(GeminiUsageLog.FEATURE_CHOICES)
    feature_max = max([_number(row["total"]) for row in feature_rows] or [1])
    for row in feature_rows:
        row["label"] = feature_labels.get(row["feature"], row["feature"])
        row["total_fmt"] = _format_number(row["total"])
        row["prompt_fmt"] = _format_number(row["prompt"])
        row["output_fmt"] = _format_number(row["output"])
        row["calls_fmt"] = _format_number(row["calls"])
        row["images_fmt"] = _format_number(row["images"])
        row["bar_percent"] = max(2, round(_number(row["total"]) / feature_max * 100))

    model_rows = list(
        filtered_qs.values("model")
        .annotate(
            calls=Count("id"),
            total=Sum("total_tokens"),
            failures=Count("id", filter=Q(is_success=False)),
        )
        .order_by("-total", "model")
    )
    for row in model_rows:
        row["model"] = row["model"] or "모델명 확인 불가"
        row["total_fmt"] = _format_number(row["total"])
        row["calls_fmt"] = _format_number(row["calls"])

    daily_rows = list(
        filtered_qs.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(calls=Count("id"), total=Sum("total_tokens"))
        .order_by("day")
    )
    daily_max = max([_number(row["total"]) for row in daily_rows] or [1])
    for row in daily_rows:
        row["total_fmt"] = _format_number(row["total"])
        row["calls_fmt"] = _format_number(row["calls"])
        row["bar_percent"] = max(2, round(_number(row["total"]) / daily_max * 100))

    logs = filtered_qs.order_by("-created_at", "-id")
    paginator = Paginator(logs, 50)
    page_obj = paginator.get_page(request.GET.get("page", "1"))
    for log in page_obj.object_list:
        log.total_tokens_fmt = _format_number(log.total_tokens)
        log.prompt_tokens_fmt = _format_number(log.prompt_tokens)
        log.output_tokens_fmt = _format_number(log.output_tokens)
        log.duration_seconds = f"{log.duration_ms / 1000:.1f}"

    models = list(
        GeminiUsageLog.objects.exclude(model="")
        .values_list("model", flat=True)
        .distinct()
        .order_by("model")
    )

    context = {
        "days": days,
        "selected_feature": feature,
        "selected_model": model_name,
        "selected_status": status,
        "today_summary": today_summary,
        "month_summary": month_summary,
        "filtered_summary": filtered_summary,
        "feature_rows": feature_rows,
        "model_rows": model_rows,
        "daily_rows": daily_rows,
        "page_obj": page_obj,
        "feature_choices": GeminiUsageLog.FEATURE_CHOICES,
        "model_choices": models,
    }
    return render(request, "core/gemini_usage_dashboard.html", context)

