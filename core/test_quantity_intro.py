"""Public introduction page, guarded start and the guided stage hooks of the quantity workflow."""
import re

from django.contrib.auth import get_user_model
from django.test import TestCase


class QuantityIntroTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.member = users.create_user("member", password="member-pass-1")
        self.staff = users.create_user("staff", password="staff-pass-1", is_staff=True)

    def test_intro_is_public_and_offers_login_with_return_to_start(self):
        response = self.client.get("/quantity/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('data-qi-authenticated="false"', html)
        self.assertIn('data-qi-can-start="false"', html)
        self.assertIn('action="/accounts/login/"', html)
        self.assertIn('name="next" value="/quantity/start/"', html)
        self.assertIn("/accounts/google/login/?next=%2Fquantity%2Fstart%2F", html)
        self.assertEqual(len(re.findall(r"data-qi-start>", html)), 6)  # hero, four steps, notice
        for step in ("프로젝트 확인", "도면 업로드", "AI 판독·검토", "수량산출·엑셀"):
            self.assertIn(step, html)

    def test_start_requires_login(self):
        response = self.client.get("/quantity/start/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/accounts/login/?next=/quantity/start/")

    def test_member_without_beta_access_returns_to_the_notice(self):
        self.client.force_login(self.member)
        response = self.client.get("/quantity/start/")
        self.assertEqual(response.url, "/quantity/?beta=restricted")
        html = self.client.get(response.url).content.decode()
        self.assertIn('data-qi-restricted="true"', html)
        self.assertIn('data-qi-can-start="false"', html)

    def test_staff_starts_the_guided_workflow(self):
        self.client.force_login(self.staff)
        self.assertIn('data-qi-can-start="true"', self.client.get("/quantity/").content.decode())
        response = self.client.get("/quantity/start/")
        self.assertEqual(response.url, "/?quantity=1")

    def test_quantity_apis_stay_staff_only(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get("/api/quantity/jobs/").status_code, 403)

    def test_home_card_links_to_intro_and_stage_loads_before_the_workflow(self):
        html = self.client.get("/").content.decode()
        card = html[html.index('cbl-svc-card--quantity'):html.index('cbl-svc-card--job')]
        self.assertIn('class="cbl-svc-btn" href="/quantity/"', card)
        self.assertNotIn("cblOpenBetaGate('quantity')", card)
        stage = html.index("core/js/quantity_stage.js")
        workflow = html.index('var modal = document.getElementById("cblQuantityMessengerModal");')
        self.assertLess(stage, workflow)
        self.assertIn("window.cblQuantityStage.botSaid(html)", html)
        self.assertIn("window.cblQuantityStage.intro(startFlow)", html)


class HomeLayoutTests(TestCase):
    def test_service_cards_share_one_structure_and_keep_their_actions(self):
        html = self.client.get("/").content.decode()
        section = html[html.index('<section class="cbl-svc"'):html.index("</section>", html.index('<section class="cbl-svc"'))]
        self.assertEqual(section.count('class="cbl-svc-card '), 4)
        for part in ('class="cbl-svc-icon', 'class="cbl-svc-title"', 'class="cbl-svc-text"', 'class="cbl-svc-btn'):
            self.assertEqual(section.count(part), 4, part)
        self.assertIn("data-cbl-beta-open=\"job\" onclick=\"return cblOpenBetaGate('job');\"", section)
        self.assertIn("cblOpenHomeServiceModal('cblProgramDownloadModal')", section)
        self.assertIn('class="cbl-svc-btn cbl-webcad-start-btn" onclick="return cblStartCadFromHomeV1();"', section)
        self.assertIn("core/css/home_layout.css", html)

    def test_header_auth_links_are_not_cloned_by_script(self):
        html = self.client.get("/about/").content.decode()
        self.assertNotIn("cblHeaderAuthRightOverlay", html)
        account = html[html.index('class="cbl-header-v7-account"'):html.index('class="cbl-header-v7-icons"')]
        self.assertIn(">회원가입</a>", account)
        self.assertIn(">로그인</a>", account)
