# Money Manager Bank Statement Parser

A resilient, headless Python CLI tool designed to parse Nigerian bank statement PDFs (Access Bank, Kuda, and OPay) and export them as an 8-column TSV file compatible with the Money Manager app.

## Features

- **PDF Data Extraction**: Uses `pdfplumber` to accurately read PDF statements.
- **Multiline Normalization**: Reconstructs broken transaction narrations spanning multiple lines.
- **Data Standardization**: Normalizes amounts to absolute positive floats and formats dates strictly to `YYYY/MM/DD`.
- **Hybrid Categorization**:
  - Uses the Google GenAI (`gemini-2.5-flash`) for smart transaction categorization.
  - Gracefully degrades to a local regex/keyword-based heuristic engine upon API failures (e.g., rate limits, missing keys, or timeouts).
- **Inter-account Transfer Detection**: Automatically detects possible transfers between the user's accounts.
- **Strict Output Format**: Generates an 8-column `.tsv` file matching exactly what Money Manager expects.

## Prerequisites

- Python 3.9 or higher
- The required Python packages (see `requirements.txt`).

## Installation

1. Clone or download this repository.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create a `.env` file in the root directory and add your Google Gemini API key (optional but recommended for smart categorization):
   ```env
   GEMINI_API_KEY=your_actual_api_key_here
   ```

## Usage

You can run the script against a single PDF file or a directory containing multiple PDFs.

```bash
python main.py --input-dir /path/to/statements --output-dir /path/to/export
```

### CLI Arguments

- `--input-dir`: (Required) Path to a directory containing PDF statements or a specific PDF file.
- `--output-dir`: (Optional) Directory to save the exported `money_manager_export.tsv`. Defaults to the current working directory.
- `--api-key`: (Optional) Google Gemini API key. If not provided, it will check the `GEMINI_API_KEY` environment variable. If neither is present, the script defaults to local regex rules.

### Output
The tool produces a tab-separated values (`.tsv`) file with the following headers:
`Date`, `Account`, `Main Category`, `Sub Category`, `Note`, `Amount`, `Type`, `Description`.

## Development and Testing

To run the unit tests, ensure you have `pytest` installed and run:

```bash
pytest tests/
```
