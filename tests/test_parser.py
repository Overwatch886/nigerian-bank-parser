import pytest
import os
import csv
from unittest.mock import MagicMock, patch

from parser.ingest import ParsedTransaction, extract_kuda, extract_access, extract_opay, _standardize_date
from parser.classifier import categorize_transaction, CategoryResult, fallback_categorize, GENAI_AVAILABLE, GEMINI_MODELS
if GENAI_AVAILABLE:
    from google.genai import errors
from parser.exporter import export_to_tsv

def test_standardize_date():
    assert _standardize_date("01-AUG-25") == "2025/08/01"
    assert _standardize_date("01/08/25") == "2025/08/01"
    assert _standardize_date("2025 Aug 01") == "2025/08/01"

def test_multiline_reconstruction(tmp_path):
    # Create a mock PDF extract string and mock pdfplumber to return it
    mock_pdf_text = (
        "Account Number Date\n"
        "25/06/26 ₦5,000.00 inward Adebimpe Folashade gift to olawuyi mobolaji ₦5,975.00\n"
        "14:01:22 transfer Rafiat/0016136264/Gtbank Plc israel.\n"
        "00001326062514011100\n"
        "0021249244\n"
        "26/06/26 ₦300.00 outward Gracious Mary soap ₦5,675.00\n"
        "09:51:51 transfer Obhio/8032565680/Opay Digital\n"
        "Services Limited\n"
    )

    mock_page = MagicMock()
    mock_page.extract_text.return_value = mock_pdf_text

    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]

    # Context manager mock
    mock_pdf.__enter__.return_value = mock_pdf
    mock_pdf.__exit__.return_value = None

    with patch('pdfplumber.open', return_value=mock_pdf):
        transactions = extract_kuda('mock.pdf')

    assert len(transactions) == 2
    tx1 = transactions[0]
    assert tx1.date == "2026/06/25"
    assert tx1.amount == 5000.0
    assert tx1.type == "Income"
    assert "Adebimpe Folashade gift to olawuyi mobolaji" in tx1.description
    assert "Rafiat/0016136264/Gtbank Plc israel." in tx1.description
    assert "00001326062514011100 0021249244" in tx1.description

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

    # Should not crash, should return a category and report the rate limit.
    with patch("sys.stderr") as stderr:
        result_tx, cat = categorize_transaction(tx, client=client_mock)

    assert cat.main_category == "Transport"
    assert result_tx.type == "Expenses"
    stderr_output = "".join(call.args[0] for call in stderr.write.call_args_list)
    assert "rate limit" in stderr_output.lower()

@pytest.mark.skipif(not GENAI_AVAILABLE, reason="google-genai not installed")
def test_classifier_uses_configured_gemini_model():
    client_mock = MagicMock()
    client_mock.models.generate_content.return_value.parsed = CategoryResult(
        main_category="Food & Dining",
        sub_category="",
    )
    tx = ParsedTransaction(
        date="2025/08/01",
        account="Access",
        amount=1000.0,
        type="Expenses",
        description="RESTAURANT PAYMENT",
    )

    categorize_transaction(tx, client=client_mock)

    assert client_mock.models.generate_content.call_args.kwargs["model"] == GEMINI_MODELS[0]

@pytest.mark.skipif(not GENAI_AVAILABLE, reason="google-genai not installed")
def test_classifier_rotates_rate_limited_models():
    client_mock = MagicMock()
    client_mock.models.generate_content.side_effect = [
        errors.APIError("Rate limit exceeded", {}),
        errors.APIError("Rate limit exceeded", {}),
        MagicMock(parsed=CategoryResult(main_category="Food & Dining", sub_category="")),
    ]
    tx = ParsedTransaction(
        date="2025/08/01",
        account="Access",
        amount=1000.0,
        type="Expenses",
        description="RESTAURANT PAYMENT",
    )

    result_tx, cat = categorize_transaction(tx, client=client_mock)

    assert result_tx.type == "Expenses"
    assert cat.main_category == "Food & Dining"
    attempted_models = [
        call.kwargs["model"]
        for call in client_mock.models.generate_content.call_args_list
    ]
    assert attempted_models == list(GEMINI_MODELS[:3])

@pytest.mark.skipif(not GENAI_AVAILABLE, reason="google-genai not installed")
def test_classifier_rotates_temporarily_unavailable_models():
    client_mock = MagicMock()
    client_mock.models.generate_content.side_effect = [
        errors.APIError("503 UNAVAILABLE: high demand", {}),
        MagicMock(parsed=CategoryResult(main_category="Food & Dining", sub_category="")),
    ]
    tx = ParsedTransaction(
        date="2025/08/01",
        account="Access",
        amount=1000.0,
        type="Expenses",
        description="RESTAURANT PAYMENT",
    )

    result_tx, cat = categorize_transaction(tx, client=client_mock)

    assert result_tx.type == "Expenses"
    assert cat.main_category == "Food & Dining"
    attempted_models = [
        call.kwargs["model"]
        for call in client_mock.models.generate_content.call_args_list
    ]
    assert attempted_models == list(GEMINI_MODELS[:2])

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
