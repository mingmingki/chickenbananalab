from django import forms
from .models import Post, Comment, UserProfile, ExperienceVault


class PostForm(forms.ModelForm):
    class Meta:
        model = Post

        fields = [
            "category",
            "title",
            "thumbnail",
            "thumbnail_text",
            "video_file",
            "program_file",
            "is_published",
            "content",
            "tags",
        ]

        widgets = {
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

            "program_file": forms.FileInput(attrs={
                "accept": ".zip,.rar,.7z,.exe,.msi,.dmg,.pkg,.apk,.py,.whl",
            }),

            "is_published": forms.CheckboxInput(attrs={
                "class": "publish-checkbox",
            }),
        }

        labels = {
            "is_published": "공개 글로 저장",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

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
