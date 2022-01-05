import json
from stripe_datev import recognition, csv
import stripe
import decimal, math
from datetime import datetime, timezone
from . import customer, output, dateparser, config
import datedelta

invoices_cached = {}

def listFinalizedInvoices(fromTime, toTime):
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
      finalized_date = datetime.fromtimestamp(invoice.status_transitions.finalized_at, timezone.utc).astimezone(config.accounting_tz)
      if finalized_date < fromTime or finalized_date >= toTime:
        # print("Skipping invoice {}, created {} finalized {} due {}".format(invoice.id, created_date, finalized_date, due_date))
        continue
      invoices.append(invoice)
      invoices_cached[invoice.id] = invoice

    if not response.has_more:
      break

  return list(reversed(invoices))

def retrieveInvoice(id):
  if id in invoices_cached:
    return invoices_cached[id]
  invoice = stripe.Invoice.retrieve(id)
  invoices_cached[invoice.id] = invoice
  return invoice

def getLineItemRecognitionRange(line_item, invoice):
  created = datetime.fromtimestamp(invoice.created, timezone.utc)

  start = None
  end = None
  if "period" in line_item:
    start = datetime.fromtimestamp(line_item["period"]["start"], timezone.utc)
    end = datetime.fromtimestamp(line_item["period"]["end"], timezone.utc)
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
      date_range = dateparser.find_date_range(line_item.get("description"), created, tz=config.accounting_tz)
      if date_range is not None:
        start, end = date_range

    except Exception as ex:
      print(ex)
      pass

  if start is None and end is None:
    print("Warning: unknown period for line item --", invoice.id, line_item.get("description"))
    start = created
    end = created

  # else:
  #   print("Period", start, end, "--", line_item.get("description"))

  return start.astimezone(config.accounting_tz), end.astimezone(config.accounting_tz)

def createRevenueItems(invs):
  revenue_items = []
  for invoice in invs:
    cus = customer.retrieveCustomer(invoice.customer)
    accounting_props = customer.getAccountingProps(customer.getCustomerDetails(cus), invoice=invoice)
    amount_with_tax = decimal.Decimal(invoice.total) / 100

    finalized_date = datetime.fromtimestamp(invoice.status_transitions.finalized_at, timezone.utc).astimezone(config.accounting_tz)

    invoice_discount = decimal.Decimal(((invoice.get("discount", None) or {}).get("coupon", None) or {}).get("percent_off", 0))

    for line_item_idx, line_item in enumerate(invoice.lines):
      text = "Invoice {} / {}".format(invoice.number, line_item.get("description", ""))
      start, end = getLineItemRecognitionRange(line_item, invoice)

      discounted_li_amount = decimal.Decimal(line_item["amount"]) / 100 * (1 - invoice_discount / 100)

      revenue_items.append({
        "id": invoice.id,
        "number": invoice.number,
        "line_item_idx": line_item_idx,
        "recognition_start": start,
        "recognition_end": end,
        "created": finalized_date,
        "amount_net": discounted_li_amount,
        "accounting_props": accounting_props,
        "text": text,
        "customer": cus,
        "amount_with_tax": amount_with_tax,
      })

  return revenue_items

def createAccountingRecords(recognition_start, recognition_end, created, amount_net, accounting_props, text, amount_with_tax=None, customer=None, id=None, number=None, line_item_idx=None):
  records = []

  months = recognition.split_months(recognition_start, recognition_end, [amount_net])

  base_months = list(filter(lambda month: month["start"] <= created, months))
  base_amount = sum(map(lambda month: month["amounts"][0], base_months))

  records.append({
    "date": created,
    "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(amount_with_tax or amount_net),
    "Soll/Haben-Kennzeichen": "S",
    "WKZ Umsatz": "EUR",
    "Konto": accounting_props["customer_account"],
    "Gegenkonto (ohne BU-Schlüssel)": accounting_props["revenue_account"],
    "BU-Schlüssel": accounting_props["datev_tax_key"],
    "Buchungstext": text,
  })

  forward_amount = amount_net - base_amount

  forward_months = list(filter(lambda month: month["start"] > created, months))

  if len(forward_months) > 0 and forward_amount > 0:
    records.append({
      "date": created,
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(forward_amount),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": accounting_props["revenue_account"],
      "Gegenkonto (ohne BU-Schlüssel)": "990",
      "Buchungstext": "{} / pRAP nach {}".format(text, "{}..{}".format(forward_months[0]["start"].strftime("%Y-%m"), forward_months[-1]["start"].strftime("%Y-%m")) if len(forward_months) > 1 else forward_months[0]["start"].strftime("%Y-%m")),
    })

    for month in forward_months:
      records.append({
        "date": month["start"],
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(month["amounts"][0]),
        "Soll/Haben-Kennzeichen": "S",
        "WKZ Umsatz": "EUR",
        "Konto": "990",
        "Gegenkonto (ohne BU-Schlüssel)": accounting_props["revenue_account"],
        "Buchungstext": "{} / pRAP aus {}".format(text, created.strftime("%Y-%m")),
      })

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
    cus = customer.retrieveCustomer(invoice.customer)
    props = customer.getAccountingProps(customer.getCustomerDetails(cus), invoice=invoice)

    total = decimal.Decimal(invoice.total) / 100
    tax = decimal.Decimal(invoice.tax) / 100 if invoice.tax else None
    total_before_tax = total
    if tax is not None:
      total_before_tax -= tax

    lines.append([
      invoice.id,
      invoice.number,
      datetime.fromtimestamp(invoice.status_transitions.finalized_at, timezone.utc).astimezone(config.accounting_tz).strftime("%Y-%m-%d"),

      format(total_before_tax, ".2f"),
      format(tax, ".2f") if tax else None,
      format(decimal.Decimal(invoice.tax_percent), ".0f") if "tax_percent" in invoice and invoice.tax_percent else None,
      format(total, ".2f"),

      cus.id,
      customer.getCustomerName(cus),
      props["country"],
      props["vat_region"],
      props["vat_id"],
      props["tax_exempt"],

      props["customer_account"],
      props["revenue_account"],
      props["datev_tax_key"],
    ])

  return csv.lines_to_csv(lines)

def to_recognized_month_csv2(revenue_items):
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

  for revenue_item in revenue_items:
    for month in recognition.split_months(revenue_item["recognition_start"], revenue_item["recognition_end"], [revenue_item["amount_net"]]):
      month_start = month["start"]
      if month_start.year <= revenue_item["created"].year:
        accounting_date = revenue_item["created"]
      else:
        accounting_date = datetime(month_start.year, 1, 1, tzinfo=config.accounting_tz)

      lines.append([
        revenue_item["id"],
        revenue_item.get("number", ""),
        revenue_item["created"].strftime("%Y-%m-%d"),
        revenue_item["recognition_start"].strftime("%Y-%m-%d"),
        revenue_item["recognition_end"].strftime("%Y-%m-%d"),
        month["start"].strftime("%Y-%m") + "-01",

        str(revenue_item.get("line_item_idx", 0) + 1),
        revenue_item["text"],
        format(month["amounts"][0], ".2f"),

        revenue_item["customer"]["id"],
        customer.getCustomerName(revenue_item["customer"]),
        revenue_item["customer"].get("address", {}).get("country", ""),

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
