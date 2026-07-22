from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0030_geminiusagelog"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="post_type",
            field=models.CharField(
                choices=[("article", "일반 글"), ("video", "영상 글")],
                db_index=True,
                default="article",
                max_length=20,
                verbose_name="게시글 유형",
            ),
        ),
        migrations.AddField(
            model_name="post",
            name="youtube_url",
            field=models.URLField(
                blank=True,
                default="",
                max_length=500,
                verbose_name="유튜브 주소",
            ),
        ),
    ]
