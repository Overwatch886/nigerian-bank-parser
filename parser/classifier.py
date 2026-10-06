import re
import os
import sys
import json
import subprocess
import time
from urllib import request
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from pydantic import BaseModel
from typing import Optional, Tuple
from parser.ingest import ParsedTransaction

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

DEFAULT_GEMINI_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemma-3-27b-it",
    "gemma-3-12b-it",
    "gemma-3-4b-it",
    "gemini-2.5-flash",
)
GEMINI_MODELS = tuple(
    model.strip()
    for model in os.environ.get("GEMINI_MODELS", ",".join(DEFAULT_GEMINI_MODELS)).split(",")
    if model.strip()
)
GEMINI_MODEL = GEMINI_MODELS[0]
WORKBOOK_CATEGORIES = (
    "Airtime Expense", "Cash", "Debt Payments", "Donations", "Gifts",
    "Investments", "Loans", "Other", "Sale Of Assets", "Shopping",
    "Subscriptions", "Taxes, Fees and Levies", "🍜 Food", "🎁 Gift",
    "🏅 Bonus", "💰 Salary", "📙 Education", "🚖 Transport", "🤑 Allowance",
    "🧘🏼 Health", "🧥 Apparel", "🪑 Household",
)
LOCAL_MODEL_ENABLED = os.environ.get("LOCAL_MODEL_ENABLED", "").lower() in ("1", "true", "yes", "on")
LOCAL_MODEL_ENDPOINT = os.environ.get(
    "LOCAL_MODEL_ENDPOINT",
    "http://127.0.0.1:8080/v1/chat/completions",
)
LOCAL_MODEL_NAME = os.environ.get("LOCAL_MODEL_NAME", "granite-4.0-h-tiny")
LOCAL_MODEL_TIMEOUT = float(os.environ.get("LOCAL_MODEL_TIMEOUT", "30"))
LOCAL_MODEL_START_SCRIPT = os.environ.get(
    "LOCAL_MODEL_START_SCRIPT",
    r"C:\developer\scripts\run_granite.bat",
)
LOCAL_MODEL_STARTUP_TIMEOUT = float(os.environ.get("LOCAL_MODEL_STARTUP_TIMEOUT", "30"))
_LOCAL_MODEL_UNAVAILABLE = False

class CategoryResult(BaseModel):
    main_category: str
    sub_category: str

# Local Heuristic Rules
CATEGORY_RULES = [
    (r'\b(UBER|BOLT|FUEL|TOTAL|OANDO|CONOIL|TRANSPORT|BUS FARE|DANFO)\b', 'Transport', 'Bus'),
    (r'\b(MTN|AIRTEL|GLO|9MOBILE|IBEDC|IKEDC|EKEDC|ELECTRIC|AIRTIME|DATA|GB)\b', 'Utilities & Airtime', ''),
    (r'\b(CHEF|KITCHEN|FOOD|RESTAURANT|BUKKA|DOMINOS|EATERIES|BEANS|RICE|BREAD|EGG|GARRI|FOOD STUFFS?)\b', 'Food & Dining', 'Purchase of Food Accessories and Minor Food Stuffs'),
    (r'\b(SUPERMARKET|GROCERY|MART|SHOPPING)\b', 'Groceries', ''),
    (r'\b(STAMP DUTY|ELECTRONIC MONEY TRANSFER LEVY|TRANSFER LEVY|SMS CHG|SMS ALERT FEE|VAT|MAINTENANCE|BANK CHARGE|FEES?)\b', 'Bank Charges', ''),
    (r'\b(SALARY|LESSON PAYMENT|LESSON SALARY|WAGES?)\b', 'Income', ''),
    (r'\b(ALLOWANCE|STIPENDS?|BURSARY)\b', '🤑 Allowance', ''),
    (r'\b(BONUS)\b', '🏅 Bonus', ''),
    (r'\b(GIFT|BIRTHDAY)\b', '🎁 Gift', ''),
    (r'\b(TITHE|DONATION|DONATED)\b', 'Donations', ''),
    (r'\b(LENT|LOAN|BORROWED|DEBT PAYMENT|PAY BACK|PAID BACK)\b', 'Loans', 'Money Lent to Others'),
    (r'\b(ATM|CASH WITHDRAWAL|WITHDRAWAL FROM|CASH)\b', 'Cash', ''),
    (r'\b(HOSPITAL|X-RAY|XRAY|LAB MASTER|PHARMACY|MEDICAL|GLASSES|EYEGLASSES|NOSE MASK)\b', '🧘🏼 Health', ''),
    (r'\b(SCHOOL|SCHOOLING|NACOS|TEXTBOOK|WORKBOOK|CALCULATOR|PHOTOCOPY|PRINTING|PASSPORT PHOTOS|ACADEMIC|DEPARTMENTAL DUES)\b', '📙 Education', ''),
    (r'\b(BAMBOO|AFRINVEST|OPTIMUS|STOCKS?|CRYPTO|SOLANA|INVESTMENT)\b', 'Investments', ''),
    (r'\b(VPN|SUBSCRIPTION|RENEWAL)\b', 'Subscriptions', ''),
    (r'\b(DETERGENT|BATHING SOAP|SOAP|KEROSENE|PADLOCK|HOUSEHOLD)\b', '🪑 Household', 'Toiletries'),
    (r'\b(SHOE|SHOES|APPAREL|CLOTHING)\b', '🧥 Apparel', 'Shoes'),
    (r'\b(SALE OF|SOLD|ASSET SALE)\b', 'Sale Of Assets', ''),
]

def fallback_categorize(description: str) -> CategoryResult:
    desc_upper = description.upper()
    for pattern, main_cat, sub_cat in CATEGORY_RULES:
        if re.search(pattern, desc_upper):
            return _normalize_category(CategoryResult(main_category=main_cat, sub_category=sub_cat))
    return CategoryResult(main_category="Other", sub_category="Other")

def _normalize_category(result: CategoryResult) -> CategoryResult:
    aliases = {
        "Transport": "🚖 Transport",
        "Food & Dining": "🍜 Food",
        "Groceries": "Other",
        "Utilities & Airtime": "Airtime Expense",
        "Bank Charges": "Taxes, Fees and Levies",
        "Income": "💰 Salary",
        "Uncategorized": "Other",
    }
    main_category = aliases.get(result.main_category, result.main_category)
    if main_category not in WORKBOOK_CATEGORIES:
        return CategoryResult(main_category="Other", sub_category="Other")
    return CategoryResult(main_category=main_category, sub_category=result.sub_category)

def _is_model_unavailable_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status_code in (429, 500, 502, 503, 504, "429", "500", "502", "503", "504"):
        return True

    message = str(exc).lower()
    return any(
        term in message
        for term in (
            "rate limit",
            "rate_limit",
            "quota exceeded",
            "resource exhausted",
            "unavailable",
            "high demand",
            "deadline exceeded",
            "timed out",
            "timeout",
        )
    )

def _exhausted_models(client: 'genai.Client') -> set[str]:
    exhausted = getattr(client, "_gemini_exhausted_models", None)
    if exhausted is None:
        exhausted = set()
        setattr(client, "_gemini_exhausted_models", exhausted)
    return exhausted

def _local_model_health_url() -> str:
    endpoint = urlsplit(LOCAL_MODEL_ENDPOINT)
    return urlunsplit((endpoint.scheme, endpoint.netloc, "/health", "", ""))

def _local_model_is_running() -> bool:
    try:
        with request.urlopen(_local_model_health_url(), timeout=2) as response:
            return response.status == 200
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return False

def ensure_local_model_running() -> bool:
    global _LOCAL_MODEL_UNAVAILABLE
    if not LOCAL_MODEL_ENABLED:
        return True
    if _local_model_is_running():
        return True

    if not os.path.isfile(LOCAL_MODEL_START_SCRIPT):
        print(
            f"Warning: Local model startup script was not found: {LOCAL_MODEL_START_SCRIPT}. "
            "Falling back to local rules.",
            file=sys.stderr,
        )
        _LOCAL_MODEL_UNAVAILABLE = True
        return False

    try:
        subprocess.Popen(
            [os.environ.get("COMSPEC", "cmd.exe"), "/c", LOCAL_MODEL_START_SCRIPT],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except OSError as exc:
        print(
            f"Warning: Could not start local model with {LOCAL_MODEL_START_SCRIPT} ({exc}). "
            "Falling back to local rules.",
            file=sys.stderr,
        )
        _LOCAL_MODEL_UNAVAILABLE = True
        return False

    deadline = time.monotonic() + LOCAL_MODEL_STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if _local_model_is_running():
            print(f"Local model is ready at {LOCAL_MODEL_ENDPOINT}.")
            return True
        time.sleep(0.5)

    print(
        f"Warning: Local model did not become ready within {LOCAL_MODEL_STARTUP_TIMEOUT:.0f} "
        "seconds. Falling back to local rules.",
        file=sys.stderr,
    )
    _LOCAL_MODEL_UNAVAILABLE = True
    return False

def _local_model_prompt(tx: ParsedTransaction) -> str:
    return f"""
Categorize this Nigerian bank transaction into exactly one main category.
Use only one of: {", ".join(WORKBOOK_CATEGORIES)}.
Return only valid JSON with this shape: {{"main_category": "...", "sub_category": "..."}}.
Description: {tx.description}
Amount: {tx.amount}
Type: {tx.type}
""".strip()

def _categorize_with_local_model(tx: ParsedTransaction) -> Optional[CategoryResult]:
    global _LOCAL_MODEL_UNAVAILABLE
    if not LOCAL_MODEL_ENABLED or _LOCAL_MODEL_UNAVAILABLE:
        return None

    payload = json.dumps({
        "model": LOCAL_MODEL_NAME,
        "messages": [
            {
                "role": "user",
                "content": _local_model_prompt(tx),
            }
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    http_request = request.Request(
        LOCAL_MODEL_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(http_request, timeout=LOCAL_MODEL_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
        return _normalize_category(CategoryResult.model_validate_json(content))
    except Exception as exc:
        _LOCAL_MODEL_UNAVAILABLE = True
        print(
            f"Warning: Local model {LOCAL_MODEL_NAME} is unavailable ({exc}). "
            "Falling back to local rules.",
            file=sys.stderr,
        )
        return None

def categorize_transaction(tx: ParsedTransaction, client: Optional['genai.Client'] = None) -> Tuple[ParsedTransaction, CategoryResult]:
    # Detect internal transfer heuristics from descriptions
    # Based on instructions: detect transfers between user's accounts (Access, Kuda, OPay).
    is_internal = False

    desc_lower = tx.description.lower()

    # Check if transfer involves typical banks used by the user
    bank_keywords = ["access", "kuda", "opay", "palmpay", "wema"]

    if 'transfer' in desc_lower:
        if any(bank in desc_lower for bank in bank_keywords) or 'transfer of funds between personal' in desc_lower:
             # Just a simple heuristic for cross-account
             is_internal = True

    if is_internal:
        tx.type = "Transfer-Out"
        tx_category = CategoryResult(main_category="Transfer", sub_category="Internal")
        return tx, tx_category

    # Use LLM if available
    if client and GENAI_AVAILABLE:
        prompt = f"""
        Analyze the following bank transaction description and categorize it.
        Description: {tx.description}
        Amount: {tx.amount}
        Type: {tx.type}
        """
        exhausted_models = _exhausted_models(client)
        unavailable_models = []
        for model in GEMINI_MODELS:
            if model in exhausted_models:
                unavailable_models.append(model)
                continue
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=CategoryResult,
                        temperature=0.0
                    ),
                )
                cat_result = response.parsed
                if not cat_result:
                    cat_result = fallback_categorize(tx.description)
                return tx, _normalize_category(cat_result)
            except Exception as e:
                if not _is_model_unavailable_error(e):
                    print(
                        f"Warning: Gemini categorization failed with {model} ({e}). "
                        "Trying the local model or rules.",
                        file=sys.stderr,
                    )
                    local_result = _categorize_with_local_model(tx)
                    return tx, local_result or fallback_categorize(tx.description)

                exhausted_models.add(model)
                unavailable_models.append(model)
                print(
                    f"Warning: Gemini model {model} is rate-limited or temporarily unavailable "
                    f"({e}). "
                    "Trying the next configured model.",
                    file=sys.stderr,
                )

        if unavailable_models and len(unavailable_models) == len(GEMINI_MODELS):
            print(
                f"Warning: All configured Gemini models are rate-limited or temporarily unavailable "
                f"while categorizing '{tx.description}'. Falling back to local rules.",
                file=sys.stderr,
            )
        local_result = _categorize_with_local_model(tx)
        return tx, local_result or fallback_categorize(tx.description)

    else:
        local_result = _categorize_with_local_model(tx)
        return tx, local_result or fallback_categorize(tx.description)
