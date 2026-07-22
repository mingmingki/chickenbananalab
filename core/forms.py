from django import forms
from .models import Post, Comment, UserProfile, ExperienceVault
from .cbl_category_policy import CBL_PUBLIC_CATEGORY_CHOICES
from django import forms as cbl_django_forms


class PostForm(forms.ModelForm):
    class Meta:
        model = Post

        fields = [
            "post_type",
            "category",
            "title",
            "thumbnail",
            "thumbnail_text",
            "video_file",
            "youtube_url",
            "program_file",
            "is_published",
            "content",
            "tags",
        ]

        widgets = {
            "post_type": forms.Select(attrs={
                "class": "form-select post-type-select",
            }),

            "category": forms.Select(attrs={
                "class": "form-select"
            }),

            "title": forms.TextInput(attrs={
                "class": "form-input",
                "placeholder": "제목을 입력하세요"
            }),

            # 대표 썸네일: 수정 화면에서 새 이미지를 선택하면 request.FILES로 넘어가도록
            # 단순 FileInput으로 표시합니다. 기존 파일은 아래 post_form.html의 미리보기에서 확인합니다.
            "thumbnail": forms.FileInput(attrs={
                "class": "thumbnail-file-input",
                "accept": "image/jpeg,image/png,image/webp,image/gif",
            }),

            "thumbnail_text": forms.TextInput(attrs={
                "class": "form-input",
                "placeholder": "썸네일 중앙에 표시할 문구를 입력하세요"
            }),

            "content": forms.Textarea(attrs={
                "class": "form-textarea rich-content-input",
                "placeholder": "내용을 입력하세요.",
                "rows": 14
            }),

            "tags": forms.TextInput(attrs={
                "class": "form-input",
                "placeholder": "예: Django, 자동매매, 공사일보, 부동산"
            }),

            "video_file": forms.FileInput(attrs={
                "accept": "video/mp4,video/webm,video/ogg,video/quicktime",
            }),

            "youtube_url": forms.URLInput(attrs={
                "id": "youtubeUrlInput",
                "class": "youtube-url-input",
                "placeholder": "https://www.youtube.com/watch?v=...",
            }),

            "program_file": forms.FileInput(attrs={
                "accept": ".zip,.rar,.7z,.exe,.msi,.dmg,.pkg,.apk,.py,.whl",
            }),

            "is_published": forms.CheckboxInput(attrs={
                "class": "publish-checkbox",
            }),
        }

        labels = {
            "post_type": "게시글 유형",
            "youtube_url": "유튜브 주소",
            "is_published": "공개 글로 저장",
        }

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("post_type") == "video":
            video_file = cleaned_data.get("video_file")
            youtube_url = (cleaned_data.get("youtube_url") or "").strip()
            if not video_file and not youtube_url:
                raise forms.ValidationError(
                    "영상 글은 유튜브 주소 또는 동영상 파일 중 하나가 필요합니다."
                )
        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # CBL_CONSTRUCTION_POSTFORM_CHOICES_START
        self.fields["category"].choices = [
            ("construction_work", "건설실무"),
            ("construction_tech", "건설기술"),
            ("bim", "BIM"),
            ("construction_real", "건설부동산"),
            ("finance", "금융"),
            ("tech", "테크"),
            ("program", "프로그램"),
            ("life", "일상"),
        ]

        legacy_initial = self.initial.get("category") or getattr(self.instance, "category", "")
        # CBL_BTP_POSTFORM_INIT_ALIAS_START
        if legacy_initial == "architecture":
            self.initial["category"] = "construction_work"
        elif legacy_initial == "realestate":
            self.initial["category"] = "construction_real"
        elif legacy_initial == "bim":
            self.initial["category"] = "bim"
        elif legacy_initial == "program":
            self.initial["category"] = "program"
        # CBL_BTP_POSTFORM_INIT_ALIAS_END
        # CBL_CONSTRUCTION_POSTFORM_CHOICES_END

        if not self.instance.pk:
            self.fields["is_published"].initial = True



class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ["content"]

        widgets = {
            "content": forms.Textarea(attrs={
                "class": "comment-textarea",
                "placeholder": "댓글을 입력해주세요.",
                "maxlength": "1000",
                "rows": "4",
            }),
        }

        labels = {
            "content": "",
        }

    def clean_content(self):
        content = (self.cleaned_data.get("content") or "").strip()

        if not content:
            raise forms.ValidationError("댓글 내용을 입력해주세요.")

        if len(content) > 1000:
            raise forms.ValidationError("댓글은 1,000자 이하로 작성해주세요.")

        return content


class NicknameForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["nickname"]

        widgets = {
            "nickname": forms.TextInput(attrs={
                "class": "form-input",
                "placeholder": "사용할 닉네임을 입력하세요",
                "maxlength": "30",
            })
        }

        labels = {
            "nickname": "닉네임",
        }

    def clean_nickname(self):
        nickname = self.cleaned_data.get("nickname", "").strip()

        if not nickname:
            raise forms.ValidationError("닉네임을 입력해주세요.")

        if len(nickname) < 2:
            raise forms.ValidationError("닉네임은 2글자 이상 입력해주세요.")

        return nickname


class ExperienceVaultForm(forms.ModelForm):
    class Meta:
        model = ExperienceVault
        fields = ["content", "is_active"]
        widgets = {
            "content": forms.Textarea(attrs={
                "rows": 22,
                "placeholder": (
                    "여기에 경험을 막 적어두면 됩니다.\n\n"
                    "예)\n"
                    "아이폰은 12프로, 14프로, 15프로맥스 써봤다. "
                    "15프로맥스는 카메라는 좋은데 무겁고 한 손 사용은 불편했다.\n\n"
                    "다이나믹메이즈 인사동은 오라카이 인사동 주차장이 편했다. "
                    "주말 대기 40분이라고 들었는데 실제로는 1시간 정도 걸렸다.\n\n"
                    "건설현장에서 마감공사는 도면상 문제 없어 보여도 "
                    "실제 시공하면 문틀, 몰딩, 타일, 가구 간섭이 자주 생긴다."
                ),
                "style": (
                    "width:100%; min-height:520px; padding:18px; "
                    "border:1px solid #d1d5db; border-radius:18px; "
                    "font-size:15px; line-height:1.8; resize:vertical;"
                )
            }),
            "is_active": forms.CheckboxInput(attrs={
                "style": "width:18px; height:18px;"
            }),
        }


# CBL_PUBLIC_CATEGORY_FORM_CHOICES_START
# 글 올리기 / 자동글 관련 폼에서 새 카테고리 8개만 노출
for _cbl_form_name in [
    "PostForm",
    "PostCreateForm",
    "PostUpdateForm",
    "AIAutoWriterSettingForm",
    "AIAutoWriterForm",
    "AIKeywordForm",
]:
    try:
        _cbl_form = globals().get(_cbl_form_name)
        if not _cbl_form:
            continue
        for _field_name in ["category", "categories", "target_category"]:
            if _field_name in _cbl_form.base_fields:
                _cbl_form.base_fields[_field_name].choices = CBL_PUBLIC_CATEGORY_CHOICES
    except Exception:
        pass
# CBL_PUBLIC_CATEGORY_FORM_CHOICES_END


# CBL_POST_ADD_CATEGORY_FORCE_START
# 글 올리기/수정 폼의 category 선택지를 신규 8개 카테고리로 강제
def _cbl_force_public_category_choices_on_instance(_form):
    _choices = [("", "카테고리를 선택하세요")] + list(CBL_PUBLIC_CATEGORY_CHOICES)

    for _field_name in ["category", "target_category"]:
        if hasattr(_form, "fields") and _field_name in _form.fields:
            _form.fields[_field_name].choices = _choices

def _cbl_patch_form_category_choices():
    for _name, _cls in list(globals().items()):
        try:
            if not isinstance(_cls, type):
                continue
            if not issubclass(_cls, cbl_django_forms.BaseForm):
                continue
            if getattr(_cls, "_cbl_post_add_category_patched", False):
                continue

            # base_fields도 즉시 변경
            for _field_name in ["category", "target_category"]:
                if hasattr(_cls, "base_fields") and _field_name in _cls.base_fields:
                    _cls.base_fields[_field_name].choices = [("", "카테고리를 선택하세요")] + list(CBL_PUBLIC_CATEGORY_CHOICES)

            _old_init = _cls.__init__

            def _make_init(__old_init):
                def __init__(self, *args, **kwargs):
                    __old_init(self, *args, **kwargs)
                    _cbl_force_public_category_choices_on_instance(self)
                return __init__

            _cls.__init__ = _make_init(_old_init)
            _cls._cbl_post_add_category_patched = True
        except Exception:
            pass

_cbl_patch_form_category_choices()
# CBL_POST_ADD_CATEGORY_FORCE_END
