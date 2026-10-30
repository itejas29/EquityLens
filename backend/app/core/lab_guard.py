"""The check every lab script makes before it touches a database.

Lab scripts run experiments that write stocks and prices. They used to refuse
only when DATABASE_URL contained a fragment of the production host's name,
which committed an infrastructure name to a public repository and protected
against exactly one host. This check needs no names and fails closed: a lab
script runs only against a database on this machine, or against a host listed
in LAB_DB_HOSTS (a remote experiment copy, such as a Neon branch).
"""

from sqlalchemy.engine import URL, make_url

from app.core.config import settings

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def require_lab_database(url: URL | str) -> str:
    """The database host, if a lab script may use it; otherwise exit.

    A URL with no host (SQLite, or Postgres over a local socket) is on this
    machine by definition.
    """
    host = (url if isinstance(url, URL) else make_url(str(url))).host
    allowed = LOCAL_HOSTS | {h.strip() for h in settings.lab_db_hosts.split(",") if h.strip()}
    if host and host not in allowed:
        raise SystemExit(
            f"Refusing: this is a lab script and DATABASE_URL points at {host}. Lab scripts run "
            "against a database on this machine, or a host listed in LAB_DB_HOSTS.")
    return host or "local socket"
