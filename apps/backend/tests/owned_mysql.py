"""Test-only conversion of a fixture-owned MySQL receipt to an async URL."""

from scripts.migration_support import IsolatedMysql, validate_isolated_database


def owned_project_database_url(database: IsolatedMysql) -> str:
    if type(database) is not IsolatedMysql:
        raise TypeError("Project isolation tests require an IsolatedMysql ownership receipt")
    validate_isolated_database(database.url, database.project)
    return database.url.replace("mysql+pymysql:", "mysql+asyncmy:", 1)
