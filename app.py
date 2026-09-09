"""
app.py
------------------------------------------------------------------------
FinAgent: Autonomous AI Finance Assistant
Streamlit entry point.

==========================================================================
SETUP INSTRUCTIONS
==========================================================================
1. Install dependencies:
       pip install streamlit groq pandas plotly werkzeug

2. Provide your Groq API key (get one at https://console.groq.com/keys)
   using ONE of the following methods:

   a) Streamlit secrets (recommended) — create a file at
      .streamlit/secrets.toml next to this file with:

          GROQ_API_KEY = "your_groq_api_key_here"

   b) Environment variable:

          export GROQ_API_KEY="your_groq_api_key_here"    # Linux / macOS
          set GROQ_API_KEY=your_groq_api_key_here          # Windows (cmd)
          $env:GROQ_API_KEY="your_groq_api_key_here"       # Windows (PowerShell)

3. Run the app:
       streamlit run app.py

The SQLite database file `finagent.db` is created automatically in the
same directory the first time the app runs — no manual setup needed.
==========================================================================
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import ai_agent
import database as db

# ---------------------------------------------------------------------------
# Page configuration (must be the first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="FinAgent | AI Finance Assistant",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Database initialization — safe to run on every app start/rerun
# ---------------------------------------------------------------------------
try:
    db.init_db()
except Exception as e:  # noqa: BLE001 - a broken DB is fatal to the whole app
    st.error(f"Fatal error initializing the database: {e}")
    st.stop()


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
def init_session_state() -> None:
    defaults = {
        "logged_in": False,
        "user_id": None,
        "username": None,
        "page": "Chat Assistant",
        "chat_history": [],  # list of {"role", "content", "data"?}
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_session_state()


# ---------------------------------------------------------------------------
# Authentication screen (login / signup)
# ---------------------------------------------------------------------------
def show_auth_screen() -> None:
    st.title("💰 FinAgent")
    st.caption("Your autonomous AI-powered personal finance assistant")

    tab_login, tab_signup = st.tabs(["🔑 Login", "🆕 Sign Up"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("Username", key="login_username")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Log In", use_container_width=True)

        if submitted:
            if not username or not password:
                st.warning("Please enter both username and password.")
            else:
                ok, user, msg = db.verify_user(username, password)
                if ok and user:
                    st.session_state.logged_in = True
                    st.session_state.user_id = user["id"]
                    st.session_state.username = user["username"]
                    st.session_state.chat_history = []
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    with tab_signup:
        with st.form("signup_form", clear_on_submit=True):
            new_username = st.text_input("Choose a username", key="signup_username")
            new_password = st.text_input("Choose a password", type="password", key="signup_password")
            confirm_password = st.text_input(
                "Confirm password", type="password", key="signup_confirm"
            )
            submitted = st.form_submit_button("Create Account", use_container_width=True)

        if submitted:
            if not new_username or not new_password:
                st.warning("Please fill in all fields.")
            elif new_password != confirm_password:
                st.warning("Passwords do not match.")
            else:
                ok, msg = db.create_user(new_username, new_password)
                if ok:
                    st.success(f"{msg} Please log in from the Login tab.")
                else:
                    st.error(msg)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def show_sidebar() -> None:
    with st.sidebar:
        st.markdown(f"### 👋 Hello, {st.session_state.username}")
        st.divider()

        page = st.radio(
            "Navigate",
            options=["Chat Assistant", "Dashboard"],
            index=["Chat Assistant", "Dashboard"].index(st.session_state.page),
            label_visibility="collapsed",
        )
        st.session_state.page = page

        st.divider()
        if st.button("🚪 Logout", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.user_id = None
            st.session_state.username = None
            st.session_state.chat_history = []
            st.session_state.page = "Chat Assistant"
            st.rerun()


# ---------------------------------------------------------------------------
# Chat Assistant page
# ---------------------------------------------------------------------------
def show_chat_page() -> None:
    st.header("💬 Chat Assistant")
    st.caption(
        "Tell me about your income or expenses in plain English — e.g. "
        "\"I spent 500 on Zomato today\" or \"Got 10000 as salary\"."
    )

    # Replay chat history
    for entry in st.session_state.chat_history:
        with st.chat_message(entry["role"]):
            st.markdown(entry["content"])
            for data in entry.get("data_list", []):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Amount", f"₹{data['amount']:,.2f}")
                c2.metric("Category", data["category"])
                c3.metric("Type", data["type"].capitalize())
                c4.metric("Description", data["description"])

    user_input = st.chat_input("Type a transaction, e.g. 'Spent 200 on groceries'")

    if not user_input:
        return

    st.session_state.chat_history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Analyzing your transaction..."):
            try:
                parsed_list, error = ai_agent.parse_transactions(user_input)
            except Exception as e:  # noqa: BLE001 - never let a stray error crash the UI
                parsed_list, error = None, f"Unexpected error: {e}"

        if error or not parsed_list:
            error_msg = f"⚠️ I couldn't process that: {error}"
            st.error(error_msg)
            st.session_state.chat_history.append({"role": "assistant", "content": error_msg})
            return

        saved = []
        failures = []
        for parsed in parsed_list:
            ok, msg = db.add_transaction(
                user_id=st.session_state.user_id,
                amount=parsed["amount"],
                category=parsed["category"],
                type_=parsed["type"],
                description=parsed["description"],
            )
            if ok:
                saved.append(parsed)
            else:
                failures.append((parsed, msg))

        if not saved:
            fail_msg = f"⚠️ Failed to save transaction(s): {failures[0][1] if failures else 'unknown error'}"
            st.error(fail_msg)
            st.session_state.chat_history.append({"role": "assistant", "content": fail_msg})
            return

        count_label = "transaction" if len(saved) == 1 else f"{len(saved)} transactions"
        success_msg = f"✅ Saved {count_label}. Here's what I recorded:"
        st.success(success_msg)
        for parsed in saved:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Amount", f"₹{parsed['amount']:,.2f}")
            c2.metric("Category", parsed["category"])
            c3.metric("Type", parsed["type"].capitalize())
            c4.metric("Description", parsed["description"])

        if failures:
            st.warning(f"⚠️ {len(failures)} item(s) could not be saved: {failures[0][1]}")

        st.session_state.chat_history.append(
            {"role": "assistant", "content": success_msg, "data_list": saved}
        )


# ---------------------------------------------------------------------------
# Dashboard page
# ---------------------------------------------------------------------------
def show_dashboard_page() -> None:
    st.header("📊 Financial Dashboard")

    user_id = st.session_state.user_id
    summary = db.get_summary(user_id)

    col1, col2, col3 = st.columns(3)
    col1.metric("💵 Total Income", f"₹{summary['income']:,.2f}")
    col2.metric("💸 Total Expense", f"₹{summary['expense']:,.2f}")
    col3.metric("🏦 Current Balance", f"₹{summary['balance']:,.2f}")

    st.divider()

    expense_data = db.get_expense_by_category(user_id)
    income_data = db.get_income_by_category(user_id)

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.subheader("Expense Breakdown")
        if expense_data:
            df_exp = pd.DataFrame(expense_data)
            fig = px.pie(
                df_exp, names="category", values="total",
                hole=0.4, title="Expenses by Category",
            )
            fig.update_traces(textposition="inside", textinfo="percent+label")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No expense data yet. Add transactions in the Chat Assistant.")

    with chart_col2:
        st.subheader("Income Breakdown")
        if income_data:
            df_inc = pd.DataFrame(income_data)
            fig2 = px.pie(
                df_inc, names="category", values="total",
                hole=0.4, title="Income by Category",
            )
            fig2.update_traces(textposition="inside", textinfo="percent+label")
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No income data yet. Add transactions in the Chat Assistant.")

    st.divider()
    st.subheader("Income vs Expense by Category")

    df_exp2 = pd.DataFrame(expense_data)
    df_inc2 = pd.DataFrame(income_data)
    if not df_exp2.empty:
        df_exp2["type"] = "Expense"
    if not df_inc2.empty:
        df_inc2["type"] = "Income"

    frames = [df for df in (df_exp2, df_inc2) if not df.empty]
    if frames:
        combined = pd.concat(frames, ignore_index=True)
        fig3 = px.bar(
            combined, x="category", y="total", color="type",
            barmode="group", title="Category Comparison",
            labels={"total": "Amount (₹)", "category": "Category"},
        )
        st.plotly_chart(fig3, use_container_width=True)
    else:
        st.info("Add transactions to see comparisons here.")

    st.divider()
    st.subheader("💡 AI Financial Insights")

    if st.button("🔮 Get AI Recommendations", use_container_width=False):
        with st.spinner("Analyzing your spending patterns..."):
            try:
                advice, error = ai_agent.get_financial_advice(
                    summary, expense_data, income_data
                )
            except Exception as e:  # noqa: BLE001 - never let a stray error crash the UI
                advice, error = None, f"Unexpected error: {e}"

        if error or not advice:
            st.warning(f"⚠️ Couldn't generate recommendations: {error}")
        else:
            st.info(advice)

    st.divider()
    st.subheader("Recent Transactions")

    transactions = db.get_transactions(user_id, limit=50)
    if transactions:
        df_tx = pd.DataFrame(transactions)
        df_tx = df_tx.rename(
            columns={
                "amount": "Amount",
                "category": "Category",
                "type": "Type",
                "description": "Description",
                "date": "Date",
            }
        )
        df_tx["Type"] = df_tx["Type"].str.capitalize()
        st.dataframe(
            df_tx[["Date", "Type", "Category", "Amount", "Description"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No transactions recorded yet.")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
def main() -> None:
    if not st.session_state.logged_in:
        show_auth_screen()
        return

    show_sidebar()

    if st.session_state.page == "Chat Assistant":
        show_chat_page()
    elif st.session_state.page == "Dashboard":
        show_dashboard_page()


if __name__ == "__main__":
    main()
