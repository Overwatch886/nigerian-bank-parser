import argparse
import os
import glob
from dotenv import load_dotenv

load_dotenv()

from parser.ingest import parse_statement
from parser.classifier import categorize_transaction, ensure_local_model_running, GENAI_AVAILABLE
from parser.exporter import export_to_tsv

if GENAI_AVAILABLE:
    from google import genai

def main():
    parser = argparse.ArgumentParser(description="Bank Statement Parser CLI for Money Manager")
    parser.add_argument("--input-dir", type=str, required=True, help="Directory containing PDF statements or a single PDF file path")
    parser.add_argument("--output-dir", type=str, default=os.getcwd(), help="Directory to save the exported TSV")
    parser.add_argument("--api-key", type=str, help="Google Gemini API Key (optional)")

    args = parser.parse_args()

    input_paths = []
    if os.path.isfile(args.input_dir):
        if args.input_dir.lower().endswith('.pdf'):
            input_paths.append(args.input_dir)
    elif os.path.isdir(args.input_dir):
        input_paths = glob.glob(os.path.join(args.input_dir, "*.pdf"))
    else:
        print(f"Error: {args.input_dir} is not a valid file or directory.")
        return

    if not input_paths:
        print("No PDF files found to process.")
        return

    # Setup genai client if available
    client = None
    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if GENAI_AVAILABLE and api_key:
        try:
            client = genai.Client(api_key=api_key)
        except Exception as e:
            print(f"Warning: Failed to initialize Google GenAI client: {e}. Falling back to regex rules.")
            client = None
    elif not api_key:
        print("No API key provided. Defaulting to local regex rules.")

    ensure_local_model_running()

    all_transactions = []

    total_files = len(input_paths)
    total_tx = 0
    total_debits = 0.0
    total_credits = 0.0

    for path in input_paths:
        print(f"Processing {path}...")
        try:
            transactions = parse_statement(path)
            for tx in transactions:
                # categorize
                tx, cat_result = categorize_transaction(tx, client)
                all_transactions.append((tx, cat_result))

                total_tx += 1
                if tx.type == "Expense":
                    total_debits += tx.amount
                elif tx.type == "Income":
                    total_credits += tx.amount

        except Exception as e:
            print(f"Error processing {path}: {e}")

    if all_transactions:
        output_file = os.path.join(args.output_dir, "money_manager_export.tsv")
        export_to_tsv(all_transactions, output_file)

        print("\n--- Summary ---")
        print(f"Total Files Processed: {total_files}")
        print(f"Total Transactions Parsed: {total_tx}")
        print(f"Total Debits: {total_debits:.2f}")
        print(f"Total Credits: {total_credits:.2f}")
        print(f"Exported to: {output_file}")
    else:
        print("No transactions were parsed.")

if __name__ == "__main__":
    main()
