from django import forms
from .models import Post


class PostForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = ["thumbnail", "category", "title", "content"]

        widgets = {
            "thumbnail": forms.ClearableFileInput(attrs={
                "class": "thumbnail-input",
                "accept": "image/*"
            }),
            "category": forms.Select(attrs={
                "class": "form-control"
            }),
            "title": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "제목을 입력하세요"
            }),
            "content": forms.Textarea(attrs={
                "class": "form-control textarea",
                "placeholder": "내용을 입력하세요"
            }),
        }

        labels = {
            "thumbnail": "썸네일",
            "category": "카테고리",
            "title": "제목",
            "content": "내용",
        }