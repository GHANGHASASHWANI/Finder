"""AI review: uses a fake GitHub Models response; checks reject/keep, caching and graceful failure."""
import io
import json
import os
import sys
import unittest
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import finder  # noqa: E402
from core import ai  # noqa: E402


def reply(obj):
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(obj)}}]}).encode())


class T(unittest.TestCase):
    def setUp(self):
        os.environ["GITHUB_TOKEN"] = "x"
        self.real = ai.urllib.request.urlopen
        self.calls = []

    def tearDown(self):
        ai.urllib.request.urlopen = self.real

    def items(self):
        d = "x" * 400
        return [({"id": "a", "title": "Data Engineer", "company": "B", "fit": "open", "priority": 7,
                  "skills_to_learn": ["Airflow"]}, d),
                ({"id": "b", "title": "Software Engineer", "company": "C", "fit": "open", "priority": 6}, d),
                ({"id": "c", "title": "SDE 1", "company": "D", "fit": "fresher", "priority": 9}, d)]

    def test_reject_and_keep(self):
        answers = {"Data Engineer": {"fit": "reject", "reason": "5+ years AWS"},
                   "Software Engineer": {"fit": "fresher", "reason": "new grads", "role_type": "Backend",
                                         "skills_to_learn": ["Go"]}}

        def fake(req, timeout=0):
            body = json.loads(req.data)
            self.calls.append(body)
            title = [l for l in body["messages"][1]["content"].splitlines() if l.startswith("Title:")][0][7:]
            return reply(answers[title])
        ai.urllib.request.urlopen = fake
        cache = {}
        r = ai.AIReviewer({"ai_max_per_run": 10}, log=lambda *a: None)
        kept = finder.ai_pass(self.items(), r, cache)
        self.assertEqual([k["id"] for k in kept], ["b", "c"])
        self.assertEqual(kept[0]["fit"], "fresher")
        self.assertEqual(kept[0]["role_type"], "Backend")
        self.assertEqual(len(self.calls), 2)          # fresher job not sent to AI
        # cached: no new calls the second time, same result
        kept = finder.ai_pass(self.items(), r, cache)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual([k["id"] for k in kept], ["b", "c"])

    def test_rate_limit_keeps_rule_result(self):
        def fake(req, timeout=0):
            raise urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)
        ai.urllib.request.urlopen = fake
        r = ai.AIReviewer({}, log=lambda *a: None)
        kept = finder.ai_pass(self.items(), r, {})
        self.assertEqual(len(kept), 3)
        self.assertTrue(r.disabled)

    def test_no_token_means_no_calls(self):
        os.environ.pop("GITHUB_TOKEN")
        r = ai.AIReviewer({}, log=lambda *a: None)
        self.assertFalse(r.available())


if __name__ == "__main__":
    unittest.main()
