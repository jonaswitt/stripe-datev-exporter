import json
from stripe_datev import recognition, csv
import pytz
import stripe
import decimal, math
from datetime import datetime, timezone, timedelta
from . import customer, output, dateparser
import datedelta
import os, os.path

accounting_tz = output.berlin

def listFinalizedInvoices(fromTime, toTime, cache=True):
  starting_after = None
  invoices = []
  while True:
    response = stripe.Invoice.list(
      starting_after=starting_after,
      created={
        "lt": int(toTime.timestamp())
      },
      due_date={
        "gte": int(fromTime.timestamp()),
      },
      limit=50,
      expand=["data.customer"],
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
      invoices.append(invoice)

    if not response.has_more:
      break

  return list(reversed(invoices))

def listInvoices(fromTime, toTime):
  invoiceRecords = []
  for invoice in listFinalizedInvoices(fromTime, toTime):
    record = {
      "raw": invoice
    }

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
  nextMonth = toTime + timedelta(seconds=7200)
  for invoice in invoices:
    # print(invoice)

    prefix = "Invoice {}".format(invoice["invoice_number"])
    record = {
      "date": invoice["date"],
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(invoice["total"]),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": customer.getCustomerAccount(invoice["customer"], invoice=invoice),
      "Gegenkonto (ohne BU-Schlüssel)": customer.getRevenueAccount(invoice["customer"], invoice),
      "BU-Schlüssel": customer.getDatevTaxKey(invoice["customer"], invoice),
      # "Belegdatum": output.formatDateDatev(invoice["date"]),
      # "Belegfeld 1": invoice["invoice_number"],
      "Buchungstext": prefix,

      # "Beleginfo - Art 1": "Belegnummer",
      # "Beleginfo - Inhalt 1": invoice["invoice_number"],

      # "Beleginfo - Art 2": "Produkt",
      # "Beleginfo - Inhalt 2": lineItem["description"],

      # "Beleginfo - Art 3": "Gegenpartei",
      # "Beleginfo - Inhalt 3": invoice["customer"]["name"],

      # "Beleginfo - Art 4": "Rechnungsnummer",
      # "Beleginfo - Inhalt 4": invoice["invoice_number"],

      # "Beleginfo - Art 5": "Betrag",
      # "Beleginfo - Inhalt 5": output.formatDecimal(lineItem["amount"]),

      # "Beleginfo - Art 6": "Umsatzsteuer",
      # "Beleginfo - Inhalt 6": invoice.get("tax_percent", 0),

      # "Beleginfo - Art 7": "Rechnungsdatum",
      # "Beleginfo - Inhalt 7": output.formatDateHuman(invoice["date"]),

      # "EU-Land u. UStID": invoice["customer"]["vat_id"],
      # "EU-Steuersatz": invoice.get("tax_percent", ""),

    }
    records.append(record)

    for lineItem in invoice["lines"]:
      currentPeriodAmount = lineItem["amount_discounted"]
      nextPeriodAmount = None
      if "period_end" in lineItem and lineItem["period_end"] > toTime:
        percentInCurrentPeriod = (toTime - lineItem["period_start"]) / (lineItem["period_end"] - lineItem["period_start"])
        nextPeriodAmount = decimal.Decimal(float(currentPeriodAmount) * (1 - percentInCurrentPeriod) * 100).to_integral_exact() / 100

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

  return records

def to_csv(inv):
  lines = [[
    "invoice_id",
    "invoice_number",
    "date",

    "total_before_tax",
    "tax",
    "tax_percent",
    "total",

    "customer_id",
    "customer_name",
    "country",
    "vat_region",
    "vat_id",
    "tax_exempt",

    "customer_account",
    "revenue_account",
    "datev_tax_key",
  ]]
  for invoice in inv:
    props = customer.getAccountingProps(invoice["customer"], invoice)
    lines.append([
      invoice["invoice_id"],
      invoice["invoice_number"],
      invoice["date"].astimezone(output.berlin).strftime("%Y-%m-%d"),

      format(invoice["total_before_tax"], ".2f"),
      format(invoice["tax"], ".2f") if "tax" in invoice else None,
      format(invoice["tax_percent"], ".0f") if "tax_percent" in invoice else None,
      format(invoice["total"], ".2f"),

      invoice["customer"]["id"],
      invoice["customer"]["name"],
      props["country"],
      props["vat_region"],
      props["vat_id"],
      props["tax_exempt"],

      props["customer_account"],
      props["revenue_account"],
      props["datev_tax_key"],
    ])

  return csv.lines_to_csv(lines)

def to_recognized_month_csv(invoices):
  lines = [[
    "invoice_id",
    "invoice_number",
    "invoice_date",
    "recognition_start",
    "recognition_end",
    "recognition_month",

    "line_item_idx",
    "line_item_desc",
    "line_item_net",

    "customer_id",
    "customer_name",
    "country",

    "accounting_date",
  ]]
  for inv2 in invoices:
    inv = inv2["raw"]

    props = customer.getAccountingProps(inv2["customer"], inv2)

    invoice_discount = decimal.Decimal(((inv.get("discount", None) or {}).get("coupon", None) or {}).get("percent_off", 0))

    created = accounting_tz.localize(datetime.fromtimestamp(inv["created"])) if "created" in inv else None
    for line_item_idx, line_item in enumerate(reversed(inv.get("lines", {}).get("data", []))):
      start = None
      end = None
      if "period" in line_item:
        start = datetime.fromtimestamp(line_item["period"]["start"]).replace(tzinfo=pytz.utc)
        end = datetime.fromtimestamp(line_item["period"]["end"]).replace(tzinfo=pytz.utc)
      if start == end:
        start = None
        end = None

      # if start is None and end is None:
      #   desc_parts = line_item.get("description", "").split(";")
      #   if len(desc_parts) >= 3:
      #     date_parts = desc_parts[-1].strip().split(" ")
      #     start = accounting_tz.localize(datetime.strptime("{} {} {}".format(date_parts[1], date_parts[2][:-2], date_parts[3]), "%b %d %Y"))
      #     end = start + timedelta(seconds=24 * 60 * 60 - 1)

      if start is None and end is None:
        try:
          date_range = dateparser.find_date_range(line_item.get("description"), created, accounting_tz)
          if date_range is not None:
            start, end = date_range

        except Exception as ex:
          print(ex)
          pass

      if start is None and end is None:
        print("Warning: unknown period for line item --", inv["id"], line_item.get("description"))
        start = created
        end = created

      # else:
      #   print("Period", start, end, "--", line_item.get("description"))

      invoice_date = inv2["date"].astimezone(output.berlin)

      for month in recognition.split_months(start, end, [decimal.Decimal(line_item["amount"]) / 100 * (1 - invoice_discount / 100)]):
        month_start = month["start"]
        if month_start.year <= invoice_date.year:
          accounting_date = invoice_date
        else:
          accounting_date = datetime(month_start.year, 1, 1, tzinfo=output.berlin)

        lines.append([
          inv2["invoice_id"],
          inv2["invoice_number"],
          invoice_date.strftime("%Y-%m-%d"),
          start.strftime("%Y-%m-%d"),
          end.strftime("%Y-%m-%d"),
          month["start"].strftime("%Y-%m") + "-01",

          str(line_item_idx + 1),
          line_item.get("description"),
          format(month["amounts"][0], ".2f"),

          inv2["customer"]["id"],
          inv2["customer"]["name"],
          props["country"],

          accounting_date.strftime("%Y-%m-%d"),
        ])

  return csv.lines_to_csv(lines)


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
