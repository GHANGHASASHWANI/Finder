"""End-to-end run with fake feeds: checks parsing, de-duplication and first-run alert rules."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import finder  # noqa: E402
from core import sources  # noqa: E402

NOW = datetime.now(timezone.utc)
FEEDS = {
    "greenhouse": {"jobs": [
        {"id": 1, "title": "Software Engineer I", "location": {"name": "Bengaluru, India"},
         "absolute_url": "https://x/1", "first_published": NOW.isoformat(), "content": "&lt;p&gt;0-1 years of experience&lt;/p&gt;"},
        {"id": 2, "title": "Senior Software Engineer", "location": {"name": "Bengaluru"}, "absolute_url": "https://x/2"},
        {"id": 3, "title": "Software Engineer", "location": {"name": "New York"}, "absolute_url": "https://x/3"}]},
    "lever": [{"id": "a", "text": "Backend Developer", "categories": {"location": "Gurugram"},
               "hostedUrl": "https://l/a", "createdAt": (NOW - timedelta(days=10)).timestamp() * 1000,
               "descriptionPlain": "Node.js, MongoDB, AWS"}],
    "ashby": {"jobs": [{"id": "z", "title": "AI Engineer", "location": "Remote - India", "jobUrl": "https://a/z",
                        "publishedAt": NOW.isoformat(), "descriptionPlain": "LangChain RAG"}]},
    "workday": {"total": 1, "jobPostings": [{"title": "Software Engineer", "externalPath": "/job/Bangalore/SE_1",
                                             "locationsText": "Bangalore", "postedOn": "Posted Today"}]},
}


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        finder.STATE = os.path.join(self.tmp, "state")
        finder.DOCS = os.path.join(self.tmp, "docs")
        self.companies = [
            {"company": "GH", "tier": 1, "source": {"type": "greenhouse", "token": "gh"}},
            {"company": "LV", "tier": 3, "source": {"type": "lever", "slug": "lv"}},
            {"company": "AB", "tier": 4, "source": {"type": "ashby", "org": "ab"}},
            {"company": "WD", "tier": 2, "source": {"type": "workday", "tenant": "t", "wd": "wd1", "site": "s"}},
        ]
        real_load = finder.load
        finder.load = lambda p, d: self.companies if p.endswith("/companies.json") and ROOT in p else real_load(p, d)
        self._real_load = real_load

        def fake(url, **kw):
            for k, v in FEEDS.items():
                if k in url or (k == "workday" and "myworkdayjobs" in url):
                    return v
            raise ValueError(url)
        self._real_get = sources.getjson
        sources.getjson = fake
        self.sent = []
        finder.send_all = lambda jobs, log: self.sent.extend(jobs)

    def tearDown(self):
        sources.getjson = self._real_get
        finder.load = self._real_load
        shutil.rmtree(self.tmp)

    def run_finder(self):
        sys.argv = ["finder.py", "--all", "--no-browser"]
        finder.main()

    def test_first_and_second_run(self):
        self.run_finder()
        board = json.load(open(os.path.join(finder.DOCS, "jobs.json")))
        titles = sorted(j["title"] for j in board)
        self.assertEqual(titles, ["AI Engineer", "Backend Developer", "Software Engineer", "Software Engineer I"])
        # first run: alert only recent ones (lever job is 10 days old)
        self.assertEqual(sorted(j["company"] for j in self.sent), ["AB", "GH", "WD"])
        # second run with one new job -> alert just that one
        self.sent.clear()
        FEEDS["lever"].append({"id": "b", "text": "SDE-1", "categories": {"location": "Noida"},
                               "hostedUrl": "https://l/b"})
        self.run_finder()
        self.assertEqual([j["title"] for j in self.sent], ["SDE-1"])
        st = json.load(open(os.path.join(finder.DOCS, "status.json")))
        self.assertEqual(st["ok"], 4)


if __name__ == "__main__":
    unittest.main()
