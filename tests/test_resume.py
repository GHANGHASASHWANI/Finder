"""Tailored resume safety: the AI may reword, but cannot change facts or invent numbers."""
import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import resume as cv  # noqa: E402

MASTER = {
    "name": "A", "headline": "SE", "contact": [["x", None]], "summary": "Grad with 1+ year at Toddle.",
    "experience": [{"org": "Toddle", "place": "Bengaluru", "roles": [["Associate", "Jul 2026 - Present"]],
                    "bullets": ["Shipped 50+ APIs for 500+ schools.", "Cut latency by 75%.", "Built analytics for 50+ customers."]}],
    "projects": [{"name": "GreenByte - E-Waste", "dates": "2023 - 2024", "tech": "Node.js",
                  "bullets": ["Processed 2,000+ devices.", "Served 500+ bidders.", "85% accuracy."]}],
    "skills": [["Languages", "Java"]], "achievements": ["1000+ problems"],
    "education": [{"org": "CU", "place": "Mohali", "degree": "B.E., CGPA 8.78", "dates": "2022 - 2026"}],
}


class T(unittest.TestCase):
    def test_facts_are_locked(self):
        ai = copy.deepcopy(MASTER)
        ai["experience"][0]["org"] = "Google"
        ai["experience"][0]["roles"] = [["Senior Engineer", "2020 - Present"]]
        ai["experience"][0]["bullets"] = ["Shipped 900 APIs.", "Cut API latency by 75%.", "Built analytics with Kubernetes, Kafka, NIST and threat modeling for 50+ customers."]
        ai["education"] = []
        ai["projects"][0]["tech"] = "Java 17, Spring Boot"
        ai["projects"][0]["name"] = "Totally Different"
        out = cv._safe(MASTER, ai)
        e = out["experience"][0]
        self.assertEqual(e["org"], "Toddle")
        self.assertEqual(e["roles"], MASTER["experience"][0]["roles"])
        self.assertEqual(e["bullets"][0], "Shipped 50+ APIs for 500+ schools.")      # invented "900" -> master kept
        self.assertEqual(e["bullets"][1], "Cut API latency by 75%.")                     # reworded -> ok
        self.assertEqual(e["bullets"][2], "Built analytics for 50+ customers.")           # new claims at work -> master
        self.assertEqual(out["education"], MASTER["education"])
        self.assertEqual(out["projects"][0]["tech"], "Java 17, Spring Boot")
        self.assertEqual(out["projects"][0]["name"], "GreenByte - E-Waste")            # project names stay

    def test_coverage(self):
        hit, miss = cv.coverage(MASTER, ["Java", "Kubernetes"])
        self.assertEqual(hit, ["Java"])
        self.assertEqual(miss, ["Kubernetes"])



class Referral(unittest.TestCase):
    JOB = {"company": "Mastercard", "title": "Software Engineer I",
           "url": "https://mastercard.wd1.myworkdayjobs.com/x/job/Pune/SE-I_R-294544"}

    def test_fallback_without_ai(self):
        from core import referral
        out = referral.write(None, MASTER, self.JOB, "desc")
        self.assertLessEqual(len(out["note"]), 280)
        self.assertIn("R-294544", out["message"])
        self.assertIn("Resume: attached", out["message"])

    def test_invented_numbers_are_dropped(self):
        from core import referral

        class R:
            used, budget = 0, 5

            def available(self):
                return True

            def ask_json(self, *a, **k):
                return {"note": "Hi! I built 900 APIs, could you refer me for Software Engineer I?",
                        "message": "Hi, I shipped 50+ APIs for 500+ schools at Toddle. " * 3,
                        "blurb": "Ashwani shipped 50+ APIs and cut latency by 75%."}, "fake"
        out = referral.write(R(), MASTER, self.JOB, "desc")
        self.assertNotIn("900", out["note"])                      # invented -> template note kept
        self.assertIn("50+ APIs for 500+ schools", out["message"])
        self.assertEqual(out["blurb"], "Ashwani shipped 50+ APIs and cut latency by 75%.")


if __name__ == "__main__":
    unittest.main()
