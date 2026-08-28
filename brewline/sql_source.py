"""Azure SQL Database access, exposed as an MCP data source.

Authenticates with Microsoft Entra ID (`Authentication=ActiveDirectoryDefault`)
rather than a SQL login/password -- this project's brewline-sql-server has
Azure AD-only authentication enabled (no SQL logins exist). Locally this
resolves via the logged-in `az` CLI session; running under a managed
identity (e.g. in Azure) would resolve the same way with no code change,
provided that identity is granted a database role.
"""

from __future__ import annotations

import os

import mssql_python
from dotenv import load_dotenv

load_dotenv()

AZURE_SQL_SERVER = os.environ.get("AZURE_SQL_SERVER", "")
AZURE_SQL_DATABASE = os.environ.get("AZURE_SQL_DATABASE", "")

CONFIGURED = bool(AZURE_SQL_SERVER and AZURE_SQL_DATABASE)


def _connect():
    if not CONFIGURED:
        raise RuntimeError("AZURE_SQL_SERVER / AZURE_SQL_DATABASE are not set in .env")
    conn_str = (
        f"Server={AZURE_SQL_SERVER},1433;"
        f"Database={AZURE_SQL_DATABASE};"
        "Encrypt=yes;TrustServerCertificate=no;"
        "Authentication=ActiveDirectoryDefault;"
    )
    return mssql_python.connect(conn_str)


def list_tables() -> list[dict]:
    """List user tables (schema + name) in the database."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME"
        )
        return [{"schema": row[0], "table": row[1]} for row in cur.fetchall()]
    finally:
        conn.close()


def run_query(sql: str, max_rows: int = 200) -> dict:
    """Run a read-only query and return its columns and rows.

    Only a single SELECT (optionally with a leading WITH ... clause) is
    allowed -- this is a read-only data source, not a general SQL execution
    tool, so mutations and statement-stacking are rejected up front.
    """
    stripped = sql.strip().rstrip(";").strip()
    first_word = stripped.split(None, 1)[0].upper() if stripped else ""
    if first_word not in ("SELECT", "WITH"):
        raise ValueError("Only SELECT queries are allowed")
    if ";" in stripped:
        raise ValueError("Only a single statement is allowed (no ';' inside the query)")

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(stripped)
        columns = [col[0] for col in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows)
        return {
            "columns": columns,
            "rows": [list(row) for row in rows],
            "truncated": len(rows) == max_rows,
        }
    finally:
        conn.close()
