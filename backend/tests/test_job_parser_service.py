from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_job_parser.db")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_APP_URL", "http://localhost:3000")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.job_parser_service import parse_job_posting_url


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class _FakeSession:
    def __init__(self, html: str) -> None:
        self._html = html
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int, headers: dict[str, str]):  # noqa: D401
        self.requested_urls.append(url)
        return _FakeResponse(self._html)


class JobParserServiceTests(unittest.TestCase):
    def test_parse_job_posting_url_uses_groq_structured_output(self) -> None:
        html = """
        <html>
          <head>
            <title>Senior Frontend Engineer | Acme</title>
            <meta property="og:description" content="Build reliable UI systems for recruiters." />
            <script type="application/ld+json">
              {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "title": "Senior Frontend Engineer",
                "description": "Build reliable UI systems for recruiters.",
                "jobLocation": {"address": {"addressLocality": "San Francisco"}},
                "baseSalary": {"value": {"currency": "USD"}}
              }
            </script>
          </head>
          <body>
            <h1>Senior Frontend Engineer</h1>
            <div>Remote - San Francisco</div>
          </body>
        </html>
        """
        fake_session = _FakeSession(html)
        groq_payload = {
            "title": "Senior Frontend Engineer",
            "description": "Build reliable UI systems for recruiters and candidates.",
            "location": "San Francisco, CA",
            "compensation": "$160k - $190k",
            "workAuthorization": "preferred",
            "remotePolicy": "remote",
            "experienceRequired": "5+ years",
        }

        with patch("app.utils.ssrf.validate_public_url", side_effect=lambda url: url), patch(
            "app.services.job_parser_service._http_session", return_value=fake_session
        ), patch(
            "app.services.job_parser_service.generate",
            return_value=groq_payload,
        ):
            parsed = parse_job_posting_url(url="https://example.com/jobs/senior-frontend-engineer")

        self.assertEqual(parsed["title"], groq_payload["title"])
        self.assertEqual(parsed["description"], groq_payload["description"])
        self.assertEqual(parsed["location"], groq_payload["location"])
        self.assertEqual(parsed["compensation"], groq_payload["compensation"])
        self.assertEqual(parsed["workAuthorization"], "preferred")
        self.assertEqual(parsed["remotePolicy"], "remote")
        self.assertEqual(parsed["experienceRequired"], "5+ years")
        self.assertEqual(fake_session.requested_urls, ["https://example.com/jobs/senior-frontend-engineer"])


if __name__ == "__main__":
    unittest.main()
