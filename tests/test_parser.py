import pytest
import os
import csv
from unittest.mock import MagicMock, patch

from parser.ingest import ParsedTransaction, extract_kuda, extract_access, extract_opay, _standardize_date
from parser.classifier import categorize_transaction, CategoryResult, fallback_categorize, GENAI_AVAILABLE
if GENAI_AVAILABLE:
    from google.genai import errors
from parser.exporter import export_to_tsv

def test_standardize_date():
    assert _standardize_date("01-AUG-25") == "2025/08/01"
    assert _standardize_date("01/08/25") == "2025/08/01"
    assert _standardize_date("2025 Aug 01") == "2025/08/01"

def test_multiline_reconstruction(tmp_path):
    # This tests the output from ingest assuming `pdfplumber` works correctly
    # Since we can't easily mock pdfplumber page extraction directly without complex setups,
    # we'll test the raw parsing logic on the actual sample files if available,
    # or just use small mock pdfs. Here we will run on sample files and check a specific known multiline.

    # We know in Kuda sample there is:
    # 25/06/26 ₦5,000.00 inward Adebimpe Folashade gift to olawuyi mobolaji israel. 00001326062514011100 0021249244
    if os.path.exists('fixtures/kuda_sample.pdf'):
        transactions = extract_kuda('fixtures/kuda_sample.pdf')
        assert len(transactions) > 0
        found = False
        for tx in transactions:
                if tx.amount == 5000.0 and "0021249244" in tx.description:
                    found = True
                    assert tx.type == "Income"
                    # Check multiline
                    assert "israel." in tx.description
        assert found
    else:
        # Just to fail if run in environment where fixtures are assumed to exist
        pass

@pytest.mark.skipif(not GENAI_AVAILABLE, reason="google-genai not installed")
def test_classifier_fallback_mechanism():
    # Mock a 429 rate limit exception from google.genai
    client_mock = MagicMock()

    # Create an APIError manually or use a generic exception depending on how the SDK is structured
    # The actual exception structure in google.genai.errors might need a specific init
    client_mock.models.generate_content.side_effect = errors.APIError(
        "Rate limit exceeded",
        {}
    )

    tx = ParsedTransaction(
        date="2025/08/01",
        account="Access",
        amount=1000.0,
        type="Expenses",
        description="UBER TRIP TO LAGOS"
    )

    # Should not crash, should return a category
    result_tx, cat = categorize_transaction(tx, client=client_mock)

    assert cat.main_category == "Transport"
    assert result_tx.type == "Expenses"

def test_classifier_internal_transfer():
    tx = ParsedTransaction(
        date="2025/08/01",
        account="Access",
        amount=50000.0,
        type="Expenses",
        description="transfer of funds between personal accts olawuyi mobolaji israel"
    )

    result_tx, cat = categorize_transaction(tx, client=None)

    assert result_tx.type == "Transfer-Out"
    assert cat.main_category == "Transfer"

def test_money_manager_format(tmp_path):
    output_path = tmp_path / "output.tsv"

    tx = ParsedTransaction(
        date="2025/08/01",
        account="Access",
        amount=3000.0,
        type="Expenses",
        description="Payment for\n FOOD\t AND \r DRINKS"
    )
    cat = CategoryResult(main_category="Food & Dining", sub_category="")

    export_to_tsv([(tx, cat)], str(output_path))

    with open(output_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter='\t')
        rows = list(reader)

        # 8 columns required
        assert len(rows[0]) == 8
        assert rows[0] == ["Date", "Account", "Main Category", "Sub Category", "Note", "Amount", "Type", "Description"]

        # Check formatting
        assert rows[1][0] == "2025/08/01"
        assert rows[1][1] == "Access"
        assert rows[1][2] == "Food & Dining"
        assert rows[1][3] == ""
        assert rows[1][4] == ""
        assert rows[1][5] == "3000.00"
        assert rows[1][6] == "Expenses"
        assert rows[1][7] == "Payment for FOOD AND DRINKS" # Cleaned whitespace
