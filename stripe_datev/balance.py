import stripe
import decimal
from datetime import datetime, timezone
from . import customer, output, config


def listBalanceTransactions(fromTime, toTime):
  return stripe.BalanceTransaction.list(
    created={
        "lt": int(toTime.timestamp()),
        "gte": int(fromTime.timestamp()),
      },
      expand=["data.source", "data.source.customer",
              "data.source.customer.tax_ids", "data.source.invoice", "data.source.charge",
              "data.source.charge.customer", "data.source.charge.invoice",
              "data.source.source_transaction", "data.source.source_transaction.invoice",
              "data.source.destination", "data.source.destination_payment"]
  ).auto_paging_iter()


def createAccountingRecords(balance_transactions):
  records = []
  for tx in balance_transactions:
    created = datetime.fromtimestamp(
      tx.created, timezone.utc).astimezone(config.accounting_tz)
    amount = decimal.Decimal(tx.amount) / 100
    fee = decimal.Decimal(tx.fee) / 100

    if tx["type"] == "charge" or tx["type"] == "payment":
      charge = tx.source
      cus = customer.retrieveCustomer(charge.customer)
      accounting_props = customer.getAccountingProps(cus)
      if charge.invoice:
        number = charge.invoice.number
      else:
        number = charge.receipt_number
      fee_desc = tx.fee_details[0].description

      records.append({
        "date": created,
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(amount),
        "Soll/Haben-Kennzeichen": "S",
        "WKZ Umsatz": "EUR",
        "Konto": str(config.accounts["bank"]),
        "Gegenkonto (ohne BU-Schlüssel)": accounting_props["customer_account"],
        "Buchungstext": "Stripe Payment ({})".format(charge.id),
        "Belegfeld 1": number,
      })

      records.append({
        "date": created,
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(fee),
        "Soll/Haben-Kennzeichen": "S",
        "WKZ Umsatz": "EUR",
        "Konto": str(config.accounts["stripe_fees"]),
        "Gegenkonto (ohne BU-Schlüssel)": str(config.accounts["bank"]),
        "Buchungstext": "{} ({})".format(fee_desc or "Stripe Fee", charge.id),
        # Stripe invoices fees within the bounds of one UTC month,
        # this makes it easier to associate a fee with a montly invoice
        "Belegfeld 1": created.astimezone(timezone.utc).strftime("%Y-%m"),
      })

    elif tx["type"] == "payout":
      records.append({
        "date": created,
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(-amount),
        "Soll/Haben-Kennzeichen": "S",
        "WKZ Umsatz": "EUR",
        "Konto": str(config.accounts["transit"]),
        "Gegenkonto (ohne BU-Schlüssel)": str(config.accounts["bank"]),
        "Buchungstext": "Stripe Payout {}".format(tx.source.id),
      })

    elif tx["type"] == "refund" or tx["type"] == "payment_refund":
      charge = tx.source.charge
      cus = customer.retrieveCustomer(charge.customer)
      accounting_props = customer.getAccountingProps(cus)
      if charge.invoice:
        number = charge.invoice.number
      else:
        number = charge.receipt_number

      records.append({
        "date": created,
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(-amount),
        "Soll/Haben-Kennzeichen": "H",
        "WKZ Umsatz": "EUR",
        "Konto": str(config.accounts["bank"]),
        "Gegenkonto (ohne BU-Schlüssel)": accounting_props["customer_account"],
        "Buchungstext": "Stripe Payment Refund ({})".format(charge.id),
        "Belegfeld 1": number,
      })

    elif tx["type"] == "contribution":
      records.append({
        "date": created,
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(-amount),
        "Soll/Haben-Kennzeichen": "S",
        "WKZ Umsatz": "EUR",
        "Konto": str(config.accounts["contributions"]),
        "Gegenkonto (ohne BU-Schlüssel)": str(config.accounts["bank"]),
        "Buchungstext": "Stripe {} {}".format(tx["description"] or "Contribution", tx["id"]),
        "Belegfeld 1": created.astimezone(timezone.utc).strftime("%Y-%m"),
      })

    elif tx["type"] == "transfer":
      transfer = tx.source
      net_amount = transfer.amount - \
          ((transfer.source_transaction.application_fee_amount if transfer.source_transaction else None) or 0)
      invoice = transfer.source_transaction.get(
        "invoice", None) if transfer.source_transaction else None
      invoiceNumber = invoice.number if invoice else None

      records.append({
        "date": created,
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(decimal.Decimal(net_amount) / 100),
        "Soll/Haben-Kennzeichen": "S",
        "WKZ Umsatz": "EUR",
        "Konto": str(config.accounts["external_services"]),
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
        "Gegenkonto (ohne BU-Schlüssel)": str(config.accounts["bank"]),
        "Buchungstext": "Fremdleistung {} anteilig".format(invoiceNumber or transfer.id),
        "Belegfeld 1": transfer.id,
      })

    elif tx["type"] == "stripe_fee":
      records.append({
        "date": created,
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(-amount),
        "Soll/Haben-Kennzeichen": "S",
        "WKZ Umsatz": "EUR",
        "Konto": str(config.accounts["stripe_fees"]),
        "Gegenkonto (ohne BU-Schlüssel)": str(config.accounts["bank"]),
        "Buchungstext": tx.description or "Stripe Fee",
        # Stripe invoices fees within the bounds of one UTC month,
        # this makes it easier to associate a fee with a montly invoice
        "Belegfeld 1": created.astimezone(timezone.utc).strftime("%Y-%m"),
      })

    elif tx["type"] == "payout_minimum_balance_hold" or tx["type"] == "payout_minimum_balance_release":
      # Not relevant for accounting on the company side
      pass

    else:
      print(
        "Warning: unsupported balance transaction type:", tx["type"], tx["id"])

  return records


def extractCharges(balance_transactions):
  charges = []
  for tx in balance_transactions:
    if tx["type"] == "charge" or tx["type"] == "payment":
      charges.append(tx.source)

  return charges
