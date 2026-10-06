# Money Manager Bank Statement Parser

A resilient, headless Python CLI tool designed to parse Nigerian bank statement PDFs (Access Bank, Kuda, and OPay) and export them as an 8-column TSV file compatible with the Money Manager app.

## Features

- **PDF Data Extraction**: Uses `pdfplumber` to accurately read PDF statements.
- **Multiline Normalization**: Reconstructs broken transaction narrations spanning multiple lines.
- **Data Standardization**: Normalizes amounts to absolute positive floats and formats dates strictly to `YYYY/MM/DD`.
- **Hybrid Categorization**:
  - Uses Google GenAI models in order, starting with `gemini-3.5-flash-lite`, then `gemini-3.1-flash-lite`, followed by Gemma 3 models and `gemini-2.5-flash`.
  - Skips models that are rate-limited for the rest of the run, then gracefully degrades to local regex/keyword rules if every configured model is unavailable.
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

The model order can be overridden without changing code:

```env
GEMINI_MODELS=gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemma-3-27b-it,gemma-3-12b-it,gemma-3-4b-it,gemini-2.5-flash
```

These environment settings belong in the `.env` file at the repository root
(the same directory as `main.py`). The optional local-model settings described
below also go in this file:

```env
LOCAL_MODEL_ENABLED=true
LOCAL_MODEL_ENDPOINT=http://127.0.0.1:8080/v1/chat/completions
LOCAL_MODEL_NAME=granite-4.0-h-tiny
LOCAL_MODEL_TIMEOUT=30
LOCAL_MODEL_START_SCRIPT=C:\developer\scripts\run_granite.bat
LOCAL_MODEL_STARTUP_TIMEOUT=30
```

The Granite launcher at `C:\developer\scripts\run_granite.bat` starts the
llama.cpp server used by these settings. When local mode is enabled, the
parser checks the server's `/health` endpoint and starts this script if the
server is not already running.
The local model is tried after Gemini rotation and before regex rules. If the
local endpoint fails, it is disabled for the rest of that run and the parser
falls back to regex rules.

### Output
The tool produces a tab-separated values (`.tsv`) file with the following headers:
`Date`, `Account`, `Main Category`, `Sub Category`, `Note`, `Amount`, `Type`, `Description`.

## Development and Testing

To run the unit tests, ensure you have `pytest` installed and run:

```bash
pytest tests/
```
