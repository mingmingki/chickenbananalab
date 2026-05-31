from django import forms
from .models import Post, UserProfile


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

            "video_file": forms.ClearableFileInput(attrs={
                "accept": "video/mp4,video/webm,video/ogg,video/quicktime",
            }),

            "program_file": forms.ClearableFileInput(attrs={
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