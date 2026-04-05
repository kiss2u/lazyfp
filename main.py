import os
import json
import logging
import pandas as pd
from datetime import datetime
from openpyxl.utils import get_column_letter
from core.rule_engine import RuleEngine
from core.orchestrator import Orchestrator
from core.cache import InvoiceCache

INPUT_DIR = "fp"
OUTPUT_FILE = "invoice_summary.xlsx"
LOG_FILE = "extraction.log"
CACHE_FILE = "invoice_cache.json"
RULES_FILE = "config/rules.json"
CUSTOM_RULES_FILE = "config/custom_rules.json"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

_rule_engine = None
_orchestrator = None
_cache = None


def _get_orchestrator() -> Orchestrator:
    global _orchestrator, _rule_engine
    if _orchestrator is None:
        _rule_engine = RuleEngine(RULES_FILE, CUSTOM_RULES_FILE)
        _orchestrator = Orchestrator(_rule_engine)
    return _orchestrator


def _get_cache() -> InvoiceCache:
    global _cache
    if _cache is None:
        _cache = InvoiceCache.get_instance(CACHE_FILE)
    return _cache


def extract_invoice_data(pdf_path: str) -> dict:
    return _get_orchestrator().extract(pdf_path)


def get_quarter(date_str: str) -> str:
    return _get_orchestrator().get_quarter(date_str)


def scan_directory(input_dir: str) -> list[dict]:
    cache = _get_cache()
    files = [f for f in os.listdir(input_dir) if f.lower().endswith('.pdf')]
    logging.info("Starting extraction for %d files found in '%s'...", len(files), input_dir)

    data_list = []
    current_files = set()
    updated_cache = False

    for filename in files:
        file_path = os.path.join(input_dir, filename)
        current_files.add(filename)

        file_stat = os.stat(file_path)
        last_mod = file_stat.st_mtime
        file_size = file_stat.st_size

        cached = cache.get(filename, last_mod, file_size)
        if cached is not None:
            data_list.append(cached)
            continue

        try:
            res = extract_invoice_data(file_path)
            if res:
                data_list.append(res)
                cache.put(filename, last_mod, file_size, res)
                updated_cache = True
        except Exception as e:
            logging.error("Error processing %s: %s", filename, e)

    cache.cleanup(current_files)
    if updated_cache:
        cache.save()

    return data_list


def process_invoices(input_dir: str) -> pd.DataFrame:
    data_list = scan_directory(input_dir)
    df = pd.DataFrame(data_list)

    if df.empty:
        return pd.DataFrame()

    df = df.fillna("")

    for col in ["invoice_no", "date", "purchaser", "seller", "total_amount", "quarter", "filename"]:
        if col not in df.columns:
            df[col] = ""

    df_valid = df[df["invoice_no"] != ""]
    df_invalid = df[df["invoice_no"] == ""]

    agg_funcs = {
        'date': 'first',
        'purchaser': 'first',
        'seller': 'first',
        'total_amount': 'first',
        'quarter': 'first',
        'filename': lambda x: ", ".join(x)
    }

    if not df_valid.empty:
        df_valid = df_valid.groupby("invoice_no", as_index=False).agg(agg_funcs)
        df_valid["count"] = df_valid["filename"].apply(lambda x: len(x.split(", ")))
    else:
        df_valid = pd.DataFrame(columns=list(df.columns) + ['count'])

    df_final = pd.concat([df_valid, df_invalid], ignore_index=True)
    if "count" not in df_final.columns:
        df_final["count"] = 1

    df_final["count"] = df_final["count"].fillna(1).astype(int)
    df_final["quarter"] = df_final["date"].apply(lambda x: get_quarter(str(x)))
    df_final = df_final.sort_values(by=["quarter", "purchaser"])

    return df_final


def main():
    df_final = process_invoices(INPUT_DIR)

    if df_final.empty:
        return

    cols = ["invoice_no", "purchaser", "seller", "total_amount", "date", "quarter", "count", "filename"]

    try:
        with pd.ExcelWriter(OUTPUT_FILE, engine='openpyxl') as writer:
            df_final[cols].to_excel(writer, index=False, sheet_name='Invoices')
            worksheet = writer.sheets['Invoices']
            for column in worksheet.columns:
                max_length = 0
                column = [cell for cell in column]
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except (ValueError, TypeError):
                        pass
                adjusted_width = (max_length + 2)
                worksheet.column_dimensions[get_column_letter(column[0].column)].width = min(adjusted_width, 50)

        logging.info("Successfully exported %d records to %s", len(df_final), OUTPUT_FILE)

    except Exception as e:
        logging.error("Failed to write Excel file: %s", e)


if __name__ == "__main__":
    main()
