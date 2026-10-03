[README.md](https://github.com/user-attachments/files/33004856/README.md)
# 💰 FinAgent — AI-Powered Conversational Finance Assistant

FinAgent is a full-stack personal finance management application that lets users log income and expenses using natural language instead of manual forms. A Large Language Model (via the Groq API) extracts structured transaction data from everyday text, which is validated, securely stored in MySQL, and visualized through an interactive analytics dashboard — complete with AI-generated personalized financial recommendations.

> **Example:** Type *"spent 500 on groceries today"* or *"got 10000 as salary"* — FinAgent automatically understands, categorizes, and records it.

---

## ✨ Features

- 🗣️ **Natural Language Transaction Logging** — no forms, no dropdowns, just plain text (English/Hinglish)
- 🤖 **AI-Powered Extraction** — Groq LLM converts unstructured text into structured transaction data
- 🔁 **Self-Correcting Retry Pipeline** — automatically retries and corrects malformed AI responses (up to 2 retries)
- 🧩 **Multi-Transaction Handling** — correctly splits messages describing multiple amounts (e.g. *"20 and 40 spent on food"*) into separate records instead of averaging them
- 🔐 **Secure Authentication** — passwords hashed with PBKDF2-SHA256 (Werkzeug), never stored in plain text
- 🛡️ **Strict Multi-User Data Isolation** — every database query is scoped to the logged-in user
- 📊 **Interactive Dashboard** — income/expense summaries, category breakdowns via Plotly pie & bar charts
- 💡 **AI Financial Recommendations** — personalized, data-driven spending insights generated on demand

---

## 🏗️ Architecture

FinAgent follows a clean 3-layer architecture:

```
┌─────────────────────┐
│   app.py (UI)        │  Streamlit — chat interface, dashboard, auth screens
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│ ai_agent.py (AI)      │  Groq LLM integration, prompt engineering, validation & retry
└──────────┬───────────┘
           │
┌──────────▼───────────┐
│ database.py (Data)    │  MySQL — connection pooling, CRUD, aggregation queries
└───────────────────────┘
```

**Request Flow:**

```
User types message → AI extracts structured JSON (with retry/validation)
    → Validated transaction(s) saved to MySQL (user-isolated)
    → Dashboard aggregates data (SQL + Pandas) → Plotly charts
    → On request, AI analyzes data and generates recommendations
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Frontend / UI | [Streamlit](https://streamlit.io/) |
| Backend Language | Python 3 |
| Database | MySQL (via `mysql-connector-python`, with connection pooling) |
| AI Engine | [Groq API](https://groq.com/) — `openai/gpt-oss-120b` |
| Security | [Werkzeug](https://werkzeug.palletsprojects.com/) (PBKDF2-SHA256 password hashing) |
| Data & Visualization | Pandas, Plotly Express |

---

## 📂 Project Structure

```
finagent/
├── app.py              # Streamlit UI — auth, chat assistant, dashboard
├── database.py          # MySQL connection, schema, CRUD, aggregation queries
├── ai_agent.py           # Groq LLM integration, prompt engineering, retry/validation
├── requirements.txt       # Python dependencies
└── .streamlit/
    └── secrets.toml       # API keys & DB credentials (not committed to git)
```

---

## 🚀 Getting Started

### Prerequisites
- Python 3.9+
- A running MySQL server
- A [Groq API key](https://console.groq.com/keys) (free tier available)

### 1. Clone the repository
```bash
git clone https://github.com/<your-username>/finagent.git
cd finagent
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Set up the MySQL database
Log in to MySQL and run:
```sql
CREATE DATABASE finagent_db;
CREATE USER 'finagent_user'@'localhost' IDENTIFIED BY 'your_password_here';
GRANT ALL PRIVILEGES ON finagent_db.* TO 'finagent_user'@'localhost';
FLUSH PRIVILEGES;
```
> Tables are created automatically on first run — no manual schema setup needed.

### 4. Configure secrets
Create `.streamlit/secrets.toml` in the project root:
```toml
GROQ_API_KEY = "your_groq_api_key_here"
MYSQL_HOST = "localhost"
MYSQL_PORT = 3306
MYSQL_USER = "finagent_user"
MYSQL_PASSWORD = "your_password_here"
MYSQL_DATABASE = "finagent_db"
```

### 5. Run the app
```bash
streamlit run app.py
```
The app will open automatically at `http://localhost:8501`.

---

## 🔒 Security Highlights

- **Password hashing** — PBKDF2-SHA256 with salting via Werkzeug; passwords are never stored or logged in plain text
- **SQL injection prevention** — all queries use parameterized placeholders (`%s`), never string concatenation
- **Strict data isolation** — every transaction query is filtered by `user_id`; no cross-user data leakage
- **Generic auth errors** — identical error messages for wrong username/password to prevent username enumeration
- **No hardcoded secrets** — API keys and DB credentials are loaded from `secrets.toml` / environment variables, excluded from version control

---

## 🧠 How the AI Pipeline Works

1. User's natural language message is sent to the Groq LLM with a strict system prompt instructing it to return a JSON **array** of transactions.
2. The raw response is cleaned (regex strips markdown/extra text) and parsed as JSON.
3. Each transaction object is validated (amount is a positive number, type is `income`/`expense`, required fields present).
4. If parsing or validation fails, the specific error is fed back to the model, which retries (up to 2 additional attempts).
5. Validated transactions are inserted into MySQL, each scoped to the authenticated user.

This design specifically handles a real-world edge case: messages describing **multiple separate amounts** (e.g. *"20 and 40 spent on food"*) are extracted as distinct transactions rather than being incorrectly averaged into one.

---

## 📈 Future Improvements

- [ ] Edit/delete transaction functionality
- [ ] Monthly budget limits with threshold alerts
- [ ] CSV/PDF export of transaction history
- [ ] Automated test suite (pytest) for database isolation and AI retry logic
- [ ] Rate-limiting on the AI endpoint for cost control
- [ ] Receipt scanning via OCR for automatic transaction entry

---

## 📝 License

This project is open source and available for educational and personal use.

---

## 🙋 Author

Built as a personal/academic project exploring practical LLM integration, secure full-stack application design, and conversational AI interfaces for everyday financial management.
