import re
import pdfplumber
from datetime import datetime
from pydantic import BaseModel
from typing import List, Optional

class ParsedTransaction(BaseModel):
    date: str
    account: str
    amount: float
    type: str
    description: str

def _parse_amount(text: str) -> Optional[float]:
    # Remove standard commas, ₦, + and spaces. Convert to float.
    amount_str = re.sub(r'[₦,\+\s]', '', text)
    if amount_str == '-':
        return None
    try:
        return abs(float(amount_str))
    except ValueError:
        return None

def _standardize_date(date_str: str) -> str:
    # Handle YYYY/MM/DD, DD/MM/YY, DD-MMM-YY, YYYY MMM DD; export as YYYY-MM-DD.
    date_str = date_str.strip()
    try:
        # 1. 01-AUG-25 (Access)
        if re.match(r'^\d{2}-[A-Za-z]{3}-\d{2}$', date_str):
            dt = datetime.strptime(date_str, '%d-%b-%y')
        # 2. 01/08/26 (Kuda)
        elif re.match(r'^\d{2}/\d{2}/\d{2}$', date_str):
            dt = datetime.strptime(date_str, '%d/%m/%y')
        # 3. 2025 Aug 01 (OPay)
        elif re.match(r'^\d{4}\s[A-Za-z]{3}\s\d{2}$', date_str):
            dt = datetime.strptime(date_str, '%Y %b %d')
        else:
            # Fallback format or already correct?
            dt = datetime.strptime(date_str, '%Y/%m/%d')
        return dt.strftime('%Y-%m-%d')
    except ValueError:
        return date_str # Return as is if format isn't recognized

def extract_access(filepath: str) -> List[ParsedTransaction]:
    transactions = []

    date_regex = re.compile(r'^(\d{2}-[A-Za-z]{3}-\d{2})\s+(\d{2}-[A-Za-z]{3}-\d{2})\s+(.*?)\s+([\d,\.]+|\-)\s+([\d,\.]+|\-)\s+([\d,\.]+|\-)$')

    with pdfplumber.open(filepath) as pdf:
        text = "\n".join(page.extract_text() for page in pdf.pages if page.extract_text())
        lines = text.split('\n')

        current_tx = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            match = date_regex.match(line)
            if match:
                if current_tx:
                    transactions.append(current_tx)

                posted_date, value_date, description, debit, credit, balance = match.groups()

                amount_str = debit if debit != '-' else credit
                amount = _parse_amount(amount_str)

                # if amount is none, it might be a header or invalid row. Access Opening Balance has 0.00 - 0.00
                if amount is None and debit == '-' and credit == '-':
                    amount = 0.0
                elif amount is None:
                    continue

                tx_type = "Expense" if debit != '-' else "Income"

                current_tx = ParsedTransaction(
                    date=_standardize_date(posted_date),
                    account="Access Bank account ",
                    amount=amount,
                    type=tx_type,
                    description=description.strip()
                )
            else:
                # Is it an overflow line?
                if current_tx and not re.match(r'^\d{2}-[A-Za-z]{3}-\d{2}', line):
                    # Check if line looks like it belongs to standard report headers
                    if not line.startswith("Posted Date") and not line.startswith("ACCOUNT STATEMENT") and "Access Bank" not in line:
                         current_tx.description += " " + line

        if current_tx:
            transactions.append(current_tx)

    return transactions

def extract_kuda(filepath: str) -> List[ParsedTransaction]:
    transactions = []

    date_regex = re.compile(r'^(\d{2}/\d{2}/\d{2})\s*(.*)$')

    with pdfplumber.open(filepath) as pdf:
        text = "\n".join(page.extract_text() for page in pdf.pages if page.extract_text())
        lines = text.split('\n')

        current_tx = None

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # Kuda format usually has date/time on one line, or two.
            # Example:
            # 15/04/25 ₦5,000.00 inward Adebimpe Folashade gift to olawuyi mobolaji ₦5,000.00
            # 12:08:13 transfer Rafiat/0016136264/Gtbank Plc israel.
            match = date_regex.match(line)
            if match:
                if current_tx:
                    transactions.append(current_tx)

                date_str = match.group(1)
                rest_of_line = match.group(2)

                # Check next line for time and rest of columns if they are broken
                if i + 1 < len(lines) and re.match(r'^\d{2}:\d{2}:\d{2}', lines[i+1].strip()):
                    i += 1
                    rest_of_line += " " + lines[i].strip()

                # We need to extract Amount, Type, Category, To/From, Description
                # This requires finding the amount which usually has ₦

                # A robust way is to just grab the first amount-like string
                amount_match = re.search(r'(₦[\d,\.]+)', rest_of_line)
                if amount_match:
                    amount = _parse_amount(amount_match.group(1))

                    tx_type = "Expense" if "outward" in rest_of_line.lower() else "Income"

                    description = rest_of_line.replace(amount_match.group(1), '').strip()

                    # Remove trailing balance (another ₦ amount)
                    trailing_balance = re.search(r'(₦[\d,\.]+)$', description)
                    if trailing_balance:
                        description = description[:trailing_balance.start()].strip()

                    # Remove time
                    description = re.sub(r'\d{2}:\d{2}:\d{2}', '', description).strip()

                    # remove inward/outward transfer words
                    description = re.sub(r'inward transfer', '', description, flags=re.IGNORECASE).strip()
                    description = re.sub(r'outward transfer', '', description, flags=re.IGNORECASE).strip()
                    description = re.sub(r'local funds transfer', '', description, flags=re.IGNORECASE).strip()
                    description = re.sub(r'airtime purchase', '', description, flags=re.IGNORECASE).strip()

                    current_tx = ParsedTransaction(
                        date=_standardize_date(date_str),
                        account="Kuda Bank Account",
                        amount=amount,
                        type=tx_type,
                        description=description.strip()
                    )
                else:
                    current_tx = None

            elif current_tx:
                # Merge multiline if it's not a pagination line
                if "Kuda MF Bank" not in line and "Page" not in line and "Commercial avenue" not in line and not line.startswith("Date/Time"):
                     current_tx.description += " " + line

            i += 1

        if current_tx:
            transactions.append(current_tx)

    return transactions

def extract_opay(filepath: str) -> List[ParsedTransaction]:
    transactions = []

    date_regex = re.compile(r'^(20\d{2}\s[A-Za-z]{3}\s\d{2})\s+(\d{2}:\d{2}:\d{2})\s+(\d{2}\s[A-Za-z]{3}\s20\d{2})\s+(.*?)\s+([\+\-][\d,\.]+)\s+([\d,\.]+)\s+(.*?)$')

    with pdfplumber.open(filepath) as pdf:
        text = "\n".join(page.extract_text() for page in pdf.pages if page.extract_text())
        lines = text.split('\n')

        current_tx = None

        for line in lines:
            line = line.strip()

            match = date_regex.match(line)
            if match:
                if current_tx:
                    transactions.append(current_tx)

                trans_date, time, value_date, description, amount_str, balance, rest = match.groups()

                amount = _parse_amount(amount_str)
                tx_type = "Income" if amount_str.startswith('+') else "Expense"

                current_tx = ParsedTransaction(
                    date=_standardize_date(trans_date),
                    account="My Personal Opay Account",
                    amount=amount,
                    type=tx_type,
                    description=description.strip() + " " + rest.strip()
                )
            elif current_tx:
                if not re.match(r'^20\d{2}\s[A-Za-z]{3}\s\d{2}', line) and not line.startswith("Account") and not line.startswith("Summary") and not line.startswith("Opening") and "Note: Current" not in line:
                    current_tx.description += " " + line

        if current_tx:
            transactions.append(current_tx)

    return transactions

def parse_statement(filepath: str) -> List[ParsedTransaction]:
    # Determine the bank type by filename or by reading the first page
    content = ""
    with pdfplumber.open(filepath) as pdf:
        if pdf.pages:
            content = pdf.pages[0].extract_text() or ""

    filepath_lower = filepath.lower()

    # All files have Kuda, OPay and Access keywords because of the transfers between them.
    # Use filename exclusively if it matches the pattern or more reliable text indicators
    if "kuda" in filepath_lower or "kuda bank" in content.lower():
        return extract_kuda(filepath)
    elif "opay" in filepath_lower or "opay wallet" in content.lower():
        return extract_opay(filepath)
    elif "access" in filepath_lower or "ACCOUNT STATEMENT" in content:
        return extract_access(filepath)
    else:
        raise ValueError(f"Could not determine bank type from PDF {filepath}.")
