import decimal
from datetime import datetime, timezone

import stripe

from . import config, customer, dateparser, invoices, output


def listTransfersRaw(fromTime, toTime):
  transfers = stripe.Transfer.list(
    created={
      "gte": int(fromTime.timestamp()),
      "lt": int(toTime.timestamp())
    },
    expand=["data.destination", "data.source_transaction",
            "data.source_transaction.invoice"]
  ).auto_paging_iter()
  for transfer in transfers:
    if transfer.reversed:
      continue
    yield transfer


def createAccountingRecords(transfers):
  records = []

  for transfer in transfers:
    created = datetime.fromtimestamp(
      transfer.created, timezone.utc).astimezone(config.accounting_tz)

    net_amount = transfer.amount - \
        (transfer.source_transaction.application_fee_amount or 0)

    invoice = transfer.source_transaction.get("invoice", None)
    invoiceNumber = invoice.number if invoice else None

    records.append({
      "date": created,
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(decimal.Decimal(net_amount) / 100),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": "4909",
      "Gegenkonto (ohne BU-Schlüssel)": transfer["destination"]["metadata"]["accountNumber"],
      "Buchungstext": "Fremdleistung {} anteilig".format(invoiceNumber or transfer.id),
      "Belegfeld 1": transfer.id,
    })

    records.append({
      "date": created,
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(decimal.Decimal(net_amount) / 100),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": transfer["destination"]["metadata"]["accountNumber"],
      "Gegenkonto (ohne BU-Schlüssel)": "1201",
      "Buchungstext": "Fremdleistung {} anteilig".format(invoiceNumber or transfer.id),
      "Belegfeld 1": transfer.id,
    })

  return records
