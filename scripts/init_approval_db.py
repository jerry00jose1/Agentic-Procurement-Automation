#!/usr/bin/env python3
"""Create the approval tables in the app database."""

from procurement_agent.approvals.service import init_database
from procurement_agent.config import get_settings


def main() -> None:
    settings = get_settings()
    init_database(settings.database_url)
    print(f"approval tables ready in {settings.database_url}")


if __name__ == "__main__":
    main()
