from django import forms
from .models import Post


class PostForm(forms.ModelForm):
    class Meta:
        model = Post

        fields = [
    "category",
    "title",
    "thumbnail",
    "thumbnail_text",
    "video_file",
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

            "location": forms.TextInput(attrs={
                "class": "form-input",
                "placeholder": "예: 서울 강남구 / 현장명 / 장소"
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
        }