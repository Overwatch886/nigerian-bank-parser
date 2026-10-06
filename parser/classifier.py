import re
import os
import sys
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
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
)
GEMINI_MODELS = tuple(
    model.strip()
    for model in os.environ.get("GEMINI_MODELS", ",".join(DEFAULT_GEMINI_MODELS)).split(",")
    if model.strip()
)
GEMINI_MODEL = GEMINI_MODELS[0]

class CategoryResult(BaseModel):
    main_category: str
    sub_category: str

# Local Heuristic Rules
CATEGORY_RULES = [
    (r'(UBER|BOLT|FUEL|TOTAL|OANDO|CONOIL)', 'Transport', ''),
    (r'(MTN|AIRTEL|GLO|9MOBILE|IBEDC|IKEDC|EKEDC|ELECTRIC)', 'Utilities & Airtime', ''),
    (r'(CHEF|KITCHEN|FOOD|RESTAURANT|BUKKA|DOMINOS|EATERIES)', 'Food & Dining', ''),
    (r'(SUPERMARKET|GROCERY|MART|SPA)', 'Groceries', ''),
    (r'(STAMP DUTY|ELECTRONIC MONEY TRANSFER LEVY|SMS CHG|VAT|MAINTENANCE)', 'Bank Charges', '')
]

def fallback_categorize(description: str) -> CategoryResult:
    desc_upper = description.upper()
    for pattern, main_cat, sub_cat in CATEGORY_RULES:
        if re.search(pattern, desc_upper):
            return CategoryResult(main_category=main_cat, sub_category=sub_cat)
    return CategoryResult(main_category="Uncategorized", sub_category="")

def _is_rate_limit_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status_code == 429:
        return True

    message = str(exc).lower()
    return any(term in message for term in ("rate limit", "rate_limit", "quota exceeded", "resource exhausted"))

def _exhausted_models(client: 'genai.Client') -> set[str]:
    exhausted = getattr(client, "_gemini_exhausted_models", None)
    if exhausted is None:
        exhausted = set()
        setattr(client, "_gemini_exhausted_models", exhausted)
    return exhausted

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
        rate_limited_models = []
        for model in GEMINI_MODELS:
            if model in exhausted_models:
                rate_limited_models.append(model)
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
                return tx, cat_result
            except Exception as e:
                if not _is_rate_limit_error(e):
                    print(
                        f"Warning: Gemini categorization failed with {model} ({e}). "
                        "Falling back to local rules.",
                        file=sys.stderr,
                    )
                    return tx, fallback_categorize(tx.description)

                exhausted_models.add(model)
                rate_limited_models.append(model)
                print(
                    f"Warning: Gemini model {model} hit a rate limit or is over quota. "
                    "Trying the next configured model.",
                    file=sys.stderr,
                )

        if rate_limited_models and len(rate_limited_models) == len(GEMINI_MODELS):
            print(
                f"Warning: All configured Gemini models are rate-limited or over quota "
                f"while categorizing '{tx.description}'. Falling back to local rules.",
                file=sys.stderr,
            )
        return tx, fallback_categorize(tx.description)

    else:
        # Fallback completely
        return tx, fallback_categorize(tx.description)
