import secrets

from django.core.exceptions import ImproperlyConfigured


def resolve_secret_key(value, *, debug, argv):
    """Return the configured key, or a throwaway one for local debug and test runs."""
    value = (value or "").strip()
    if value:
        return value
    if debug or (len(argv) > 1 and argv[1] == "test"):
        return secrets.token_urlsafe(64)
    raise ImproperlyConfigured(
        "Set DJANGO_SECRET_KEY in the environment or .env, or set DJANGO_DEBUG=True for local development."
    )
