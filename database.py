"""
database.py
------------------------------------------------------------------------
Data access layer for FinAgent — MySQL edition.

Handles:
  - MySQL connection management (via mysql-connector-python)
  - Schema creation (users, transactions)
  - User signup / login (password hashing via werkzeug)
  - Transaction CRUD + server-side aggregation queries for the dashboard

STRICT RULE: every transaction query in this module is scoped by user_id,
so one user can never see or affect another user's data.

------------------------------------------------------------------------
CONNECTION CONFIG
------------------------------------------------------------------------
Set these via Streamlit secrets (.streamlit/secrets.toml) OR environment
variables. Streamlit secrets take priority if present.

.streamlit/secrets.toml example:

    MYSQL_HOST = "localhost"
    MYSQL_PORT = 3306
    MYSQL_USER = "finagent_user"
    MYSQL_PASSWORD = "your_password_here"
    MYSQL_DATABASE = "finagent_db"

Environment variable equivalents:
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE

The target database (e.g. `finagent_db`) must already exist on the MySQL
server — create it once with:

    CREATE DATABASE finagent_db;
    CREATE USER 'finagent_user'@'%' IDENTIFIED BY 'your_password_here';
    GRANT ALL PRIVILEGES ON finagent_db.* TO 'finagent_user'@'%';
    FLUSH PRIVILEGES;

init_db() below will create the required TABLES inside that database
automatically — you only need to create the database + user once.
------------------------------------------------------------------------
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import mysql.connector
from mysql.connector import Error as MySQLError
from mysql.connector import pooling
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import streamlit as st
except ImportError:  # allows this module to be unit-tested without streamlit
    st = None


# ---------------------------------------------------------------------------
# Connection configuration
# ---------------------------------------------------------------------------
def _get_config_value(key: str, default=None):
    """Resolve a config value from st.secrets first, then environment variables."""
    if st is not None:
        try:
            value = st.secrets.get(key)  # type: ignore[union-attr]
            if value is not None:
                return value
        except Exception:
            pass
    return os.environ.get(key, default)


def _get_mysql_config() -> dict:
    return {
        "host": _get_config_value("MYSQL_HOST", "localhost"),
        "port": int(_get_config_value("MYSQL_PORT", 3306)),
        "user": _get_config_value("MYSQL_USER", "root"),
        "password": _get_config_value("MYSQL_PASSWORD", ""),
        "database": _get_config_value("MYSQL_DATABASE", "finagent_db"),
    }


# Connection pool avoids the overhead of opening a new TCP connection for
# every single query, and handles concurrent Streamlit sessions gracefully.
_POOL: Optional[pooling.MySQLConnectionPool] = None


def _get_pool() -> pooling.MySQLConnectionPool:
    global _POOL
    if _POOL is None:
        config = _get_mysql_config()
        try:
            _POOL = pooling.MySQLConnectionPool(
                pool_name="finagent_pool",
                pool_size=5,
                pool_reset_session=True,
                autocommit=False,
                **config,
            )
        except MySQLError as e:
            raise RuntimeError(
                f"Could not create MySQL connection pool: {e}. "
                "Check MYSQL_HOST / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DATABASE."
            ) from e
    return _POOL


@contextmanager
def get_connection():
    """
    Context manager that yields a pooled MySQL connection with a
    dictionary-returning cursor available via conn.cursor(dictionary=True).
    Commits on success, rolls back and re-raises as RuntimeError on failure,
    always releases the connection back to the pool.
    """
    conn = None
    try:
        pool = _get_pool()
        conn = pool.get_connection()
        yield conn
        conn.commit()
    except MySQLError as e:
        if conn is not None:
            conn.rollback()
        raise RuntimeError(f"Database error: {e}") from e
    finally:
        if conn is not None:
            conn.close()  # returns the connection to the pool, doesn't destroy it


def init_db() -> None:
    """Create tables if they do not already exist. Safe to call on every app start."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(150) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                amount DECIMAL(14, 2) NOT NULL,
                category VARCHAR(100) NOT NULL,
                type ENUM('income', 'expense') NOT NULL,
                description VARCHAR(255),
                date DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_transactions_user_id (user_id),
                CONSTRAINT fk_transactions_user
                    FOREIGN KEY (user_id) REFERENCES users (id)
                    ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.close()


# ---------------------------------------------------------------------------
# User authentication
# ---------------------------------------------------------------------------
def create_user(username: str, password: str) -> tuple[bool, str]:
    """Create a new user account. Returns (success, message)."""
    username = (username or "").strip()
    password = password or ""

    if not username or not password:
        return False, "Username and password cannot be empty."
    if len(username) < 3:
        return False, "Username must be at least 3 characters."
    if len(password) < 4:
        return False, "Password must be at least 4 characters."

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            if cur.fetchone() is not None:
                cur.close()
                return False, "That username is already taken."

            password_hash = generate_password_hash(password)
            cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (username, password_hash),
            )
            cur.close()
        return True, "Account created successfully."
    except RuntimeError as e:
        return False, f"Could not create account: {e}"


def verify_user(username: str, password: str) -> tuple[bool, Optional[dict], str]:
    """Verify credentials. Returns (success, user_dict_or_None, message)."""
    username = (username or "").strip()
    password = password or ""

    if not username or not password:
        return False, None, "Please enter both username and password."

    try:
        with get_connection() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT id, username, password_hash FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
            cur.close()
            if row is None:
                return False, None, "Invalid username or password."
            if not check_password_hash(row["password_hash"], password):
                return False, None, "Invalid username or password."
            return True, {"id": row["id"], "username": row["username"]}, "Login successful."
    except RuntimeError as e:
        return False, None, f"Login failed: {e}"


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------
def add_transaction(
    user_id: int, amount: float, category: str, type_: str, description: str
) -> tuple[bool, str]:
    """Insert a new transaction scoped to user_id. Returns (success, message)."""
    if type_ not in ("income", "expense"):
        return False, "Transaction type must be 'income' or 'expense'."
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return False, "Amount must be a valid number."
    if amount < 0:
        return False, "Amount cannot be negative."

    category = (category or "Other").strip() or "Other"
    description = (description or category).strip() or category
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO transactions (user_id, amount, category, type, description, date)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (user_id, amount, category, type_, description, timestamp),
            )
            cur.close()
        return True, "Transaction saved."
    except RuntimeError as e:
        return False, f"Could not save transaction: {e}"


def get_transactions(user_id: int, limit: int = 100) -> list[dict]:
    """Return the most recent transactions for user_id."""
    try:
        with get_connection() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                """
                SELECT id, amount, category, type, description, date
                FROM transactions
                WHERE user_id = %s
                ORDER BY date DESC, id DESC
                LIMIT %s
                """,
                (user_id, limit),
            )
            rows = cur.fetchall()
            cur.close()
            for row in rows:
                row["amount"] = float(row["amount"])
            return rows
    except RuntimeError:
        return []


def get_summary(user_id: int) -> dict:
    """Return total income, total expense, and balance for user_id."""
    try:
        with get_connection() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions "
                "WHERE user_id = %s AND type = 'income'",
                (user_id,),
            )
            income = float(cur.fetchone()["total"])

            cur.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions "
                "WHERE user_id = %s AND type = 'expense'",
                (user_id,),
            )
            expense = float(cur.fetchone()["total"])
            cur.close()

        return {"income": income, "expense": expense, "balance": income - expense}
    except RuntimeError:
        return {"income": 0.0, "expense": 0.0, "balance": 0.0}


def get_expense_by_category(user_id: int) -> list[dict]:
    """Server-side aggregation: total expense per category for user_id."""
    try:
        with get_connection() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                """
                SELECT category, SUM(amount) AS total
                FROM transactions
                WHERE user_id = %s AND type = 'expense'
                GROUP BY category
                ORDER BY total DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()
            cur.close()
            for row in rows:
                row["total"] = float(row["total"])
            return rows
    except RuntimeError:
        return []


def get_income_by_category(user_id: int) -> list[dict]:
    """Server-side aggregation: total income per category for user_id."""
    try:
        with get_connection() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                """
                SELECT category, SUM(amount) AS total
                FROM transactions
                WHERE user_id = %s AND type = 'income'
                GROUP BY category
                ORDER BY total DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()
            cur.close()
            for row in rows:
                row["total"] = float(row["total"])
            return rows
    except RuntimeError:
        return []
