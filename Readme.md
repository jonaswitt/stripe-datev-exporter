# Stripe DATEV Exporter

## Requirements

- Tested on Python 3.9 - 3.12
- Dependencies listed in `pyproject.toml` (uv) and `requirements.txt` (pip)

## Environment

Consider using [uv](https://docs.astral.sh/uv/) or Python's [virtualenv](https://pypi.org/project/virtualenv/) or alternatives.

When using uv, in the commands below replace `python stripe-datev-cli.py` with `uv run stripe-datev-cli.py`.

## How to Use

```
python stripe-datev-cli.py fill_account_numbers
```

Run this before `download`. This assigns the `accountNumber` metadata to each customer, this will be the account number for all booking records related to this customer. You can assign the `accountNumber` metadata using a different approach, if you like, but every customer (which has any transactions) needs this metadata.

```
python stripe-datev-cli.py list_accounts
python stripe-datev-cli.py list_accounts <file>
```

Outputs a CSV file with all customers, suitable to import into DATEV as master data (Stammdaten). Skip the output file argument to output to stdout. Otherwise, the file is written in Latin1 encoding.

```
python stripe-datev-cli.py download <year> <month>
```

Processes all invoices, charges and transactions in the given month. Outputs DATEV records in `./out/datev`, CSV summaries in `./out/overview` and `./out/monthly_recognition`. Downloads PDF receipts to `./out/pdf`.

```
python stripe-datev-cli.py fees <year> <month>
```

Shows a summary of all Stripe fees and contributions accrued in the given month (uses UTC, as Stripe does in their invoices, instead of the local timezone)

```
python stripe-datev-cli.py opos
python stripe-datev-cli.py opos <year> <month> <date>
```

Shows all unpaid invoices as of now, or as of the end of the given date. Useful to verify the balance of pRAP accounts at the end of a year.

```
python stripe-datev-cli.py preview <in_123...>
python stripe-datev-cli.py preview <ch_123...>
python stripe-datev-cli.py preview <txn_123...>
```

Shows a preview of all accounting records stemming from one invoice/charge/transaction. Useful to diff output when making changes to the accounting record generation logic.
