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
import re

import mssql_python
from dotenv import load_dotenv

load_dotenv()

AZURE_SQL_SERVER = os.environ.get("AZURE_SQL_SERVER", "")
AZURE_SQL_DATABASE = os.environ.get("AZURE_SQL_DATABASE", "")

CONFIGURED = bool(AZURE_SQL_SERVER and AZURE_SQL_DATABASE)

# KPI names come from BrewLine.py's kpis() dict keys (customers_served, cmax,
# utilization, throughput, avg_wait, max_wait, p95_wait, avg_queue_length,
# max_queue_length, avg_wip, max_wip, avg_sojourn) and are used to build a
# table name below -- validated against this pattern since SQL identifiers
# can't be parameterized like values can.
_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


def _kpi_table_name(kpi_name: str) -> str:
    if not _SAFE_IDENTIFIER.match(kpi_name):
        raise ValueError(f"Unsafe KPI name for a table identifier: {kpi_name!r}")
    return f"kpi_{kpi_name}"


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


def ensure_schema() -> None:
    """Create the Inputs / run_validation / run_barista_utilization tables if missing.

    `Inputs` was already provisioned in brewline-sql-db ahead of this code
    (InputID identity PK, decimal(10,4) config columns, CreatedAt defaulting
    to getdate()) -- this recreates that exact shape so the module is still
    self-provisioning against a fresh database, without altering the live
    table. Each KPI gets its own table (kpi_utilization, kpi_throughput,
    ...) rather than one shared table -- those are created lazily by
    `_ensure_kpi_table` the first time that KPI is inserted, since the KPI
    set lives in BrewLine.py's kpis() and this module shouldn't hardcode a
    second copy of it.
    """
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("""
            IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'Inputs')
            CREATE TABLE Inputs (
                InputID INT IDENTITY(1,1) PRIMARY KEY,
                arrival_rate_per_min DECIMAL(10,4),
                mean_interarrival_min DECIMAL(10,4),
                num_baristas INT,
                mean_service_time_min DECIMAL(10,4),
                horizon_min DECIMAL(10,4),
                replications INT,
                master_seed INT,
                offered_load_rho DECIMAL(10,4),
                stable BIT,
                CreatedAt DATETIME DEFAULT (getdate())
            )
        """)
        cur.execute("""
            IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'run_validation')
            CREATE TABLE run_validation (
                input_id INT NOT NULL PRIMARY KEY REFERENCES Inputs(InputID),
                littles_law_max_abs_error FLOAT NOT NULL
            )
        """)
        cur.execute("""
            IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'run_barista_utilization')
            CREATE TABLE run_barista_utilization (
                input_id INT NOT NULL REFERENCES Inputs(InputID),
                barista_index INT NOT NULL,
                mean FLOAT NOT NULL,
                stdev FLOAT NOT NULL,
                ci95_low FLOAT NOT NULL,
                ci95_high FLOAT NOT NULL,
                min_value FLOAT NOT NULL,
                max_value FLOAT NOT NULL,
                PRIMARY KEY (input_id, barista_index)
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _ensure_kpi_table(cur, kpi_name: str) -> str:
    """Create this KPI's table (e.g. kpi_utilization) if missing. Returns its name."""
    table = _kpi_table_name(kpi_name)
    cur.execute(f"""
        IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = '{table}')
        CREATE TABLE {table} (
            input_id INT NOT NULL PRIMARY KEY REFERENCES Inputs(InputID),
            mean FLOAT NOT NULL,
            stdev FLOAT NOT NULL,
            ci95_low FLOAT NOT NULL,
            ci95_high FLOAT NOT NULL,
            min_value FLOAT NOT NULL,
            max_value FLOAT NOT NULL
        )
    """)
    return table


def insert_run(results: dict) -> int:
    """Insert one BrewLine run: config into `Inputs`, one row per KPI into
    that KPI's own table (kpi_utilization, kpi_throughput, ...), the
    Little's-law check into `run_validation`, and per-barista utilization
    into `run_barista_utilization`.

    Creates the schema (and any new KPI's table) on first use. Returns the
    new `InputID`, which can be used to correlate a SQL row with the
    matching vector-index chunks (see `brewline/search_index.py`) generated
    from the same results dict.
    """
    ensure_schema()
    cfg = results["config"]

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO Inputs (
                arrival_rate_per_min, mean_interarrival_min, num_baristas,
                mean_service_time_min, horizon_min, replications, master_seed,
                offered_load_rho, stable
            )
            OUTPUT INSERTED.InputID
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            cfg["arrival_rate_per_min"],
            cfg["mean_interarrival_min"],
            cfg["num_baristas"],
            cfg["mean_service_time_min"],
            cfg["horizon_min"],
            cfg["replications"],
            cfg["master_seed"],
            cfg["offered_load_rho"],
            cfg["stable"],
        )
        input_id = cur.fetchone()[0]

        cur.execute(
            """
            INSERT INTO run_validation (input_id, littles_law_max_abs_error)
            VALUES (?, ?)
            """,
            input_id, results["validation"]["littles_law_max_abs_error"],
        )

        for kpi_name, stats in results.get("summary", {}).items():
            table = _ensure_kpi_table(cur, kpi_name)
            cur.execute(
                f"""
                INSERT INTO {table} (
                    input_id, mean, stdev, ci95_low, ci95_high, min_value, max_value
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                input_id, stats["mean"], stats["stdev"],
                stats["ci95_low"], stats["ci95_high"], stats["min"], stats["max"],
            )

        resource_kpis = results.get("resource_kpis") or {}
        for i, stats in enumerate(resource_kpis.get("utilization_by_barista", [])):
            cur.execute(
                """
                INSERT INTO run_barista_utilization (
                    input_id, barista_index, mean, stdev, ci95_low, ci95_high, min_value, max_value
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                input_id, i, stats["mean"], stats["stdev"],
                stats["ci95_low"], stats["ci95_high"], stats["min"], stats["max"],
            )

        conn.commit()
        return input_id
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
