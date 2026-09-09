"""
ai_agent.py
------------------------------------------------------------------------
The AI Agent Engine: converts a natural-language message like
"I spent 500 on Zomato today" into a structured transaction dict using
the Groq API (llama3-70b-8192 by default).

Design goals:
  - Strict system prompt so the LLM behaves purely as an entity extractor.
  - Robust JSON parsing: strip code fences / stray text, then json.loads.
  - Retry loop (up to MAX_RETRIES extra attempts) that feeds the parsing
    error back to the model so it can self-correct.
  - Never raises to the caller for expected failure modes; instead
    returns (None, error_message) so the UI can display it gracefully.
------------------------------------------------------------------------
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

try:
    import streamlit as st
except ImportError:  # allows this module to be unit-tested without streamlit
    st = None

from groq import Groq
from groq import APIError, APIConnectionError, APITimeoutError

MODEL_NAME = "openai/gpt-oss-120b"
MAX_RETRIES = 2  # total attempts = 1 initial + MAX_RETRIES retries
REQUIRED_KEYS = {"amount", "category", "type", "description"}

SYSTEM_PROMPT = """You are a strict financial transaction entity extractor.
Your ONLY job is to read a user's natural-language message about money and
return a valid JSON ARRAY of transaction objects. Do not include any
explanation, greeting, markdown formatting, or code fences. Return ONLY the
raw JSON array and nothing else — even if there is only one transaction,
still wrap it in an array with a single element.

Each transaction object must have EXACTLY these keys:
- "amount": a positive number (integer or float) — the transaction amount.
- "category": a short string such as "Food", "Salary", "Rent", "Shopping",
  "Transport", "Entertainment", "Utilities", "Health", "Freelance", "Other".
- "type": exactly "income" or "expense" (lowercase).
- "description": a short, human-readable description of the transaction.

CRITICAL rule for multiple amounts in one message:
- If the message mentions two or more separate amounts that were each
  spent/earned separately (e.g. "20 and 40 spent on food", "500 on food and
  200 on transport"), create ONE SEPARATE object for EACH amount. Never
  average, combine, or sum multiple amounts into a single number.
- Only combine into one object if the message clearly describes a single
  total for a single event (e.g. "spent 60 total on snacks").

Classification rules:
- Spending, paying, buying, purchasing -> "expense".
- Receiving, earning, getting paid, salary, refund, bonus, gift received -> "income".
- If an amount is genuinely not stated anywhere, make the best reasonable
  guess based on context; never omit the "amount" key.
- Respond with ONLY the JSON array. No preamble, no trailing commentary,
  no markdown code fences.

Example:
Input: "I spent 500 on Zomato today"
Output: [{"amount": 500, "category": "Food", "type": "expense", "description": "Zomato order"}]

Example:
Input: "Got 10000 as salary"
Output: [{"amount": 10000, "category": "Salary", "type": "income", "description": "Salary credited"}]

Example (multiple amounts in one message):
Input: "20 aur 40 khane me kharch ho gye"
Output: [{"amount": 20, "category": "Food", "type": "expense", "description": "Food expense"}, {"amount": 40, "category": "Food", "type": "expense", "description": "Food expense"}]

Example (mixed categories):
Input: "spent 500 on food and 200 on transport"
Output: [{"amount": 500, "category": "Food", "type": "expense", "description": "Food expense"}, {"amount": 200, "category": "Transport", "type": "expense", "description": "Transport expense"}]
"""


def _get_api_key() -> Optional[str]:
    """Resolve the Groq API key from st.secrets first, then environment variables."""
    api_key = None
    if st is not None:
        try:
            api_key = st.secrets.get("GROQ_API_KEY")  # type: ignore[union-attr]
        except Exception:
            api_key = None
    if not api_key:
        api_key = os.environ.get("GROQ_API_KEY")
    return api_key


def _get_client() -> Groq:
    api_key = _get_api_key()
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not found. Add it to .streamlit/secrets.toml as "
            'GROQ_API_KEY = "your_key_here", or set it as an environment variable.'
        )
    return Groq(api_key=api_key)


def _extract_json_block(text: str) -> str:
    """
    Best-effort clean-up of a raw LLM response so json.loads() has the best
    chance of succeeding: strips markdown code fences and pulls out the
    first [...] array (or falls back to a {...} object) if there is
    surrounding chatter.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    array_match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if array_match:
        return array_match.group(0)

    object_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if object_match:
        return object_match.group(0)

    return cleaned


def _validate_and_normalize(data: dict) -> dict:
    """Validate the parsed JSON has the right shape/types; raises ValueError otherwise."""
    if not isinstance(data, dict):
        raise ValueError("Model response is not a JSON object.")

    missing = REQUIRED_KEYS - data.keys()
    if missing:
        raise ValueError(f"Missing required keys: {sorted(missing)}")

    try:
        amount = float(data["amount"])
    except (TypeError, ValueError):
        raise ValueError("'amount' is not a valid number.")
    if amount < 0:
        raise ValueError("'amount' cannot be negative.")

    tx_type = str(data["type"]).strip().lower()
    if tx_type not in ("income", "expense"):
        raise ValueError("'type' must be exactly 'income' or 'expense'.")

    category = str(data.get("category") or "Other").strip() or "Other"
    description = str(data.get("description") or category).strip() or category

    return {
        "amount": round(amount, 2),
        "category": category,
        "type": tx_type,
        "description": description,
    }


def parse_transactions(user_text: str) -> tuple[Optional[list[dict]], Optional[str]]:
    """
    Send user_text to the Groq LLM and parse a LIST of structured
    transactions out of the response (a message can describe more than one
    transaction, e.g. "20 and 40 spent on food"). Retries up to MAX_RETRIES
    times on malformed output.

    Returns:
        (list_of_parsed_dicts, None) on success
        (None, error_message) on failure
    """
    if not user_text or not user_text.strip():
        return None, "Please describe a transaction, e.g. 'Spent 200 on groceries'."

    try:
        client = _get_client()
    except ValueError as e:
        return None, str(e)

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text.strip()},
    ]

    last_error = "Unknown error."

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0.1,
                max_tokens=500,
            )
        except APITimeoutError:
            last_error = "The AI service timed out. Please try again."
            continue
        except APIConnectionError:
            last_error = "Could not connect to the AI service. Check your internet connection."
            continue
        except APIError as e:
            last_error = f"AI service returned an error: {e}"
            continue
        except Exception as e:  # noqa: BLE001 - guard against any unexpected SDK error
            last_error = f"Unexpected error while contacting the AI service: {e}"
            continue

        try:
            raw_content = response.choices[0].message.content or ""
        except (AttributeError, IndexError):
            last_error = "The AI service returned an empty or malformed response."
            continue

        json_str = _extract_json_block(raw_content)

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as je:
            last_error = f"Could not parse JSON from model output: {je}"
            messages.append({"role": "assistant", "content": raw_content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"That response could not be parsed as JSON ({je}). "
                        "Reply again with ONLY a valid raw JSON array of "
                        "transaction objects, each containing amount, "
                        "category, type, and description. No markdown, no "
                        "extra words."
                    ),
                }
            )
            continue

        # Backward-compatible: accept a single object too, just wrap it.
        if isinstance(parsed, dict):
            parsed = [parsed]

        if not isinstance(parsed, list) or len(parsed) == 0:
            last_error = "Model response was not a non-empty JSON array."
            messages.append({"role": "assistant", "content": raw_content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "That response was not a valid non-empty JSON array. "
                        "Reply again with ONLY a JSON array of transaction "
                        "objects (wrap a single transaction in an array too)."
                    ),
                }
            )
            continue

        try:
            validated_list = [_validate_and_normalize(item) for item in parsed]
            return validated_list, None
        except ValueError as ve:
            last_error = str(ve)
            messages.append({"role": "assistant", "content": raw_content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"That JSON was invalid: {ve}. Reply again with ONLY a "
                        "valid raw JSON array of transaction objects, each "
                        "containing amount, category, type, and description."
                    ),
                }
            )
            continue

    return None, (
        f"Failed to extract valid transactions after {MAX_RETRIES + 1} attempts. "
        f"Last error: {last_error}"
    )


def parse_transaction(user_text: str) -> tuple[Optional[dict], Optional[str]]:
    """
    Backward-compatible single-transaction wrapper around parse_transactions().
    Returns only the first parsed transaction. Prefer parse_transactions()
    for new code so multi-amount messages are handled correctly.
    """
    parsed_list, error = parse_transactions(user_text)
    if error or not parsed_list:
        return None, error
    return parsed_list[0], None


# ---------------------------------------------------------------------------
# AI Financial Advisor: turns raw numbers into plain-language recommendations
# ---------------------------------------------------------------------------
ADVISOR_SYSTEM_PROMPT = """You are a friendly, practical personal finance advisor.
You will be given a user's financial summary (total income, total expense,
balance, and a category-wise expense breakdown). Your job is to write a
short, encouraging, and genuinely useful set of recommendations.

Rules:
- Write 3 to 5 short bullet points, in plain text starting each with "- ".
- Be specific: mention actual numbers/categories/percentages from the data
  given, not generic advice like "save more money".
- If a category takes up a large share of expenses (e.g. over 30%), call it
  out by name and suggest a concrete, realistic action.
- If balance is negative or income is close to expenses, gently flag the
  risk and suggest one concrete fix.
- If the data looks healthy, still give at least one constructive tip
  (e.g. building an emergency fund, or a savings target).
- Keep the tone warm and non-judgmental, never preachy or alarmist.
- Do not repeat the raw numbers back verbatim in every line; interpret them.
- Output ONLY the bullet points. No greeting, no closing remarks, no
  markdown headers.
"""


def get_financial_advice(
    summary: dict, expense_by_category: list[dict], income_by_category: list[dict]
) -> tuple[Optional[str], Optional[str]]:
    """
    Ask the LLM for personalized, plain-language financial recommendations
    based on the user's aggregated transaction data.

    Returns:
        (advice_text, None) on success
        (None, error_message) on failure
    """
    if summary.get("income", 0) == 0 and summary.get("expense", 0) == 0:
        return None, "Add a few transactions first so I have something to analyze."

    try:
        client = _get_client()
    except ValueError as e:
        return None, str(e)

    data_summary = (
        f"Total income: ₹{summary.get('income', 0):,.2f}\n"
        f"Total expense: ₹{summary.get('expense', 0):,.2f}\n"
        f"Current balance: ₹{summary.get('balance', 0):,.2f}\n"
        f"Expense by category: {expense_by_category}\n"
        f"Income by category: {income_by_category}\n"
    )

    messages = [
        {"role": "system", "content": ADVISOR_SYSTEM_PROMPT},
        {"role": "user", "content": data_summary},
    ]

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.4,
            max_tokens=400,
        )
        advice = (response.choices[0].message.content or "").strip()
        if not advice:
            return None, "The AI service returned an empty response."
        return advice, None
    except APITimeoutError:
        return None, "The AI service timed out. Please try again."
    except APIConnectionError:
        return None, "Could not connect to the AI service. Check your internet connection."
    except APIError as e:
        return None, f"AI service returned an error: {e}"
    except Exception as e:  # noqa: BLE001
        return None, f"Unexpected error while contacting the AI service: {e}"
