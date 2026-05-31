from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(user_logged_in)
def prepare_user_after_login(sender, request, user, **kwargs):
    UserProfile.objects.get_or_create(user=user)

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