import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core.filters import Matcher  # noqa: E402

P = json.load(open(os.path.join(ROOT, "profile.json")))
M = Matcher(P)
CO = {"company": "Acme", "tier": 3, "source": {"type": "greenhouse"}, "fresher_ctc_lpa": "~30-40"}


def ev(title, loc="Bengaluru, India", desc="", company=CO):
    return M.evaluate({"id": "x", "company": company["company"], "title": title, "location": loc,
                       "url": "u", "posted": None, "desc": desc}, company)[0]


class T(unittest.TestCase):
    def test_fresher_titles_pass(self):
        for t in ["Software Engineer", "SDE I", "SDE-1", "Software Development Engineer I", "Backend Engineer",
                  "Software Engineer, New Grad 2026", "Associate Software Engineer", "Member of Technical Staff",
                  "Full Stack Developer", "Quantitative Developer", "ML Engineer", "Software Engineer - University Graduate"]:
            self.assertTrue(ev(t), t)

    def test_senior_and_non_swe_blocked(self):
        for t in ["Senior Software Engineer", "Staff Engineer", "Software Engineer III", "Engineering Manager",
                  "Sales Engineer", "Solutions Architect", "Customer Support Engineer", "Principal Engineer",
                  "Tech Lead - Backend", "Product Manager", "ASIC Verification Engineer",
                  "Software Engineering Intern", "SDE III", "Software Engineer 3"]:
            self.assertFalse(ev(t), t)

    def test_google_level_ii_allowed(self):
        g = dict(CO, company="Google")
        self.assertTrue(ev("Software Engineer II, Early Career", company=g))
        self.assertFalse(ev("Software Engineer III", company=g))

    def test_interns_ignored(self):
        self.assertFalse(ev("Software Engineer Intern", desc="6-month internship with PPO opportunity"))
        self.assertFalse(ev("SDE II"))
        self.assertFalse(ev("Software Engineer 2"))
        self.assertTrue(ev("Junior Backend Developer"))

    def test_locations(self):
        self.assertTrue(ev("Software Engineer", "Gurugram, Haryana"))
        self.assertTrue(ev("Software Engineer", "Remote - APAC"))
        self.assertTrue(ev("Software Engineer", "Remote (Worldwide)"))
        self.assertFalse(ev("Software Engineer", "Remote - US"))
        self.assertFalse(ev("Software Engineer", "San Francisco (Remote)"))
        self.assertFalse(ev("Software Engineer", "Paris (Remote)"))
        self.assertTrue(ev("Software Engineer", "Home based - Worldwide"))
        self.assertTrue(ev("Software Engineer", "Remote, Global"))
        self.assertTrue(ev("Software Engineer", "Remote - India"))
        self.assertFalse(ev("Software Engineer", "London, United Kingdom"))
        self.assertFalse(ev("Software Engineer", "San Francisco, CA"))
        self.assertEqual(M.location_group("Bangalore, Karnataka"), "South India")
        self.assertEqual(M.location_group("Noida"), "North India")
        self.assertEqual(M.location_group("Pune, India"), "Central/West India")
        self.assertIsNone(M.location_group("Indianapolis, Indiana"))

    def test_experience(self):
        self.assertFalse(ev("Software Engineer", desc="You have 3+ years of professional software experience."))
        self.assertFalse(ev("Software Engineer", desc="2+ years of experience"))
        self.assertFalse(ev("Software Engineer", desc="Minimum 2 years of experience"))
        self.assertFalse(ev("Software Engineer", desc="1+ years of experience. 5 years of experience preferred."))
        self.assertFalse(ev("Data Engineer", desc="experience with: 5+ years of experience in developing on AWS"))
        self.assertFalse(ev("R&D Engineer (C++)", desc="Seniority: 5-8 years development including tools"))
        self.assertFalse(ev("Software Engineer", desc="You will mentor junior engineers"))
        self.assertTrue(ev("Software Engineer", desc="0-2 years of experience in backend development"))
        self.assertTrue(ev("Software Engineer", desc="We were founded 10 years ago."))
        self.assertTrue(ev("Software Engineer", desc="For over 25 years, we have built software."))

    def test_fit_labels(self):
        def fit(title, desc="", **kw):
            j = {"id": "x", "company": "Acme", "title": title, "location": "Pune, India", "url": "u", "desc": desc}
            j.update(kw)
            return M.evaluate(j, CO)[1]["fit"]
        self.assertEqual(fit("Software Engineer, New Grad 2026"), "fresher")
        self.assertEqual(fit("Software Engineer", "0-1 years of experience"), "fresher")
        self.assertEqual(fit("Software Engineer", "Build APIs in Node.js " * 20), "open")
        self.assertEqual(fit("Software Engineer", "1-2 years of experience"), "stretch")
        self.assertEqual(fit("Software Engineer", "", desc_missing=True), "unverified")

    def test_title_seniority_codes(self):
        for t in ["API Software Engineer - AVP", "Platforms-API Software Engineer - BA4", "Advanced Software Engr",
                  "IC Design Engineer", "Application Engineering ADAS", "Network Engineering Security"]:
            self.assertFalse(ev(t), t)
        for t in ["SDET", "QA Automation Engineer", "AI Engineer", "Data Engineer", "DevOps Engineer"]:
            self.assertTrue(ev(t), t)

    def test_resume_match(self):
        have, learn, pct = M.skills("Node.js, PostgreSQL, AWS, Kafka, PySpark, Airflow")
        self.assertIn("Node.js", have)
        self.assertIn("Airflow", learn)
        self.assertEqual(pct, 67)

    def test_page_source_unknown_location(self):
        c = dict(CO, source={"type": "page"})
        self.assertTrue(ev("Software Engineer", loc="", company=c))
        self.assertFalse(ev("Software Engineer", loc="", company=CO))


if __name__ == "__main__":
    unittest.main()
