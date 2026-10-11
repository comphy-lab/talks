"""Semantic shell drift, exact exceptions, immutable fetches and failure accounting."""

import json
import shutil
import subprocess
import urllib.error
from pathlib import Path

import pytest
import yaml

from talks_build import shell_drift as shell

SHA = "a" * 40
CSS = "@media screen and (max-width: 1119px) { .s-header__menu-toggle { display: block; } }"
HEADER = '''<header class="s-header"><nav><ul class="s-header__nav-list">
  <li><button aria-label="Dark theme" aria-pressed="false"></button></li>
  <li><a href="/team/"><span>Team</span><span aria-hidden="true">decoration</span></a></li>
  <li><a href="/talks/">Talks</a></li>
  <li><a href="https://github.com/comphy-lab" aria-label="GitHub Organization"><i></i></a></li>
  <li class="command-palette-button"><button>Search</button></li>
</ul></nav></header>'''
FOOTER = '''<footer class="c-footer"><div class="c-footer__brand">
 <a href="/" aria-label="CoMPhy Lab home"><img alt=""></a></div>
 <nav class="c-footer__col" aria-label="Explore"><h4>Explore</h4>
 <a href="/research">Research</a><a href="/talks/">Talks</a></nav>
 <div class="c-footer__legal"><a href="https://github.com/comphy-lab/comphy-lab.github.io">Edit on GitHub</a>
 <a href="#top">Back to top ↑</a></div></footer>'''


def talks_html():
    header = HEADER.replace('<li class="command-palette-button"><button>Search</button></li>', "")
    header = header.replace('href="/talks/"', 'href="/talks/" aria-current="page"')
    footer = FOOTER.replace("<h4>", "<h2>").replace("</h4>", "</h2>")
    footer = footer.replace(shell.EDIT_UPSTREAM, shell.EDIT_TALKS)
    return header + footer


def test_documented_differences_and_whitespace_match():
    expected = shell.extract(HEADER, FOOTER, CSS)
    html = talks_html().replace(">Team<", ">  Team \n <")
    actual = shell.extract(html, html, CSS, talks=True)
    assert shell.differences(expected, actual, "index.html") == []
    assert expected.navigation[0] == ("Team", "/team/")


def test_inline_markup_does_not_invent_spaces_inside_accessible_names():
    expected = shell.extract(HEADER, FOOTER, CSS)
    html = talks_html().replace("<span>Team</span>", "<span>Te</span>am")
    assert shell.differences(expected, shell.extract(html, html, CSS, talks=True), "index.html") == []


def test_nav_rename_and_footer_removal_are_both_reported():
    expected = shell.extract(HEADER, FOOTER, CSS)
    html = talks_html().replace("<span>Team</span>", "<span>People</span>")
    html = html.replace('<a href="/research">Research</a>', "")
    diff = "\n".join(shell.differences(expected, shell.extract(html, html, CSS, talks=True), "index.html"))
    assert "navigation differs" in diff and '"People"' in diff
    assert "footer differs" in diff and '"Research"' in diff


def test_nav_order_is_significant():
    expected = shell.extract(HEADER, FOOTER, CSS)
    html = talks_html().replace("<span>Team</span>", "<span>Talks</span>")
    assert shell.differences(expected, shell.extract(html, html, CSS, talks=True), "index.html")


def test_only_exact_edit_link_exception_is_normalised():
    expected = shell.extract(HEADER, FOOTER, CSS)
    html = talks_html().replace(shell.EDIT_TALKS, "https://github.com/comphy-lab/wrong")
    assert "footer differs" in "\n".join(
        shell.differences(expected, shell.extract(html, html, CSS, talks=True), "index.html")
    )


def test_aria_labelledby_resolves_and_missing_reference_fails():
    header = HEADER.replace('<span>Team</span>', '<span id="team-name">Team</span>')
    header = header.replace('href="/team/"', 'href="/team/" aria-labelledby="team-name"')
    assert shell.extract(header, FOOTER, CSS).navigation[0] == ("Team", "/team/")
    with pytest.raises(shell.ShellError, match="aria-labelledby"):
        shell.extract(header.replace('id="team-name"', 'id="other"'), FOOTER, CSS)
    header = HEADER.replace('href="/team/"', 'href="/team/" aria-labelledby="outside"')
    header = header.replace("</header>", '<span id="outside">People</span></header>')
    assert shell.extract(header, FOOTER, CSS).navigation[0] == ("People", "/team/")


def test_menu_breakpoint_ignores_unrelated_media_queries():
    css = CSS + "@media (max-width: 768px) { .c-footer { display: block; } }"
    assert shell.menu_breakpoint(css) == 1119
    expected = shell.extract(HEADER, FOOTER, CSS)
    html = talks_html()
    actual = shell.extract(html, html, CSS.replace("1119", "1023"), talks=True)
    assert shell.differences(expected, actual, "index.html") == [
        "index.html: Menu breakpoint 1023px; main site 1119px"
    ]


@pytest.mark.parametrize("css", ["", CSS + CSS.replace("1119", "768"), CSS[:-1]])
def test_missing_ambiguous_or_malformed_menu_rule_fails_closed(css):
    with pytest.raises(shell.ShellError):
        shell.menu_breakpoint(css)


@pytest.mark.parametrize("header,footer", [("", FOOTER), (HEADER, ""), (HEADER.replace("/team/", ""), FOOTER)])
def test_missing_shell_or_link_target_fails_closed(header, footer):
    with pytest.raises(shell.ShellError):
        shell.extract(header, footer, CSS)


def test_remote_files_all_use_one_resolved_main_commit(monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        if "api.github.com" in url:
            return json.dumps({"object": {"type": "commit", "sha": SHA}})
        return {"header.html": HEADER, "footer-v2.html": FOOTER, "styles.css": CSS}[url.rsplit("/", 1)[-1]]

    monkeypatch.setattr(shell, "fetch", fetch)
    assert shell.load_upstream(None) == (HEADER, FOOTER, CSS, SHA)
    assert len(calls) == 4
    assert all(f"/{SHA}/" in url for url in calls[1:])


@pytest.mark.parametrize("ref", [None, {}, {"object": None}, {"object": {"type": "commit", "sha": "main"}}])
def test_malformed_ref_fails_before_fetching_files(monkeypatch, ref):
    calls = []

    def fetch(url):
        calls.append(url)
        return json.dumps(ref)

    monkeypatch.setattr(shell, "fetch", fetch)
    with pytest.raises(shell.ShellError, match="commit SHA"):
        shell.load_upstream(None)
    assert len(calls) == 1


def test_bearer_is_only_sent_to_api_and_redirects_are_rejected(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return b"content"

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert timeout == 20
            return Response()

    monkeypatch.setenv("GITHUB_TOKEN", "test-only-token")
    monkeypatch.setattr(shell.urllib.request, "build_opener", lambda _handler: Opener())
    shell.fetch(f"https://api.github.com/repos/{shell.UPSTREAM}/git/ref/heads/main")
    shell.fetch(f"https://raw.githubusercontent.com/{shell.UPSTREAM}/{SHA}/_includes/header.html")
    assert requests[0].get_header("Authorization") == "Bearer test-only-token"
    assert requests[1].get_header("Authorization") is None
    with pytest.raises(shell.ShellError, match="redirect"):
        shell.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://other.invalid/")


@pytest.mark.parametrize("status", [404, 403, 500])
def test_only_missing_include_allows_legacy_fallback(monkeypatch, status):
    calls = []

    def fetch(url):
        calls.append(url)
        if "api.github.com" in url:
            return json.dumps({"object": {"type": "commit", "sha": SHA}})
        if url.endswith("_includes/header.html"):
            raise urllib.error.HTTPError(url, status, "failed", {}, None)
        return {"default.html": HEADER, "footer-v2.html": FOOTER, "styles.css": CSS}[url.rsplit("/", 1)[-1]]

    monkeypatch.setattr(shell, "fetch", fetch)
    if status == 404:
        assert shell.load_upstream(None)[0] == HEADER
        assert any(url.endswith("_layouts/default.html") for url in calls)
    else:
        with pytest.raises(urllib.error.HTTPError):
            shell.load_upstream(None)
        assert not any(url.endswith("_layouts/default.html") for url in calls)


def fixture_roots(tmp_path):
    upstream, site = tmp_path / "upstream", tmp_path / "site"
    files = {
        upstream / "_includes/header.html": HEADER,
        upstream / "_includes/footer-v2.html": FOOTER,
        upstream / "assets/css/styles.css": CSS,
        site / "index.html": talks_html(),
        site / "player/talk.template.html": talks_html(),
        site / "vendor/comphy-lab/header.css": CSS,
    }
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return upstream, site


def test_local_cli_checks_both_templates_and_reports_both_changes(tmp_path):
    upstream, site = fixture_roots(tmp_path)
    report_path = tmp_path / "report.json"
    args = ["--upstream-root", str(upstream), "--site-root", str(site), "--json-output", str(report_path)]
    assert shell.main(args) == 0
    assert json.loads(report_path.read_text())["status"] == "pass"
    template = site / "player/talk.template.html"
    template.write_text(
        template.read_text().replace("<span>Team</span>", "<span>People</span>")
        .replace('<a href="/research">Research</a>', "")
    )
    assert shell.main(args) == 1
    report = json.loads(report_path.read_text())
    diff = "\n".join(report["differences"])
    assert "talk.template.html: navigation differs" in diff
    assert "talk.template.html: footer differs" in diff
    assert "index.html: navigation differs" not in diff


def test_fetch_error_is_an_error_not_a_drift_issue(tmp_path, monkeypatch):
    def unavailable(_root):
        raise urllib.error.URLError("network unavailable")

    monkeypatch.setattr(shell, "load_upstream", unavailable)
    report_path = tmp_path / "report.json"
    assert shell.main(["--json-output", str(report_path)]) == 2
    report = json.loads(report_path.read_text())
    assert report["status"] == "error" and report["differences"] == []
    assert "issue_body" not in report


def test_issue_content_is_stable_and_names_the_actual_commit():
    diff = ['index.html: navigation differs', '+ ["People", "/team/"]']
    body = shell.issue_body(SHA, diff)
    assert body == shell.issue_body(SHA, diff)
    assert shell.ISSUE_MARKER in body and f"/commit/{SHA}" in body
    assert shell.issue_body("b" * 40, diff) != body
    assert "```malicious" not in shell.issue_body(SHA, ["```malicious"])


NODE_REPORTER_TEST = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = [];
const sandbox = {
  require: name => {
    if (name !== 'node:fs') throw new Error('Unexpected module');
    return {readFileSync: () => JSON.stringify(payload.report)};
  },
  process: {env: {SHELL_DRIFT_REPORT: 'fixture.json'}},
  context: {repo: {owner: 'comphy-lab', repo: 'talks'}},
  core: {info: () => {}},
  github: {
    paginate: async () => { calls.push({kind: 'list'}); return payload.issues; },
    rest: {issues: {
      listForRepo: () => {},
      create: async args => { calls.push({kind: 'create', args}); },
      update: async args => { calls.push({kind: 'update', args}); }
    }}
  }
};
(async () => {
  let error = null;
  try {
    await vm.runInNewContext('(async () => {\n' + payload.script + '\n})()', sandbox, {timeout: 1000});
  } catch (err) {
    error = err.message;
  }
  console.log(JSON.stringify({calls, error}));
})();
"""


@pytest.mark.parametrize(
    "case,expected_write,error",
    [
        ("pass", None, False), ("fetch_error", None, True), ("invalid", None, True),
        ("new", "create", False), ("unchanged", None, False), ("changed", "update", False),
        ("closed", "update", False), ("duplicate", None, True), ("pull_request", "create", False),
    ],
)
def test_scheduled_reporter_writes_only_one_verified_changed_issue(case, expected_write, error):
    node = shutil.which("node")
    if not node:
        pytest.skip("needs Node to execute the scheduled workflow's actual issue reporter")
    workflow_path = Path(__file__).parents[2] / ".github/workflows/shell-drift.yml"
    workflow = yaml.safe_load(workflow_path.read_text())
    script = workflow["jobs"]["check"]["steps"][-1]["with"]["script"]
    body = shell.issue_body(SHA, ["index.html: navigation differs"])
    report = {"status": "drift", "upstream_commit": SHA, "issue_body": body}
    issue = {"number": 17, "body": body, "state": "open"}
    issues = []
    if case == "pass":
        report = {"status": "pass"}
    elif case == "fetch_error":
        report = {"status": "error", "error": "network unavailable"}
    elif case == "invalid":
        report["upstream_commit"] = "main"
    elif case in ("unchanged", "changed", "closed", "duplicate", "pull_request"):
        issues = [issue]
        if case == "changed":
            issue["body"] = shell.issue_body("b" * 40, ["older difference"])
        elif case == "closed":
            issue["state"] = "closed"
        elif case == "duplicate":
            issues.append({**issue, "number": 18})
        elif case == "pull_request":
            issue["pull_request"] = {}
    completed = subprocess.run(
        [node, "-e", NODE_REPORTER_TEST],
        input=json.dumps({"script": script, "report": report, "issues": issues}),
        capture_output=True, text=True, check=True, timeout=5,
    )
    result = json.loads(completed.stdout)
    assert bool(result["error"]) == error, result
    writes = [call for call in result["calls"] if call["kind"] != "list"]
    assert [call["kind"] for call in writes] == ([expected_write] if expected_write else []), result
    if writes:
        assert writes[0]["args"]["owner"] == "comphy-lab" and writes[0]["args"]["repo"] == "talks"
        assert writes[0]["args"]["body"] == body
        if expected_write == "update":
            assert writes[0]["args"]["issue_number"] == 17 and writes[0]["args"]["state"] == "open"
