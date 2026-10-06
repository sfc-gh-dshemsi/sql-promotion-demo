"""Render setup SQL to stdout only. Does not connect or execute SQL."""

import argparse
import json
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]


def setup_sql(qa_subject, prod_subject):
    for subject, suffix in ((qa_subject, "qa"), (prod_subject, "prod")):
        if (not re.fullmatch(r"repo:[A-Za-z0-9_.@/-]+:environment:[a-z]+", subject)
                or not subject.endswith(f":environment:{suffix}")):
            raise ValueError(f"supply the exact GitHub subject ending in environment:{suffix}")
    orders = json.loads((ROOT / "fixtures/orders.json").read_text())
    for order in orders:
        if (not re.fullmatch(r"O[0-9]+", order["order_id"])
                or order["status"] not in ("completed", "cancelled")
                or not re.fullmatch(r"[0-9]+\.[0-9]{2}", order["amount"])):
            raise ValueError("invalid synthetic fixture")
    templates = Environment(loader=FileSystemLoader(str(ROOT / "setup")),
                            undefined=StrictUndefined, autoescape=False,
                            keep_trailing_newline=True)
    return templates.get_template("setup.sql.j2").render(
        qa_subject=qa_subject, prod_subject=prod_subject, orders=orders)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-subject", required=True)
    parser.add_argument("--prod-subject", required=True)
    args = parser.parse_args()
    try:
        print(setup_sql(args.qa_subject, args.prod_subject))
    except ValueError as error:
        parser.error(str(error))