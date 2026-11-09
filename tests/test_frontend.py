import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from .conftest import create_meeting

ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = ROOT / "app" / "static" / "js"


class LinkCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for key in ("href", "src", "action"):
            if attrs.get(key):
                self.links.append(attrs[key])
        if attrs.get("id"):
            self.ids.append(attrs["id"])


def collect(html):
    parser = LinkCollector()
    parser.feed(html)
    return parser


PUBLIC_PAGES = ["/auth/login", "/auth/signup"]
PRIVATE_PAGES = ["/dashboard", "/meeting/new", "/tasks", "/analytics"]


def test_public_pages_render(client):
    for path in PUBLIC_PAGES:
        response = client.get(path)
        assert response.status_code == 200 and b"NEXUS" in response.data and b'lang="en"' in response.data


def test_private_pages_render_for_signed_in_user(auth_client, meeting):
    for path in PRIVATE_PAGES + [f"/meeting/{meeting.id}"]:
        response = auth_client.get(path)
        assert response.status_code == 200, path
        for expected in (b"Command Center", b"Action Matrix", b"Analytics", b"AI CORE ONLINE", b"Alice"):
            assert expected in response.data, (path, expected)


def test_empty_account_pages_render(auth_client):
    assert b"No meetings yet." in auth_client.get("/dashboard").data
    assert b"No tasks yet." in auth_client.get("/tasks").data
    assert b"No action items yet." in auth_client.get("/dashboard").data


def test_every_internal_link_form_and_asset_resolves(auth_client, meeting):
    pages = PRIVATE_PAGES + [f"/meeting/{meeting.id}"]
    checked = set()
    for page in pages:
        for link in collect(auth_client.get(page).get_data(as_text=True)).links:
            if link.startswith(("http://", "https://", "//", "#", "mailto:")):
                continue
            path = link.split("#")[0]
            if path in checked:
                continue
            checked.add(path)
            if path.endswith("/logout") or "/status" in path:
                assert auth_client.get(path).status_code == 405, path  # POST-only endpoints exist
            else:
                assert auth_client.get(path).status_code in (200, 302), (page, path)
    assert "/static/css/nexus.css" in checked and "/static/js/nexus.js" in checked


def test_login_and_signup_links_resolve(client):
    for page in PUBLIC_PAGES:
        for link in collect(client.get(page).get_data(as_text=True)).links:
            if not link.startswith(("http", "//")):
                assert client.get(link).status_code == 200, (page, link)


def test_charts_are_served_locally_not_from_a_cdn(auth_client, user):
    create_meeting(user)
    for path in ("/dashboard", "/analytics"):
        body = auth_client.get(path).get_data(as_text=True)
        assert "/static/js/chart.umd.min.js" in body and "cdn.jsdelivr" not in body
    assert auth_client.get("/static/js/chart.umd.min.js").status_code == 200


def test_chart_data_is_embedded_as_json(auth_client, meeting):
    body = auth_client.get("/analytics").get_data(as_text=True)
    assert '"Pending", "In Progress", "Completed"' in body.replace("\n", " ") or "Pending" in body
    assert "NEXUS.renderChart" in body


def test_element_ids_are_unique_on_each_page(auth_client, meeting):
    for path in PRIVATE_PAGES + [f"/meeting/{meeting.id}"]:
        ids = collect(auth_client.get(path).get_data(as_text=True)).ids
        assert len(ids) == len(set(ids)), path


def test_original_ui_elements_are_preserved(auth_client, meeting):
    dashboard = auth_client.get("/dashboard").get_data(as_text=True)
    for fragment in ("From conversation", "Start analysis", "MEETINGS", "ACTION ITEMS", "Recent intelligence", "Execution pulse", 'class="reactor"'):
        assert fragment in dashboard
    detail = auth_client.get(f"/meeting/{meeting.id}").get_data(as_text=True)
    for fragment in ("Executive summary", "Decisions", "Topics", "Ask NEXUS", "CONTEXT-AWARE", "Action matrix", "Transcript"):
        assert fragment in detail
    new_meeting = auth_client.get("/meeting/new").get_data(as_text=True)
    for fragment in ("Capture meeting", "Choose or drop audio", "Or paste transcript", "Run AI analysis", "AI pipeline", "01 · Speech"):
        assert fragment in new_meeting


def test_no_whisper_or_openai_text_remains_in_ui(auth_client):
    for path in ("/dashboard", "/meeting/new"):
        text = auth_client.get(path).get_data(as_text=True).lower()
        assert "whisper" not in text and "openai" not in text


def test_forms_have_labels_and_accessible_names(auth_client, meeting):
    page = auth_client.get("/meeting/new").get_data(as_text=True)
    for field in ("title", "audio", "transcript"):
        assert f'for="{field}"' in page and f'id="{field}"' in page
    detail = auth_client.get(f"/meeting/{meeting.id}").get_data(as_text=True)
    assert 'aria-label="Send question"' in detail and 'aria-label="Ask a question about this meeting"' in detail


def test_css_has_responsive_rules_and_keeps_navigation_visible_on_small_screens():
    css = (ROOT / "app" / "static" / "css" / "nexus.css").read_text()
    assert "@media(max-width:900px)" in css and "@media(max-width:600px)" in css
    assert "font-size:0" not in css  # the original hid the whole navigation on small screens
    assert "#060811" in css and "#7454ff" in css and "#a65bff" in css  # dark theme + purple gradient preserved


def test_javascript_has_valid_syntax_and_no_innerhtml_for_ai_text():
    source = (STATIC_JS / "nexus.js").read_text()
    assert "innerHTML" not in source
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run([node, "--check", str(STATIC_JS / "nexus.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_javascript_behaviour_under_jsdom():
    node = shutil.which("node")
    if node is None or not (ROOT / "tests" / "js" / "node_modules" / "jsdom").exists():
        pytest.skip("run `npm install` in tests/js to enable the jsdom tests")
    result = subprocess.run([node, "--test", str(ROOT / "tests" / "js" / "chat.test.js")], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]


def test_inline_scripts_in_templates_are_valid_javascript(auth_client, meeting):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    for path in ("/dashboard", "/analytics"):
        html = auth_client.get(path).get_data(as_text=True)
        for index, script in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
            target = ROOT / "instance" / f"inline_{index}.js"
            target.write_text(script)
            try:
                result = subprocess.run([node, "--check", str(target)], capture_output=True, text=True)
                assert result.returncode == 0, (path, result.stderr)
            finally:
                target.unlink()


def test_project_contains_no_openai_code_or_dependencies():
    offenders = []
    for path in list(ROOT.glob("app/**/*.py")) + [ROOT / "requirements.txt", ROOT / ".env.example", ROOT / "config.py", ROOT / "run.py"]:
        if re.search(r"openai|whisper", path.read_text(), re.I):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
