@echo off
chcp 65001 > nul

echo ===== config/settings.py ===== > current_code.txt
type config\settings.py >> current_code.txt

echo. >> current_code.txt
echo ===== config/urls.py ===== >> current_code.txt
type config\urls.py >> current_code.txt

echo. >> current_code.txt
echo ===== core/apps.py ===== >> current_code.txt
type core\apps.py >> current_code.txt

echo. >> current_code.txt
echo ===== core/signals.py ===== >> current_code.txt
if exist core\signals.py (
    type core\signals.py >> current_code.txt
) else (
    echo core\signals.py 없음 >> current_code.txt
)

echo. >> current_code.txt
echo ===== core/models.py ===== >> current_code.txt
type core\models.py >> current_code.txt

echo. >> current_code.txt
echo ===== core/forms.py ===== >> current_code.txt
type core\forms.py >> current_code.txt

echo. >> current_code.txt
echo ===== core/views.py ===== >> current_code.txt
type core\views.py >> current_code.txt

echo. >> current_code.txt
echo ===== core/urls.py ===== >> current_code.txt
type core\urls.py >> current_code.txt

echo. >> current_code.txt
echo ===== core/templates/core/base.html ===== >> current_code.txt
type core\templates\core\base.html >> current_code.txt

echo. >> current_code.txt
echo ===== core/templates/registration/login.html ===== >> current_code.txt
type core\templates\registration\login.html >> current_code.txt

echo. >> current_code.txt
echo ===== core/templates/socialaccount/signup.html ===== >> current_code.txt
if exist core\templates\socialaccount\signup.html (
    type core\templates\socialaccount\signup.html >> current_code.txt
) else (
    echo core\templates\socialaccount\signup.html 없음 >> current_code.txt
)

echo. >> current_code.txt
echo ===== core/templates/account/signup.html ===== >> current_code.txt
if exist core\templates\account\signup.html (
    type core\templates\account\signup.html >> current_code.txt
) else (
    echo core\templates\account\signup.html 없음 >> current_code.txt
)

echo. >> current_code.txt
echo ===== core/templates/core/home.html ===== >> current_code.txt
type core\templates\core\home.html >> current_code.txt

echo. >> current_code.txt
echo ===== core/templates/core/category.html ===== >> current_code.txt
type core\templates\core\category.html >> current_code.txt

echo. >> current_code.txt
echo ===== core/templates/core/post_form.html ===== >> current_code.txt
type core\templates\core\post_form.html >> current_code.txt

echo. >> current_code.txt
echo ===== core/templates/core/post_detail.html ===== >> current_code.txt
type core\templates\core\post_detail.html >> current_code.txt

echo. >> current_code.txt
echo ===== core/templates/core/admin_dashboard.html ===== >> current_code.txt
type core\templates\core\admin_dashboard.html >> current_code.txt

echo. >> current_code.txt
echo ===== core/static/core/css/style.css ===== >> current_code.txt
type core\static\core\css\style.css >> current_code.txt

echo.
echo current_code.txt created!
pause