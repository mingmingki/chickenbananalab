"""
회원가입 폼 확장: 관심 분야 / 추천인 아이디 / 약관 동의 / 마케팅 수신 동의,
그리고 아이디 중복확인 API.

실제 서비스에서 사용 중인 회원가입 경로는 core.views.signup (core/urls.py의
"signup/") 이며, django.contrib.auth.forms.UserCreationForm을 그대로 쓰고
있다. (django-allauth의 /accounts/signup/ 은 어디에서도 링크되지 않는
미사용 경로라 건드리지 않는다.) 따라서 이 파일은 UserCreationForm을
확장하는 방식으로 작성한다.

기존 CAD/수량산출 코드가 있는 views.py와는 완전히 분리된 별도 파일이다.
"""
import re

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .models import UserProfile


INTEREST_CHOICES = [
    ("construction_work", "건설실무"),
    ("construction_tech", "건설기술"),
    ("construction_real", "건설부동산"),
    ("bim_dynamo", "BIM·Dynamo"),
    ("ai_dev", "AI·개발"),
    ("data_security_server", "데이터·보안·서버"),
    ("program", "업무용 프로그램"),
    ("quantity_takeoff", "AI수량산출"),
    ("job", "구인구직"),
    ("cad", "CAD"),
]

INTEREST_CODE_SET = frozenset(code for code, _label in INTEREST_CHOICES)
INTEREST_LABEL_MAP = dict(INTEREST_CHOICES)

_USERNAME_PATTERN = re.compile(r"^[\w.@+-]+$")


def _username_check_result(username):
    username = (username or "").strip()

    if not username:
        return {"ok": False, "available": False, "message": "아이디를 입력해 주세요."}

    if len(username) > 150:
        return {"ok": False, "available": False, "message": "아이디는 150자 이하로 입력해 주세요."}

    if not _USERNAME_PATTERN.match(username):
        return {
            "ok": False,
            "available": False,
            "message": "문자, 숫자 그리고 @/./+/-/_만 사용할 수 있습니다.",
        }

    exists = User.objects.filter(username__iexact=username).exists()

    if exists:
        return {"ok": True, "available": False, "message": "이미 사용 중인 아이디예요."}

    return {"ok": True, "available": True, "message": "사용할 수 있는 아이디예요."}


@require_GET
def api_username_available(request):
    result = _username_check_result(request.GET.get("username", ""))
    return JsonResponse(result)


class CblSignupForm(UserCreationForm):
    """
    기존 UserCreationForm(아이디/비밀번호/비밀번호 확인)에 관심 분야,
    추천인 아이디, 약관 동의, 마케팅 수신 동의를 추가한다.
    """

    interests = forms.MultipleChoiceField(
        choices=INTEREST_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="관심 분야",
    )

    referrer_username = forms.CharField(
        required=False,
        max_length=150,
        label="추천인 아이디",
    )

    terms_agree = forms.BooleanField(
        required=True,
        label="이용약관 및 개인정보처리방침에 동의합니다",
        error_messages={"required": "이용약관 및 개인정보처리방침에 동의해 주세요."},
    )

    marketing_opt_in = forms.BooleanField(
        required=False,
        label="새 프로그램, 이벤트 소식을 이메일로 받아볼게요",
    )

    def clean_interests(self):
        selected = self.cleaned_data.get("interests") or []
        return [code for code in selected if code in INTEREST_CODE_SET]

    def clean_referrer_username(self):
        value = (self.cleaned_data.get("referrer_username") or "").strip()

        if not value:
            return ""

        if not User.objects.filter(username__iexact=value).exists():
            raise forms.ValidationError("존재하지 않는 추천인 아이디예요. 다시 확인해 주세요.")

        return value

    def save(self, commit=True):
        user = super().save(commit=commit)

        if commit:
            self._save_profile(user)

        return user

    def _save_profile(self, user):
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.interests = self.cleaned_data.get("interests") or []
        profile.marketing_opt_in = bool(self.cleaned_data.get("marketing_opt_in"))

        referrer_username = self.cleaned_data.get("referrer_username")
        if referrer_username:
            referrer = (
                User.objects.filter(username__iexact=referrer_username)
                .exclude(pk=user.pk)
                .first()
            )
            if referrer:
                profile.referred_by = referrer

        profile.save(update_fields=["interests", "marketing_opt_in", "referred_by", "updated_at"])
