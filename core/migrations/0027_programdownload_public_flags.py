# Generated manually by ChickenBananaLab program public/scroll patch

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0026_aiautowritersetting_use_bim_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='programdownload',
            name='mac_is_public',
            field=models.BooleanField(default=False, verbose_name='Mac 공개'),
        ),
        migrations.AddField(
            model_name='programdownload',
            name='windows_is_public',
            field=models.BooleanField(default=False, verbose_name='Windows 공개'),
        ),
    ]
