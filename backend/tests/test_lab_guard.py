"""The lab-script database check (app/core/lab_guard.py).

It replaced a committed fragment of the production hostname. What has to hold:
a database on this machine passes, any other host is refused unless listed in
LAB_DB_HOSTS, and nothing needs production's name to keep production out.
"""

import pytest
from sqlalchemy.engine import make_url

from app.core import lab_guard
from app.core.config import settings


@pytest.mark.parametrize("url", [
    "postgresql+psycopg2://lab:lab@127.0.0.1:55432/lab",
    "postgresql+psycopg2://lab:lab@localhost/lab",
    "postgresql+psycopg2://lab@/lab",          # local socket: no host at all
    "sqlite:///:memory:",
])
def test_a_database_on_this_machine_passes(url):
    lab_guard.require_lab_database(url)


def test_any_other_host_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "lab_db_hosts", "")
    with pytest.raises(SystemExit, match="db.example.com"):
        lab_guard.require_lab_database("postgresql+psycopg2://u:p@db.example.com/prod")


def test_a_listed_experiment_host_is_allowed_and_only_that_one(monkeypatch):
    monkeypatch.setattr(settings, "lab_db_hosts", "branch.example.com, other.example.com")
    assert lab_guard.require_lab_database("postgresql://u:p@branch.example.com/x") == "branch.example.com"
    with pytest.raises(SystemExit):
        lab_guard.require_lab_database("postgresql://u:p@db.example.com/x")


def test_a_sqlalchemy_url_object_is_read_without_its_password(monkeypatch):
    """Scripts pass db.get_bind().url, whose str() masks the password."""
    monkeypatch.setattr(settings, "lab_db_hosts", "")
    with pytest.raises(SystemExit):
        lab_guard.require_lab_database(make_url("postgresql://u:secret@db.example.com/x"))
