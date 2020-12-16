import stripe
import decimal, math
from datetime import datetime, timezone, timedelta
from . import customer, output
import datedelta

def listFinalizedInvoices(fromTime, toTime):
  starting_after = None
  while True:
    response = stripe.Invoice.list(
      starting_after=starting_after,
      created={
        "lt": int(toTime.timestamp())
      },
      due_date={
        "gte": int(fromTime.timestamp()),
      },
      limit=10,
    )
    # print("Fetched {} invoices".format(len(response.data)))
    if len(response.data) == 0:
      break
    starting_after = response.data[-1].id
    for invoice in response.data:
      if invoice.status == "draft" or invoice.status == "void":
        continue
      created_date = datetime.fromtimestamp(invoice.created, timezone.utc)
      due_date = datetime.fromtimestamp(invoice.due_date, timezone.utc)
      finalized_date = datetime.fromtimestamp(invoice.status_transitions.finalized_at, timezone.utc)
      if finalized_date < fromTime or finalized_date >= toTime:
        # print("Skipping invoice {}, created {} finalized {} due {}".format(invoice.id, created_date, finalized_date, due_date))
        continue
      yield invoice

def listInvoices(fromTime, toTime):
  invoiceRecords = []
  for invoice in listFinalizedInvoices(fromTime, toTime):
    record = {}

    if invoice.post_payment_credit_notes_amount == invoice.total:
      if invoice.total > 0:
        print("Warning: Invoice {} has been fully refunded, skipping".format(invoice.id))
      continue
    elif invoice.post_payment_credit_notes_amount > 0:
      print("Warning: Invoice {} partially refunded - please check".format(invoice.id))
    assert invoice.currency == "eur"

    record["invoice_id"] = invoice.id
    record["invoice_pdf"] = invoice.invoice_pdf
    record["invoice_number"] = invoice.number

    finalized_date = datetime.fromtimestamp(invoice.status_transitions.finalized_at, timezone.utc)
    record["date"] = finalized_date
    record["total"] = decimal.Decimal(invoice.total) / 100
    record["subtotal"] = decimal.Decimal(invoice.subtotal) / 100
    if invoice.tax:
      record["tax"] = decimal.Decimal(invoice.tax) / 100
      record["tax_percent"] = decimal.Decimal(invoice.tax_percent)
      record["total_before_tax"] = record["total"] - record["tax"]
    else:
      record["total_before_tax"] = record["total"]

    record["customer_tax_exempt"] = invoice.customer_tax_exempt
    record["charge_id"] = invoice.charge

    lines = []
    for line in invoice.lines:
      # print(line)
      amount = decimal.Decimal(line.amount) / 100
      lineRecord = {
        "amount": amount,
        "amount_discounted": amount * record["total_before_tax"] / record["subtotal"],
        "description": line.description,
      }
      if "period" in line:
        lineRecord["period_start"] = datetime.fromtimestamp(line.period.start, timezone.utc)
        lineRecord["period_end"] = datetime.fromtimestamp(line.period.end, timezone.utc)
      lines.append(lineRecord)
    record["lines"] = lines

    record["customer"] = customer.getCustomerDetails(invoice.customer)

    invoiceRecords.append(record)

  print("Retrieved {} invoice(s), total {} EUR".format(len(invoiceRecords), sum([r["total"] for r in invoiceRecords])))
  return invoiceRecords

def createAccountingRecords(invoices, fromTime, toTime):
  records = []
  nextMonth = toTime + timedelta(1)
  for invoice in invoices:
    # print(invoice)

    for lineItem in invoice["lines"]:
      # print(lineItem)
      currentPeriodAmount = lineItem["amount_discounted"]
      nextPeriodAmount = None

      if "period_end" in lineItem and lineItem["period_end"] > toTime:
        percentInCurrentPeriod = (toTime - lineItem["period_start"]) / (lineItem["period_end"] - lineItem["period_start"])
        nextPeriodAmount = decimal.Decimal(float(currentPeriodAmount) * (1 - percentInCurrentPeriod) * 100).to_integral_exact() / 100

      prefix = "Stripe Invoice {}".format(invoice["invoice_number"])
      text = "{} / {}".format(prefix, lineItem["description"])
      record = {
        "date": invoice["date"],
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(currentPeriodAmount),
        "Soll/Haben-Kennzeichen": "S",
        "WKZ Umsatz": "EUR",
        "Konto": customer.getCustomerAccount(invoice["customer"]),
        "Gegenkonto (ohne BU-Schlüssel)": customer.getRevenueAccount(invoice["customer"], invoice),
        "BU-Schlüssel": customer.getDatevTaxKey(invoice["customer"], invoice),
        # "Belegdatum": output.formatDateDatev(invoice["date"]),
        # "Belegfeld 1": invoice["invoice_number"],
        "Buchungstext": text,

        # "Beleginfo - Art 1": "Belegnummer",
        # "Beleginfo - Inhalt 1": invoice["invoice_number"],

        # "Beleginfo - Art 2": "Produkt",
        # "Beleginfo - Inhalt 2": lineItem["description"],

        "Beleginfo - Art 3": "Gegenpartei",
        "Beleginfo - Inhalt 3": invoice["customer"]["name"],

        "Beleginfo - Art 4": "Rechnungsnummer",
        "Beleginfo - Inhalt 4": invoice["invoice_number"],

        "Beleginfo - Art 5": "Betrag",
        "Beleginfo - Inhalt 5": output.formatDecimal(lineItem["amount"]),

        # "Beleginfo - Art 6": "Umsatzsteuer",
        # "Beleginfo - Inhalt 6": invoice.get("tax_percent", 0),

        "Beleginfo - Art 7": "Rechnungsdatum",
        "Beleginfo - Inhalt 7": output.formatDateHuman(invoice["date"]),

        # "EU-Land u. UStID": invoice["customer"]["vat_id"],
        # "EU-Steuersatz": invoice.get("tax_percent", ""),

      }
      records.append(record)

      if nextPeriodAmount is not None:
        text = "{} / Anteilig Rueckstellung".format(prefix, lineItem["description"])
        rueckstRecord = {
          "date": invoice["date"],
          "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(nextPeriodAmount),
          "Soll/Haben-Kennzeichen": "S",
          "WKZ Umsatz": "EUR",
          "Konto": customer.getRevenueAccount(invoice["customer"], invoice),
          "Gegenkonto (ohne BU-Schlüssel)": "990",
          # "Belegdatum": output.formatDateDatev(invoice["date"]),
          # "Belegfeld 1": invoice["invoice_number"],
          "Buchungstext": text,
        }
        records.append(rueckstRecord)

        text = "{} / Aus Vormonat".format(prefix, lineItem["description"])
        auflRecord = {
          "date": nextMonth,
          "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(nextPeriodAmount),
          "Soll/Haben-Kennzeichen": "S",
          "WKZ Umsatz": "EUR",
          "Konto": "990",
          "Gegenkonto (ohne BU-Schlüssel)": customer.getRevenueAccount(invoice["customer"], invoice),
          # "Belegdatum": output.formatDateDatev(nextMonth),
          # "Belegfeld 1": invoice["invoice_number"],
          "Buchungstext": text,
        }
        records.append(auflRecord)

    tax = invoice.get("tax", 0)
    if tax > 0:
      text = "{} / Umsatzsteuer".format(prefix)
      record = {
        "date": invoice["date"],
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(tax),
        "Soll/Haben-Kennzeichen": "S",
        "WKZ Umsatz": "EUR",
        "Konto": customer.getCustomerAccount(invoice["customer"]),
        "Gegenkonto (ohne BU-Schlüssel)": "1777",
        "Buchungstext": text,
        "Beleginfo - Art 3": "Gegenpartei",
        "Beleginfo - Inhalt 3": invoice["customer"]["name"],
        "Beleginfo - Art 4": "Rechnungsnummer",
        "Beleginfo - Inhalt 4": invoice["invoice_number"],
        "Beleginfo - Art 5": "Betrag",
        "Beleginfo - Inhalt 5": output.formatDecimal(lineItem["amount"]),
        "Beleginfo - Art 7": "Rechnungsdatum",
        "Beleginfo - Inhalt 7": output.formatDateHuman(invoice["date"]),
      }
      records.append(record)

  return records

def roundCentsDown(dec):
  return math.floor(dec * 100) / 100

def accrualRecords(invoiceDate, invoiceAmount, customerAccount, revenueAccount, text, firstRevenueDate, revenueSpreadMonths, includeOriginalInvoice=True):
  records = []

  if includeOriginalInvoice:
    records.append({
      "date": invoiceDate,
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(invoiceAmount),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": str(customerAccount),
      "Gegenkonto (ohne BU-Schlüssel)": str(revenueAccount),
      "Buchungstext": text,
    })

  revenuePerPeriod = roundCentsDown(invoiceAmount / revenueSpreadMonths)
  if invoiceDate < firstRevenueDate:
    accrueAmount = invoiceAmount
    accrueText = "{} / Rueckstellung ({} Monate)".format(text, revenueSpreadMonths)
    periodsBooked = 0
    periodDate = firstRevenueDate
  else:
    accrueAmount = invoiceAmount - revenuePerPeriod
    accrueText = "{} / Rueckstellung Anteilig ({}/{} Monate)".format(text, revenueSpreadMonths-1, revenueSpreadMonths)
    periodsBooked = 1
    periodDate = firstRevenueDate + datedelta.MONTH

  records.append({
    "date": invoiceDate,
    "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(accrueAmount),
    "Soll/Haben-Kennzeichen": "S",
    "WKZ Umsatz": "EUR",
    "Konto": str(revenueAccount),
    "Gegenkonto (ohne BU-Schlüssel)": "990",
    "Buchungstext": accrueText,
  })

  remainingAmount = accrueAmount

  while periodsBooked < revenueSpreadMonths:
    if periodsBooked < revenueSpreadMonths - 1:
      periodAmount = revenuePerPeriod
    else:
      periodAmount = remainingAmount

    records.append({
      "date": periodDate,
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(periodAmount),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": "990",
      "Gegenkonto (ohne BU-Schlüssel)": str(revenueAccount),
      "Buchungstext": "{} / Aufloesung Rueckstellung Monat {}/{}".format(text, periodsBooked+1, revenueSpreadMonths),
    })

    periodDate = periodDate + datedelta.MONTH
    periodsBooked += 1
    remainingAmount -= periodAmount

  return records
