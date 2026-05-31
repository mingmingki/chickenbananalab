# Generated manually to fix SEO Post fields migration

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_userprofile_is_sub_admin"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="slug",
            field=models.SlugField(
                max_length=220,
                blank=True,
                allow_unicode=True,
                verbose_name="주소 슬러그",
            ),
        ),
        migrations.AddField(
            model_name="post",
            name="summary",
            field=models.TextField(
                blank=True,
                verbose_name="요약",
            ),
        ),
        migrations.AddField(
            model_name="post",
            name="meta_description",
            field=models.CharField(
                max_length=160,
                blank=True,
                verbose_name="SEO 설명",
            ),
        ),
    ]