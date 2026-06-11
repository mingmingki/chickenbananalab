from django import template
from core.models import Post

register = template.Library()

CATEGORY_CONFIG = [
    {"slug": "architecture", "title": "건축", "limit": 4, "wide": False},
    {"slug": "realestate", "title": "부동산", "limit": 4, "wide": False},
    {"slug": "finance", "title": "금융", "limit": 4, "wide": False},
    {"slug": "tech", "title": "테크", "limit": 4, "wide": False},
    {"slug": "life", "title": "일상", "limit": 5, "wide": False},
]

def _field_names():
    return {f.name for f in Post._meta.get_fields()}

def _order_field():
    names = _field_names()
    if "created_at" in names:
        return "-created_at"
    if "updated_at" in names:
        return "-updated_at"
    return "-id"

def _base_queryset():
    qs = Post.objects.all()
    names = _field_names()
    if "is_published" in names:
        qs = qs.filter(is_published=True)
    return qs

def _category_posts(slug, title, limit):
    qs = _base_queryset()
    order = _order_field()

    candidates = [
        ("category", slug),
        ("category", title),
        ("category__slug", slug),
        ("category__name", title),
        ("category__title", title),
    ]

    for key, value in candidates:
        try:
            test_qs = qs.filter(**{key: value}).order_by(order)
            list(test_qs[:1])
            return test_qs[:limit]
        except Exception:
            continue

    return Post.objects.none()

@register.simple_tag
def get_home_category_sections():
    return [
        {
            "slug": config["slug"],
            "title": config["title"],
            "posts": _category_posts(config["slug"], config["title"], config["limit"]),
            "wide": config.get("wide", False),
        }
        for config in CATEGORY_CONFIG
    ]
