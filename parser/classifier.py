import re
import os
from pydantic import BaseModel
from typing import List, Optional
from parser.ingest import ParsedTransaction

try:
    from google import genai
    from google.genai import types, errors
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

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

def is_transfer(description: str, account_names: List[str] = None) -> bool:
    # Detect transfers between the user's accounts
    # The user typically transfers between Access, Kuda, OPay, Palmpay, Wema, etc.
    # In a real app we'd match exact account numbers.
    # Based on instructions: detect transfers between user's accounts (Access to Kuda/OPay).
    # Since we might not know all names, we check for explicit 'transfer from/to <user's own names>' if available.

    # We'll use a simple heuristic for now:
    desc_lower = description.lower()
    if 'transfer of funds between personal accts' in desc_lower:
        return True

    # For now, let's treat any explicit user name matches as internal transfers if provided
    # A robust implementation would need configuration of user names/accounts.

    return False

def categorize_transaction(tx: ParsedTransaction, client: Optional['genai.Client'] = None) -> ParsedTransaction:
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
        try:
            prompt = f"""
            Analyze the following bank transaction description and categorize it.
            Description: {tx.description}
            Amount: {tx.amount}
            Type: {tx.type}
            """
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CategoryResult,
                    temperature=0.0
                ),
            )
            cat_result = response.parsed
            if not cat_result:
                # fallback if parsed is None
                 cat_result = fallback_categorize(tx.description)
            return tx, cat_result

        except (errors.APIError, Exception) as e:
            # Fallback on any error (429, timeout, network issue)
            return tx, fallback_categorize(tx.description)

    else:
        # Fallback completely
        return tx, fallback_categorize(tx.description)
