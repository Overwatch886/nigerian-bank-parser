import csv
import re
from typing import List, Tuple
from parser.ingest import ParsedTransaction
from parser.classifier import CategoryResult

def export_to_tsv(transactions: List[Tuple[ParsedTransaction, CategoryResult]], output_path: str):
    headers = [
        "Date",
        "Account",
        "Main Category",
        "Sub Category",
        "Note",
        "Amount",
        "Type",
        "Description"
    ]

    with open(output_path, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(headers)

        for tx, cat in transactions:
            # Clean description
            # Strip any tabs, carriage returns, or excessive whitespace
            clean_desc = re.sub(r'[\t\r\n]+', ' ', tx.description)
            clean_desc = re.sub(r'\s+', ' ', clean_desc).strip()

            row = [
                tx.date,
                tx.account,
                cat.main_category,
                cat.sub_category,
                "", # Note
                f"{tx.amount:.2f}",
                tx.type,
                clean_desc
            ]
            writer.writerow(row)
