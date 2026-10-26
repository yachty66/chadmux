#!/usr/bin/env python3
"""scripts/ci/gh_app_token.sh: publish-path gh calls go out as the release App, github.token as fallback.

Every workflow's github.token is one actor, so a burst elsewhere refused the nightly publish
(2026-10-26, run 40298250989: HTTP 403 with no x-ratelimit-* headers while core had 4899 calls
left). These tests drive the wrapper against a fake gh, check publish-release-assets.py's
fallback, and check that nightly.yml mints the App token and installs the wrapper before
gh_budget_wait.sh (which then wraps it and keeps its wait) in every publish job whose
environment holds the App's credentials.
"""

from __future__ import annotations

import importlib.util
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci" / "gh_app_token.sh"
BUDGET = ROOT / "scripts" / "ci" / "gh_budget_wait.sh"
NIGHTLY = ROOT / ".github" / "workflows" / "nightly.yml"
APP, FALLBACK = "ghs_app", "ghs_actions"

# The fake gh logs the token of each call. A token with refusals left in $FAKE_STATE/<token> fails
# with that token's error ($FAKE_STATE/<token>.err); otherwise it prints its arguments and stdin.
FAKE_GH = r"""#!/bin/bash
state="$FAKE_STATE"
if [ "$1 $2" = "api rate_limit" ]; then echo 0; exit 0; fi
echo "${GH_TOKEN:-none} $*" >> "$state/log"
left="$(cat "$state/${GH_TOKEN:-none}" 2>/dev/null || echo 0)"
if [ "$left" -gt 0 ]; then
  echo $((left - 1)) > "$state/${GH_TOKEN:-none}"
  cat "$state/${GH_TOKEN:-none}.err" >&2
  exit 1
fi
echo "ok $*"
if [ -n "${READS_STDIN:-}" ]; then cat; fi
"""


class Harness:
    def __init__(self, app_token: str = APP, budget: bool = False, **env: str) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.state = base / "state"
        self.state.mkdir()
        real = base / "real"
        real.mkdir()
        fake = real / "gh"
        fake.write_text(FAKE_GH)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.runner_temp = base / "runner-temp"
        self.runner_temp.mkdir()
        self.github_path = base / "github_path"
        self.env = {
            "PATH": f"{real}:/usr/bin:/bin",
            "RUNNER_TEMP": str(self.runner_temp),
            "GITHUB_PATH": str(self.github_path),
            "FAKE_STATE": str(self.state),
            "GH_BUDGET_WAIT_MAX_SECONDS": "2",
            "GH_TOKEN": FALLBACK,
            **env,
        }
        install_env = {**self.env, "GH_APP_TOKEN": app_token, "GH_FALLBACK_TOKEN": FALLBACK}
        self.installed = subprocess.run(["bash", str(SCRIPT), "--install"], env=install_env,
                                        capture_output=True, text=True, check=True)
        self.shim = real / "gh"
        if self.github_path.exists():
            self.shim = Path(self.github_path.read_text().strip().splitlines()[-1]) / "gh"
        if budget:  # gh_budget_wait.sh installs after this wrapper, as in nightly.yml, and wraps it
            self.env["PATH"] = f"{self.shim.parent}:{self.env['PATH']}"
            subprocess.run(["bash", str(BUDGET), "--install"], env=self.env, capture_output=True, text=True, check=True)
            self.shim = Path(self.github_path.read_text().strip().splitlines()[-1]) / "gh"

    def refuse(self, token: str, times: int, error: str) -> None:
        (self.state / token).write_text(str(times))
        (self.state / f"{token}.err").write_text(error + "\n")

    def gh(self, *args: str, stdin: str | None = None, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self.shim), *args], env={**self.env, **env}, input=stdin,
                              capture_output=True, text=True, timeout=30)

    def calls(self) -> list[str]:
        log = self.state / "log"
        return log.read_text().splitlines() if log.exists() else []

    def waited(self) -> int:
        ledger = self.runner_temp / "gh-budget-wait" / "waited"
        return int(ledger.read_text()) if ledger.exists() else 0


SECONDARY = "HTTP 403: API rate limit exceeded for installation. (https://api.github.com/repos/o/r/releases)"


class WrapperTests(unittest.TestCase):
    def harness(self, **kwargs) -> Harness:
        h = Harness(**kwargs)
        self.addCleanup(h.tmp.cleanup)
        return h

    def test_a_github_token_call_goes_out_as_the_app(self) -> None:
        h = self.harness()
        result = h.gh("release", "upload", "nightly", "a.dmg")
        self.assertEqual(result.stdout, "ok release upload nightly a.dmg\n")
        self.assertEqual(h.calls(), [f"{APP} release upload nightly a.dmg"])
        token = h.runner_temp / "gh-app-token" / "token"
        self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o600)
        self.assertNotIn(FALLBACK, "".join(p.read_text() for p in (h.runner_temp / "gh-app-token").rglob("*") if p.is_file()))

    def test_another_token_is_left_alone(self) -> None:
        h = self.harness()
        h.gh("api", "x", GH_TOKEN="ghs_release_tag")
        h.gh("api", "y", GH_TOKEN="")
        self.assertEqual(h.calls(), ["ghs_release_tag api x", "none api y"])

    def test_a_refused_app_token_falls_back_to_github_token(self) -> None:
        for error in (SECONDARY, "HTTP 403: Resource not accessible by integration", "HTTP 429: Too Many Requests"):
            h = self.harness()
            h.refuse(APP, 1, error)
            result = h.gh("api", "repos/o/r/actions/runs")
            self.assertEqual(result.returncode, 0, (error, result))
            self.assertEqual(result.stdout, "ok api repos/o/r/actions/runs\n")
            self.assertEqual(h.calls(), [f"{APP} api repos/o/r/actions/runs", f"{FALLBACK} api repos/o/r/actions/runs"])
            self.assertEqual(h.waited(), 0)

    def test_an_area_the_app_cannot_reach_goes_to_github_token_for_the_job(self) -> None:
        h = self.harness()
        h.refuse(APP, 1, "HTTP 403: Resource not accessible by integration (https://api.github.com/x)")
        self.assertEqual(h.gh("api", "repos/o/r/actions/runs/1/artifacts").returncode, 0)
        self.assertEqual(h.gh("api", "--paginate", "/repos/o/r/actions/artifacts?name=x").returncode, 0)
        self.assertEqual(h.gh("api", "repos/o/r/releases/tags/nightly").returncode, 0)
        self.assertEqual(h.calls(), [f"{APP} api repos/o/r/actions/runs/1/artifacts",
                                     f"{FALLBACK} api repos/o/r/actions/runs/1/artifacts",
                                     f"{FALLBACK} api --paginate /repos/o/r/actions/artifacts?name=x",
                                     f"{APP} api repos/o/r/releases/tags/nightly"])

    def test_an_expired_app_token_is_dropped_for_the_job(self) -> None:
        h = self.harness()
        h.refuse(APP, 1, "HTTP 401: Bad credentials (https://api.github.com/x)")
        self.assertEqual(h.gh("api", "x").returncode, 0)
        self.assertEqual(h.gh("api", "y").returncode, 0)
        self.assertEqual(h.calls(), [f"{APP} api x", f"{FALLBACK} api x", f"{FALLBACK} api y"])

    def test_another_app_failure_is_returned_as_is(self) -> None:
        h = self.harness()
        h.refuse(APP, 1, "HTTP 404: Not Found")
        result = h.gh("release", "view", "nightly")
        self.assertEqual(result.returncode, 1)
        self.assertIn("HTTP 404", result.stderr)
        self.assertEqual(h.calls(), [f"{APP} release view nightly"])

    def test_both_refused_backs_off_then_retries_both(self) -> None:
        h = self.harness(GH_BUDGET_WAIT_MAX_SECONDS="1")
        h.refuse(APP, 1, SECONDARY)
        h.refuse(FALLBACK, 1, SECONDARY)
        result = h.gh("release", "upload", "nightly", "a.dmg")
        self.assertEqual(result.returncode, 0, result)
        self.assertEqual(h.calls(), [f"{APP} release upload nightly a.dmg", f"{FALLBACK} release upload nightly a.dmg",
                                     f"{APP} release upload nightly a.dmg"])
        self.assertIn("backing off 1s", result.stderr)  # 60 s, capped by the job's total
        self.assertEqual(h.waited(), 1)

    def test_backoff_is_exponential_from_60s_and_honours_retry_after(self) -> None:
        text = SCRIPT.read_text()
        self.assertIn("want=$((60 * (1 << (try - 1))))", text)
        h = self.harness(GH_BUDGET_WAIT_MAX_SECONDS="100")
        h.refuse(APP, 1, SECONDARY)
        h.refuse(FALLBACK, 1, SECONDARY + "\nretry-after: 1")
        result = h.gh("api", "-i", "x")
        self.assertEqual(result.returncode, 0, result)
        self.assertIn("backing off 1s", result.stderr)

    def test_a_permission_refusal_of_github_token_does_not_wait(self) -> None:
        h = self.harness()
        h.refuse(APP, 1, "HTTP 403: Resource not accessible by integration")
        h.refuse(FALLBACK, 1, "HTTP 403: Resource not accessible by integration")
        self.assertEqual(h.gh("api", "x").returncode, 1)
        self.assertEqual(len(h.calls()), 2)
        self.assertEqual(h.waited(), 0)

    def test_the_budget_wrapper_wraps_this_one_and_keeps_its_wait(self) -> None:
        h = self.harness(budget=True, GH_BUDGET_WAIT_MAX_SECONDS="1", GH_APP_TOKEN_ATTEMPTS="1")
        h.refuse(APP, 2, SECONDARY)
        h.refuse(FALLBACK, 1, SECONDARY)
        result = h.gh("api", "x")
        self.assertEqual(result.returncode, 0, result)
        self.assertIn("GitHub API budget", result.stderr)  # gh_budget_wait.sh waited, then retried
        self.assertEqual(h.calls(), [f"{APP} api x", f"{FALLBACK} api x", f"{APP} api x", f"{FALLBACK} api x"])

    def test_stdin_is_replayed_for_the_fallback(self) -> None:
        h = self.harness(READS_STDIN="1")
        h.refuse(APP, 1, SECONDARY)
        result = h.gh("api", "x", "--input", "-", stdin='{"a": 1}')
        self.assertEqual(result.stdout, 'ok api x --input -\n{"a": 1}')

    def test_no_app_token_installs_nothing(self) -> None:
        h = self.harness(app_token="")
        self.assertIn("::warning", h.installed.stdout)
        self.assertFalse(h.github_path.exists())


spec = importlib.util.spec_from_file_location("publisher_app_token", ROOT / "scripts/ci/publish-release-assets.py")
publisher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = publisher
spec.loader.exec_module(publisher)


class PublisherTests(unittest.TestCase):
    def client(self, failures: dict[str, list[int]]):
        client = publisher.GitHub("o/r", APP, fallback=FALLBACK)
        seen = []

        def fake(method, url, **kwargs):
            seen.append(client.token)
            statuses = failures.get(client.token, [])
            if statuses:
                raise publisher.RequestError(f"{method} HTTP {statuses[0]}", status=statuses.pop(0))
            return {"ok": True}
        client._request = fake
        return client, seen

    def test_a_refused_app_upload_is_sent_again_with_github_token(self) -> None:
        for status in (401, 403, 429):
            client, seen = self.client({APP: [status]})
            self.assertEqual(client.request("POST", "https://uploads.github.com/x", body=b"a"), {"ok": True})
            self.assertEqual(seen, [APP, FALLBACK])
            client.request("GET", "https://api.github.com/y")
            self.assertEqual(seen, [APP, FALLBACK, FALLBACK])

    def test_another_failure_does_not_switch(self) -> None:
        client, seen = self.client({APP: [422]})
        with self.assertRaises(publisher.RequestError):
            client.request("POST", "https://api.github.com/x", body=b"a")
        self.assertEqual(seen, [APP])

    def test_main_reads_the_app_token_first(self) -> None:
        text = (ROOT / "scripts/ci/publish-release-assets.py").read_text()
        self.assertIn('client = GitHub(args.repo, app or token, fallback=token if app else "")', text)


# Publish jobs whose environment (release or release-next) holds CMUX_RELEASE_APP_ID/_KEY.
APP_JOBS = {
    "recover-nightly-next-notarization": "read", "publish-nightly": "write", "keep-published-dsyms": "read",
    "legacy-identity-target": "read", "sign-nightly-identity-legacy": "read",
    "publish-nightly-identity-legacy": "write", "generate-nightly-deltas": "read",
    "republish-nightly-deltas": "write", "publish-release-notes": "read",
}
APP_INSTALL = re.compile(r"bash (\.publish-tooling/)?scripts/ci/gh_app_token\.sh --install")
BUDGET_INSTALL = re.compile(r"bash (\.publish-tooling/)?scripts/ci/gh_budget_wait\.sh --install")


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import yaml
        cls.jobs = yaml.safe_load(NIGHTLY.read_text(encoding="utf-8"))["jobs"]

    def test_publish_jobs_mint_the_app_token_and_install_before_the_budget_wait(self) -> None:
        for name, contents in APP_JOBS.items():
            job, steps = self.jobs[name], self.jobs[name]["steps"]
            self.assertIn(job.get("environment"), ("release-next", "${{ needs.decide.outputs.environment }}"), name)
            mint = [i for i, s in enumerate(steps) if s.get("id") == "gh-app-token"]
            app = [i for i, s in enumerate(steps) if APP_INSTALL.search(str(s.get("run", "")))]
            budget = [i for i, s in enumerate(steps) if BUDGET_INSTALL.search(str(s.get("run", "")))]
            self.assertEqual((len(mint), len(app), len(budget)), (1, 1, 1), name)
            # Mint, then this wrapper, then gh_budget_wait.sh, which wraps it and keeps its wait.
            self.assertEqual((app[0], budget[0]), (mint[0] + 1, mint[0] + 2), name)
            step = steps[mint[0]]
            self.assertIn("actions/create-github-app-token@", step["uses"])
            self.assertTrue(step.get("continue-on-error"), name)  # a failed mint keeps github.token
            self.assertEqual(step["with"]["app-id"], "${{ secrets.CMUX_RELEASE_APP_ID }}")
            self.assertEqual(step["with"]["private-key"], "${{ secrets.CMUX_RELEASE_APP_KEY }}")
            self.assertEqual(step["with"]["permission-contents"], contents, name)
            self.assertEqual({k for k in step["with"] if k.startswith("permission-")}, {"permission-contents"}, name)
            self.assertEqual(steps[app[0]]["env"], {"GH_APP_TOKEN": "${{ steps.gh-app-token.outputs.token }}",
                                                    "GH_FALLBACK_TOKEN": "${{ github.token }}"}, name)
            # Every step that sends github.token runs after the wrapper is on PATH.
            for i, s in enumerate(steps):
                if i != app[0] and "github.token" in str(s.get("env", {})):
                    self.assertGreater(i, budget[0], (name, s.get("name")))

    def test_release_asset_uploads_take_the_app_token_first(self) -> None:
        for name in ("publish-nightly", "republish-nightly-deltas"):
            for step in self.jobs[name]["steps"]:
                if "publish-release-assets.py" in str(step.get("run", "")):
                    self.assertEqual(step["env"]["GH_APP_TOKEN"], "${{ steps.gh-app-token.outputs.token }}", step["name"])
                    self.assertEqual(step["env"]["GH_TOKEN"], "${{ github.token }}", step["name"])

    def test_legacy_target_runs_only_where_its_environment_admits(self) -> None:
        job = self.jobs["legacy-identity-target"]
        self.assertEqual(job["environment"], "release-next")
        self.assertIn("github.ref == 'refs/heads/nightly-next'", job["if"])
        self.assertIn("needs.decide.outputs.environment == 'release-next'", job["if"])


if __name__ == "__main__":
    unittest.main()
