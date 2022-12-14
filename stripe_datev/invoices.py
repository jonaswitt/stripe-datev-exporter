import json
from stripe_datev import recognition, csv
import stripe
import decimal, math
from datetime import datetime, timedelta, timezone
from . import customer, output, dateparser, config
import datedelta

invoices_cached = {}

def listFinalizedInvoices(fromTime, toTime):
  invoices = stripe.Invoice.list(
    created={
      "lt": int(toTime.timestamp())
    },
    due_date={
      "gte": int(fromTime.timestamp()),
    },
    expand=["data.customer", "data.customer.tax_ids"]
  ).auto_paging_iter()

  for invoice in invoices:
    if invoice.status == "draft":
      continue
    finalized_date = datetime.fromtimestamp(invoice.status_transitions.finalized_at, timezone.utc).astimezone(config.accounting_tz)
    if finalized_date < fromTime or finalized_date >= toTime:
      # print("Skipping invoice {}, created {} finalized {} due {}".format(invoice.id, created_date, finalized_date, due_date))
      continue
    invoices_cached[invoice.id] = invoice
    yield invoice

def retrieveInvoice(id):
  if isinstance(id, str):
    if id in invoices_cached:
      return invoices_cached[id]
    invoice = stripe.Invoice.retrieve(id, expand=["customer", "customer.tax_ids"])
    invoices_cached[invoice.id] = invoice
    return invoice
  elif isinstance(id, stripe.Invoice):
    invoices_cached[id.id] = id
    return id
  else:
    raise Exception("Unexpected retrieveInvoice() argument: {}".format(id))

tax_rates_cached = {}

def retrieveTaxRate(id):
  if id in tax_rates_cached:
    return tax_rates_cached[id]
  tax_rate = stripe.TaxRate.retrieve(id)
  tax_rates_cached[id] = tax_rate
  return tax_rate

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
    voided_at = None
    marked_uncollectible_at = None
    if invoice.status == "void":
      voided_at = datetime.fromtimestamp(invoice.status_transitions.voided_at, timezone.utc).astimezone(config.accounting_tz)
    elif invoice.status == "uncollectible":
      marked_uncollectible_at = datetime.fromtimestamp(invoice.status_transitions.marked_uncollectible_at, timezone.utc).astimezone(config.accounting_tz)

    credited_at = None
    credited_amount = None
    invoice_discount_factor = 1
    if invoice.post_payment_credit_notes_amount > 0:
      cns = stripe.CreditNote.list(invoice=invoice.id).data
      assert len(cns) == 1
      credited_at = datetime.fromtimestamp(cns[0].created, timezone.utc).astimezone(config.accounting_tz)
      credited_amount = decimal.Decimal(invoice.post_payment_credit_notes_amount) / 100
      invoice_discount_factor = 1 - (decimal.Decimal(invoice.post_payment_credit_notes_amount) / decimal.Decimal(invoice.total))

    line_items = []

    cus = customer.retrieveCustomer(invoice.customer)
    accounting_props = customer.getAccountingProps(cus, invoice=invoice)
    amount_with_tax = decimal.Decimal(invoice.total) / 100
    amount_net = amount_with_tax
    if invoice.tax:
      amount_net -= decimal.Decimal(invoice.tax) / 100

    tax_percentage = None
    if len(invoice.total_tax_amounts) > 0:
      rate = retrieveTaxRate(invoice.total_tax_amounts[0]["tax_rate"])
      tax_percentage = decimal.Decimal(rate["percentage"])
    # Do not apply invoice_discount_factor to net/with tax invoice
    # amounts (for DATEV, we create refund bookings), only
    # to line items (below) for revenue recognition

    finalized_date = datetime.fromtimestamp(invoice.status_transitions.finalized_at, timezone.utc).astimezone(config.accounting_tz)

    invoice_discount = decimal.Decimal(0)
    coupon = (invoice.get("discount", None) or {}).get("coupon", None)
    if coupon is not None:
      if coupon.get("percent_off", None):
        invoice_discount = decimal.Decimal(coupon["percent_off"])
      elif coupon.get("amount_off", None):
        invoice_discount = decimal.Decimal(coupon["amount_off"]) / 100 / amount_net * 100
    line_item_discount_factor = (1 - invoice_discount / 100)

    is_subscription = invoice.get("subscription", None) is not None

    if invoice.lines.has_more:
      lines = invoice.lines.list().auto_paging_iter()
    else:
      lines = invoice.lines

    for line_item_idx, line_item in enumerate(lines):
      text = "Invoice {} / {}".format(invoice.number, line_item.get("description", ""))
      start, end = getLineItemRecognitionRange(line_item, invoice)

      li_amount = decimal.Decimal(line_item["amount"]) / 100
      discounted_li_net = li_amount * line_item_discount_factor
      discounted_li_total = discounted_li_net
      if len(line_item["tax_amounts"]) > 0:
        assert len(line_item["tax_amounts"]) == 1
        li_tax = decimal.Decimal(line_item["tax_amounts"][0]["amount"]) / 100
        if not line_item["tax_amounts"][0]["inclusive"]:
          discounted_li_total += li_tax
        else:
          discounted_li_net -= li_tax

      line_items.append({
        "line_item_idx": line_item_idx,
        "recognition_start": start,
        "recognition_end": end,
        "amount_net": discounted_li_net * invoice_discount_factor,
        "text": text,
        "amount_with_tax": discounted_li_total * invoice_discount_factor
      })

    revenue_items.append({
      "id": invoice.id,
      "number": invoice.number,
      "created": finalized_date,
      "amount_net": amount_net,
      "accounting_props": accounting_props,
      "customer": cus,
      "amount_with_tax": amount_with_tax,
      "tax_percentage": tax_percentage,
      "text": "Invoice {}".format(invoice.number),
      "voided_at": voided_at,
      "credited_at": credited_at,
      "credited_amount": credited_amount,
      "marked_uncollectible_at": marked_uncollectible_at,
      "line_items": line_items if voided_at is None and marked_uncollectible_at is None else [],
      "is_subscription": is_subscription,
    })

  return revenue_items

def createAccountingRecords(revenue_item):
  created = revenue_item["created"]
  amount_with_tax = revenue_item["amount_with_tax"]
  accounting_props = revenue_item["accounting_props"]
  line_items = revenue_item["line_items"]
  text = revenue_item["text"]
  voided_at = revenue_item.get("voided_at", None)
  credited_at = revenue_item.get("credited_at", None)
  credited_amount = revenue_item.get("credited_amount", None)
  marked_uncollectible_at = revenue_item.get("marked_uncollectible_at", None)
  number = revenue_item["number"]
  eu_vat_id = accounting_props["vat_id"] or ""

  records = []

  records.append({
    "date": created,
    "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(amount_with_tax),
    "Soll/Haben-Kennzeichen": "S",
    "WKZ Umsatz": "EUR",
    "Konto": accounting_props["customer_account"],
    "Gegenkonto (ohne BU-Schlüssel)": accounting_props["revenue_account"],
    "BU-Schlüssel": accounting_props["datev_tax_key"],
    "Buchungstext": text,
    "Belegfeld 1": number,
    "EU-Land u. UStID": eu_vat_id,
  })

  if voided_at is not None:
    print("Voided", text, "Created", created, 'Voided', voided_at)
    records.append({
      "date": voided_at,
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(amount_with_tax),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": accounting_props["revenue_account"],
      "Gegenkonto (ohne BU-Schlüssel)": accounting_props["customer_account"],
      "BU-Schlüssel": accounting_props["datev_tax_key"],
      "Buchungstext": "Storno {}".format(text),
      "Belegfeld 1": number,
      "EU-Land u. UStID": eu_vat_id,
    })

  elif marked_uncollectible_at is not None:
    print("Uncollectible", text, "Created", created, 'Marked uncollectible', marked_uncollectible_at)
    records.append({
      "date": marked_uncollectible_at,
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(amount_with_tax),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": accounting_props["revenue_account"],
      "Gegenkonto (ohne BU-Schlüssel)": accounting_props["customer_account"],
      "BU-Schlüssel": accounting_props["datev_tax_key"],
      "Buchungstext": "Storno {}".format(text),
      "Belegfeld 1": number,
      "EU-Land u. UStID": eu_vat_id,
    })

  elif credited_at is not None:
    print("Refunded", text, "Created", created, 'Refunded', credited_at)
    records.append({
      "date": credited_at,
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(credited_amount),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": accounting_props["revenue_account"],
      "Gegenkonto (ohne BU-Schlüssel)": accounting_props["customer_account"],
      "BU-Schlüssel": accounting_props["datev_tax_key"],
      "Buchungstext": "Erstattung {}".format(text),
      "Belegfeld 1": number,
      "EU-Land u. UStID": eu_vat_id,
    })

  for line_item in line_items:
    amount_with_tax = line_item["amount_with_tax"]
    recognition_start = line_item["recognition_start"]
    recognition_end = line_item["recognition_end"]
    text = line_item["text"]

    months = recognition.split_months(recognition_start, recognition_end, [amount_with_tax])

    base_months = list(filter(lambda month: month["start"] <= created, months))
    base_amount = sum(map(lambda month: month["amounts"][0], base_months))

    forward_amount = amount_with_tax - base_amount

    forward_months = list(filter(lambda month: month["start"] > created, months))

    if len(forward_months) > 0 and forward_amount > 0:
      records.append({
        "date": created,
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(forward_amount),
        "Soll/Haben-Kennzeichen": "S",
        "WKZ Umsatz": "EUR",
        "Konto": accounting_props["revenue_account"],
        "Gegenkonto (ohne BU-Schlüssel)": "990",
        "Buchungstext": "pRAP nach {} / {}".format("{}..{}".format(forward_months[0]["start"].strftime("%Y-%m"), forward_months[-1]["start"].strftime("%Y-%m")) if len(forward_months) > 1 else forward_months[0]["start"].strftime("%Y-%m"), text),
        "EU-Land u. UStID": eu_vat_id,
      })

      for month in forward_months:
        records.append({
          "date": month["start"],
          "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(month["amounts"][0]),
          "Soll/Haben-Kennzeichen": "S",
          "WKZ Umsatz": "EUR",
          "Konto": "990",
          "Gegenkonto (ohne BU-Schlüssel)": accounting_props["revenue_account"],
          "Buchungstext": "pRAP aus {} / {}".format(created.strftime("%Y-%m"), text),
          "EU-Land u. UStID": eu_vat_id,
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
    if invoice.status == "void":
      continue
    cus = customer.retrieveCustomer(invoice.customer)
    props = customer.getAccountingProps(cus, invoice=invoice)

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
    "revenue_type",
    "is_recurring",
  ]]

  for revenue_item in revenue_items:
    voided_at = revenue_item.get("voided_at", None)
    if voided_at is not None:
      continue

    last_line_item_recognition_end = max((line_item["recognition_end"] for line_item in revenue_item["line_items"]), default=None)
    if last_line_item_recognition_end is not None and revenue_item["created"] + timedelta(days=1) < last_line_item_recognition_end:
      revenue_type = "Prepaid"
    else:
      revenue_type = "PayPerUse"
    is_recurring = revenue_item.get("is_subscription", False)

    for line_item in revenue_item["line_items"]:
      for month in recognition.split_months(line_item["recognition_start"], line_item["recognition_end"], [line_item["amount_net"]]):
        accounting_date = max(revenue_item["created"], month["start"])

        lines.append([
          revenue_item["id"],
          revenue_item.get("number", ""),
          revenue_item["created"].strftime("%Y-%m-%d"),
          line_item["recognition_start"].strftime("%Y-%m-%d"),
          line_item["recognition_end"].strftime("%Y-%m-%d"),
          month["start"].strftime("%Y-%m") + "-01",

          str(line_item.get("line_item_idx", 0) + 1),
          line_item["text"],
          format(month["amounts"][0], ".2f"),

          revenue_item["customer"]["id"],
          customer.getCustomerName(revenue_item["customer"]),
          revenue_item["customer"].get("address", {}).get("country", ""),

          accounting_date.strftime("%Y-%m-%d"),
          revenue_type,
          "true" if is_recurring else "false",
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
