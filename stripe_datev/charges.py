import stripe
import decimal
from datetime import datetime, timezone
from . import customer, output

def listCharges(fromTime, toTime):
  starting_after = None
  chargeRecords = []
  while True:
    response = stripe.Charge.list(
      starting_after=starting_after,
      created={
        "gte": int(fromTime.timestamp()),
        "lt": int(toTime.timestamp())
      },
      limit=50,
    )
    if len(response.data) == 0:
      break
    starting_after = response.data[-1].id

    for charge in response.data:
      if not charge.paid:
        continue
      if charge.refunded:
        continue

      record = {
        "id": charge.id,
        "amount": decimal.Decimal(charge.amount) / 100,
        "created": datetime.fromtimestamp(charge.created, timezone.utc),
        "description": charge.description,
      }

      record["customer"] = customer.getCustomerDetails(stripe.Customer.retrieve(charge.customer))

      balance_transaction = stripe.BalanceTransaction.retrieve(charge.balance_transaction)
      # print(balance_transaction)
      assert len(balance_transaction.fee_details) == 1
      assert balance_transaction.fee_details[0].currency == "eur"
      record["fee_amount"] = decimal.Decimal(balance_transaction.fee_details[0].amount) / 100
      record["fee_desc"] = balance_transaction.fee_details[0].description

      chargeRecords.append(record)

    if not response.has_more:
      break

  print("Retrieved {} charge(s), total {} EUR (fees: {} EUR)".format(len(chargeRecords), sum([r["amount"] for r in chargeRecords]), sum([r["fee_amount"] for r in chargeRecords])))
  return chargeRecords

def createAccountingRecords(charges):
  records = []
  for charge in charges:
    text = "{} {}".format(charge["description"] or "Stripe Payment", charge["id"])
    record = {
      "date": charge["created"],
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(charge["amount"]),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": "1201",
      "Gegenkonto (ohne BU-Schlüssel)": customer.getCustomerAccount(charge["customer"]),
      # "BU-Schlüssel": "0",
      # "Belegdatum": output.formatDateDatev(charge["created"]),
      # "Belegfeld 1": charge["id"],
      "Buchungstext": text,

      # # "Beleginfo - Art 1": "Belegnummer",
      # # "Beleginfo - Inhalt 1": invoice["invoice_number"],

      # # "Beleginfo - Art 2": "Produkt",
      # # "Beleginfo - Inhalt 2": lineItem["description"],

      # "Beleginfo - Art 3": "Gegenpartei",
      # "Beleginfo - Inhalt 3": invoice["customer"]["name"],

      # "Beleginfo - Art 4": "Rechnungsnummer",
      # "Beleginfo - Inhalt 4": invoice["invoice_number"],

      "Beleginfo - Art 5": "Betrag",
      "Beleginfo - Inhalt 5": output.formatDecimal(charge["amount"]),

      # "Beleginfo - Art 6": "Umsatzsteuer",
      # "Beleginfo - Inhalt 6": 0,

      # "Beleginfo - Art 7": "Rechnungsdatum",
      # "Beleginfo - Inhalt 7": output.formatDateHuman(invoice["date"]),

      # "EU-Land u. UStID": invoice["customer"]["vat_id"],
      # "EU-Steuersatz": invoice.get("tax_percent", ""),

    }
    records.append(record)

    text = "{} {} {}".format(charge["description"] or "", charge["fee_desc"] or "Stripe Fee", charge["id"])
    record = {
      "date": charge["created"],
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(charge["fee_amount"]),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": "70025",
      "Gegenkonto (ohne BU-Schlüssel)": "1201",
      "Buchungstext": text,
      "Beleginfo - Art 5": "Betrag",
      "Beleginfo - Inhalt 5": output.formatDecimal(charge["fee_amount"]),
    }
    records.append(record)

    # text = "{} Reverse Charge IE3206488LH {}".format(charge["fee_desc"] or "Stripe Fee", charge["id"])
    # record = {
    #   "date": charge["created"],
    #   "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(charge["fee_amount"]),
    #   "Soll/Haben-Kennzeichen": "S",
    #   "WKZ Umsatz": "EUR",
    #   "Konto": "1577",
    #   "Gegenkonto (ohne BU-Schlüssel)": "1787",
    #   "Buchungstext": text,
    #   "Beleginfo - Art 5": "Betrag",
    #   "Beleginfo - Inhalt 5": output.formatDecimal(charge["fee_amount"]),
    # }
    # records.append(record)


  return records

