from pathlib import Path

import pytest

from jobbot.config import load_config
from jobbot.models import Job
from jobbot.profile import Profile

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def profile() -> Profile:
    return Profile.load(ROOT / "jobbot" / "templates" / "profile.example.yaml")


@pytest.fixture
def cfg(tmp_path):
    cfg = load_config(tmp_path / "config.yaml")  # no file: pure defaults, paths under tmp_path
    cfg["search"]["keywords"] = ["Data Analyst", "Business Intelligence Analyst"]
    cfg["notify"]["email"]["enabled"] = False
    return cfg


JD = """About the role
We are hiring a Senior Data Analyst in Dubai to own reporting for our payments business.

Requirements
- 5+ years in data analysis with SQL and Python
- Experience building Power BI or Tableau dashboards
- Familiarity with dbt, Airflow and Snowflake
- A/B Testing and Forecasting experience
- Stakeholder Management skills
"""


def make_job(**kw) -> Job:
    base = dict(source="test", external_id="1", title="Senior Data Analyst", company="Acme Payments",
                location="Dubai, United Arab Emirates", url="https://example.org/jobs/1",
                apply_url="https://example.org/jobs/1", description=JD)
    base.update(kw)
    return Job(**base)


@pytest.fixture
def job() -> Job:
    return make_job()
