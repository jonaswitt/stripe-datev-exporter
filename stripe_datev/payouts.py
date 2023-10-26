import decimal
from datetime import datetime, timezone

import stripe

from . import output


def listPayouts(fromTime, toTime):
  payouts = stripe.Payout.list(
    created={
      "gte": int(fromTime.timestamp()),
      "lt": int(toTime.timestamp())
    },
    expand=["data.balance_transaction"]
  ).auto_paging_iter()

  for payout in payouts:
    if payout.status != "paid":
      continue
    assert payout.currency == "eur"
    balance_transaction = payout.balance_transaction
    assert len(balance_transaction.fee_details) == 0

    record = {
      "id": payout.id,
      "amount": decimal.Decimal(payout.amount) / 100,
      "arrival_date": datetime.fromtimestamp(payout.created, timezone.utc),
      "description": payout.description,
    }
    yield record


def createAccountingRecords(payouts):
  records = []
  for payout in payouts:
    text = "Stripe Payout {} / {}".format(
      payout["id"], payout["description"] or "")
    record = {
      "date": payout["arrival_date"],
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(payout["amount"]),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": "1360",
      "Gegenkonto (ohne BU-Schlüssel)": "1201",
      # "BU-Schlüssel": "0",
      # "Belegdatum": output.formatDateDatev(payout["arrival_date"]),
      # "Belegfeld 1": payout["id"],
      "Buchungstext": text,

      # # "Beleginfo - Art 1": "Belegnummer",
      # # "Beleginfo - Inhalt 1": invoice["invoice_number"],

      # # "Beleginfo - Art 2": "Produkt",
      # # "Beleginfo - Inhalt 2": lineItem["description"],

      # "Beleginfo - Art 3": "Gegenpartei",
      # "Beleginfo - Inhalt 3": invoice["customer"]["name"],

      # "Beleginfo - Art 4": "Rechnungsnummer",
      # "Beleginfo - Inhalt 4": invoice["invoice_number"],

      # "Beleginfo - Art 5": "Betrag",
      # "Beleginfo - Inhalt 5": output.formatDecimal(payout["amount"]),

      # "Beleginfo - Art 6": "Umsatzsteuer",
      # "Beleginfo - Inhalt 6": 0,

      # "Beleginfo - Art 7": "Rechnungsdatum",
      # "Beleginfo - Inhalt 7": output.formatDateHuman(invoice["date"]),

      # "EU-Land u. UStID": invoice["customer"]["vat_id"],
      # "EU-Steuersatz": invoice.get("tax_percent", ""),

    }
    records.append(record)
  return records


def createAccountingRecordsContributions(balance_transactions):
  records = []
  for balance_transaction in balance_transactions:
    record = {
      "date": datetime.fromtimestamp(balance_transaction["created"], timezone.utc),
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(-decimal.Decimal(balance_transaction["amount"]) / 100),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": "4600",
      "Gegenkonto (ohne BU-Schlüssel)": "1201",
      # "BU-Schlüssel": "0",
      # "Belegdatum": output.formatDateDatev(payout["arrival_date"]),
      # "Belegfeld 1": payout["id"],
      "Buchungstext": "Stripe {} {}".format(balance_transaction["description"] or "Contribution", balance_transaction["id"]),
    }
    records.append(record)
  return records
