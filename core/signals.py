from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver


@receiver(user_logged_in)
def grant_admin_to_allowed_google_user(sender, request, user, **kwargs):
    allowed_emails = getattr(settings, "ADMIN_GOOGLE_EMAILS", [])

    user_email = (user.email or "").lower().strip()
    allowed_emails = [email.lower().strip() for email in allowed_emails]

    if user_email in allowed_emails:
        changed = False

        if not user.is_staff:
            user.is_staff = True
            changed = True

        if not user.is_superuser:
            user.is_superuser = True
            changed = True

        if changed:
            user.save(update_fields=["is_staff", "is_superuser"])