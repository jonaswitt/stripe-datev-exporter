import stripe
import decimal
from datetime import datetime, timezone
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
    assert payout.status == "paid"
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
    text = "Stripe Payout {} / {}".format(payout["id"], payout["description"] or "")
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
