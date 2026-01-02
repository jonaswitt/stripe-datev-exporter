import json
from stripe_datev import recognition, csv
import stripe
import decimal
import math
from datetime import datetime, timedelta, timezone
from . import customer, output, dateparser, config
import datedelta

invoices_cached = {}


def listFinalizedInvoices(fromTime, toTime):
  invoices = stripe.Invoice.list(
    created={
      "lt": int(toTime.timestamp()),
      # Increase this padding if you have invoices where more than
      # 1 month passed between creation and finalization
      "gte": int((fromTime - datedelta.MONTH).timestamp()),
    },
    expand=["data.customer", "data.customer.tax_ids"]
  ).auto_paging_iter()

  for invoice in invoices:
    if invoice.status == "draft":
      continue
    finalized_date = datetime.fromtimestamp(
      invoice.status_transitions.finalized_at, timezone.utc).astimezone(config.accounting_tz)
    if finalized_date < fromTime or finalized_date >= toTime:
      # print("Skipping invoice {}, created {} finalized {} due {}".format(invoice.id, created_date, finalized_date, due_date))
      continue
    invoices_cached[invoice.id] = invoice
    yield invoice


def retrieveInvoice(id):
  if isinstance(id, str):
    if id in invoices_cached:
      return invoices_cached[id]
    invoice = stripe.Invoice.retrieve(
      id, expand=["customer", "customer.tax_ids"])
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
      date_range = dateparser.find_date_range(line_item.get(
        "description"), created, tz=config.accounting_tz)
      if date_range is not None:
        start, end = date_range

    except Exception as ex:
      print(ex)
      pass

  if start is None and end is None:
    print("Warning: unknown period for line item --",
          invoice.id, line_item.get("description"))
    start = created
    end = created

  # else:
  #   print("Period", start, end, "--", line_item.get("description"))

  return start.astimezone(config.accounting_tz), end.astimezone(config.accounting_tz)


def createRevenueItems(invs):
  revenue_items = []
  for invoice in invs:
    if invoice["metadata"].get("stripe-datev-exporter:ignore", "false") == "true":
      print("Skipping invoice {} (ignore)".format(invoice.id))
      continue

    voided_at = None
    marked_uncollectible_at = None
    if invoice.status == "void":
      voided_at = datetime.fromtimestamp(
        invoice.status_transitions.voided_at, timezone.utc).astimezone(config.accounting_tz)
    elif invoice.status == "uncollectible":
      marked_uncollectible_at = datetime.fromtimestamp(
        invoice.status_transitions.marked_uncollectible_at, timezone.utc).astimezone(config.accounting_tz)

    credited_at = None
    credited_amount = None
    if invoice.post_payment_credit_notes_amount > 0:
      cns = stripe.CreditNote.list(invoice=invoice.id).data
      assert len(cns) == 1
      credited_at = datetime.fromtimestamp(
        cns[0].created, timezone.utc).astimezone(config.accounting_tz)
      credited_amount = decimal.Decimal(
        invoice.post_payment_credit_notes_amount) / 100

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

    finalized_date = datetime.fromtimestamp(
      invoice.status_transitions.finalized_at, timezone.utc).astimezone(config.accounting_tz)

    is_subscription = invoice.get("subscription", None) is not None

    if invoice.lines.has_more:
      lines = invoice.lines.list().auto_paging_iter()
    else:
      lines = invoice.lines

    for line_item_idx, line_item in enumerate(lines):
      text = "Invoice {} / {}".format(invoice.number,
                                      line_item.get("description", ""))
      start, end = getLineItemRecognitionRange(line_item, invoice)

      li_amount_net = decimal.Decimal(line_item["amount"]) / 100
      for discount in line_item["discount_amounts"]:
        li_amount_net -= decimal.Decimal(discount["amount"]) / 100

      li_amount_with_tax = li_amount_net
      for tax_amount in line_item["tax_amounts"]:
        if tax_amount["inclusive"]:
          li_amount_net -= decimal.Decimal(tax_amount["amount"]) / 100
        else:
          li_amount_with_tax += decimal.Decimal(tax_amount["amount"]) / 100

      line_items.append({
        "line_item_idx": line_item_idx,
        "recognition_start": start,
        "recognition_end": end,
        "amount_net": li_amount_net,
        "text": text,
        "amount_with_tax": li_amount_with_tax
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
      "line_items": line_items,
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

  if amount_with_tax > 0:
    records.append({
      "date": created,
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(amount_with_tax),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": accounting_props["customer_account"],
      "Gegenkonto (ohne BU-Schlüssel)": accounting_props["revenue_account"],
      "BU-Schlüssel": accounting_props["datev_tax_key_invoice"],
      "Buchungstext": text,
      "Belegfeld 1": number,
      "EU-Land u. UStID": eu_vat_id,
    })

    if voided_at is not None:
      print("Voided", text, "Created", created, 'Voided', voided_at)
      records.append({
        "date": voided_at,
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(amount_with_tax),
        "Soll/Haben-Kennzeichen": "H",
        "WKZ Umsatz": "EUR",
        "Konto": accounting_props["customer_account"],
        "Gegenkonto (ohne BU-Schlüssel)": accounting_props["revenue_account"],
        "BU-Schlüssel": accounting_props["datev_tax_key_invoice"],
        "Buchungstext": "Storno {}".format(text),
        "Belegfeld 1": number,
        "EU-Land u. UStID": eu_vat_id,
      })

    elif marked_uncollectible_at is not None:
      print("Uncollectible", text, "Created", created,
            'Marked uncollectible', marked_uncollectible_at)
      records.append({
        "date": marked_uncollectible_at,
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(amount_with_tax),
        "Soll/Haben-Kennzeichen": "H",
        "WKZ Umsatz": "EUR",
        "Konto": accounting_props["customer_account"],
        "Gegenkonto (ohne BU-Schlüssel)": accounting_props["revenue_account"],
        "BU-Schlüssel": accounting_props["datev_tax_key_invoice"],
        "Buchungstext": "Storno {}".format(text),
        "Belegfeld 1": number,
        "EU-Land u. UStID": eu_vat_id,
      })

    elif credited_at is not None:
      print("Refunded", text, "Created", created, 'Refunded', credited_at)
      records.append({
        "date": credited_at,
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(credited_amount),
        "Soll/Haben-Kennzeichen": "H",
        "WKZ Umsatz": "EUR",
        "Konto": accounting_props["customer_account"],
        "Gegenkonto (ohne BU-Schlüssel)": accounting_props["revenue_account"],
        "BU-Schlüssel": accounting_props["datev_tax_key_invoice"],
        "Buchungstext": "Erstattung {}".format(text),
        "Belegfeld 1": number,
        "EU-Land u. UStID": eu_vat_id,
      })

  prap_records = []
  def apply_prap(date, start, end, amount):
    # print("apply_prap", date, start, end, amount)

    months = recognition.split_months(start, end, [amount])

    base_months = list(filter(lambda month: month["start"] <= date, months))
    base_amount = sum(map(lambda month: month["amounts"][0], base_months))

    forward_amount = amount - base_amount
    forward_months = list(
      filter(lambda month: month["start"] > date, months))

    if len(forward_months) == 0:
      return

    prap_records.append({
      "date": date,
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(abs(forward_amount)),
      "Soll/Haben-Kennzeichen": "S" if forward_amount >= 0 else "H",
      "WKZ Umsatz": "EUR",
      "Konto": accounting_props["revenue_account"],
      "Gegenkonto (ohne BU-Schlüssel)": str(config.accounts["prap"]),
      "Buchungstext": "pRAP nach {} / {}".format("{}..{}".format(forward_months[0]["start"].strftime("%Y-%m"), forward_months[-1]["start"].strftime("%Y-%m")) if len(forward_months) > 1 else forward_months[0]["start"].strftime("%Y-%m"), text),
      "Belegfeld 1": number,
      "EU-Land u. UStID": eu_vat_id,
    })

    for month in forward_months:
      prap_records.append({
        # If invoice was voided/etc., resolve all pRAP in that month, don't keep going into the future
        "date": month["start"],
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(abs(month["amounts"][0])),
        "Soll/Haben-Kennzeichen": "S" if month["amounts"][0] >= 0 else "H",
        "WKZ Umsatz": "EUR",
        "Konto": str(config.accounts["prap"]),
        "Gegenkonto (ohne BU-Schlüssel)": accounting_props["revenue_account"],
        "Buchungstext": "pRAP aus {} / {}".format(date.strftime("%Y-%m"), text),
        "Belegfeld 1": number,
        "EU-Land u. UStID": eu_vat_id,
      })

    assert sum(map(lambda month: month["amounts"][0], forward_months)) == forward_amount

  for line_item in line_items:
    amount_with_tax = line_item["amount_with_tax"]
    recognition_start = line_item["recognition_start"]
    recognition_end = line_item["recognition_end"]
    text = line_item["text"]

    apply_prap(created, recognition_start, recognition_end, amount_with_tax)

    if voided_at:
      apply_prap(voided_at, recognition_start, recognition_end, -amount_with_tax)
    elif marked_uncollectible_at:
      apply_prap(marked_uncollectible_at, recognition_start, recognition_end, -amount_with_tax)
    elif credited_at:
      if len(line_items) == 1:
        credited_amount_li = credited_amount
      else:
        credited_amount_li = credited_amount * (amount_with_tax / revenue_item["amount_with_tax"]) # TODO: rounding issues?
      apply_prap(credited_at, recognition_start, recognition_end, -credited_amount_li)

  prap_records_by_month = {}
  for record in prap_records:
    month = record["date"].strftime("%Y-%m")
    if month not in prap_records_by_month:
      prap_records_by_month[month] = []
    prap_records_by_month[month].append(record)

  # If all pRAP records are in the same month, don't emit them
  if len(prap_records_by_month) > 1:
    for month in prap_records_by_month.keys():
      # If all records in a month cancel each other out, don't emit them
      month_total = sum(map(lambda r: decimal.Decimal(r["Umsatz (ohne Soll/Haben-Kz)"].replace(",", ".")) * (1 if r["Soll/Haben-Kennzeichen"] == "S" else -1) * (-1 if r["Konto"] == str(config.accounts["prap"]) else 1), prap_records_by_month[month]))
      if month_total != 0:
        records += prap_records_by_month[month]

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
      datetime.fromtimestamp(invoice.status_transitions.finalized_at, timezone.utc).astimezone(
        config.accounting_tz).strftime("%Y-%m-%d"),

      format(total_before_tax, ".2f"),
      format(tax, ".2f") if tax else None,
      format(decimal.Decimal(invoice.tax_percent),
             ".0f") if "tax_percent" in invoice and invoice.tax_percent else None,
      format(total, ".2f"),

      cus.id,
      customer.getCustomerName(cus),
      props["country"],
      props["vat_region"],
      props["vat_id"],
      props["tax_exempt"],

      props["customer_account"],
      props["revenue_account"],
      props["datev_tax_key_invoice"],
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
    amount_with_tax = revenue_item.get("amount_with_tax")
    voided_at = revenue_item.get("voided_at", None)
    credited_at = revenue_item.get("credited_at", None)
    credited_amount = revenue_item.get("credited_amount", None)
    marked_uncollectible_at = revenue_item.get("marked_uncollectible_at", None)

    last_line_item_recognition_end = max(
      (line_item["recognition_end"] for line_item in revenue_item["line_items"]), default=None)
    if last_line_item_recognition_end is not None and revenue_item["created"] + timedelta(days=1) < last_line_item_recognition_end:
      revenue_type = "Prepaid"
    else:
      revenue_type = "PayPerUse"
    is_recurring = revenue_item.get("is_subscription", False)

    for line_item in revenue_item["line_items"]:
      end = voided_at or marked_uncollectible_at or credited_at or line_item["recognition_end"]
      for month in recognition.split_months(line_item["recognition_start"], line_item["recognition_end"], [line_item["amount_net"]]):
        accounting_date = max(
          revenue_item["created"], end if end < month["start"] else month["start"])

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

        if voided_at is not None:
          reverse = lines[-1].copy()
          reverse[8] = format(month["amounts"][0] * -1, ".2f")
          reverse[12] = max(revenue_item["created"], end if end <
                            month["end"] else month["start"]).strftime("%Y-%m-%d")
          lines.append(reverse)

        elif marked_uncollectible_at is not None:
          reverse = lines[-1].copy()
          reverse[8] = format(month["amounts"][0] * -1, ".2f")
          reverse[12] = max(revenue_item["created"], end if end <
                            month["end"] else month["start"]).strftime("%Y-%m-%d")
          lines.append(reverse)

        elif credited_at is not None:
          reverse = lines[-1].copy()
          reverse[8] = format(month["amounts"][0] * -1 *
                              (credited_amount / amount_with_tax), ".2f")
          reverse[12] = max(revenue_item["created"], end if end <
                            month["end"] else month["start"]).strftime("%Y-%m-%d")
          lines.append(reverse)

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
    accrueText = "{} / Rueckstellung ({} Monate)".format(text,
                                                         revenueSpreadMonths)
    periodsBooked = 0
    periodDate = firstRevenueDate
  else:
    accrueAmount = invoiceAmount - revenuePerPeriod
    accrueText = "{} / Rueckstellung Anteilig ({}/{} Monate)".format(
      text, revenueSpreadMonths - 1, revenueSpreadMonths)
    periodsBooked = 1
    periodDate = firstRevenueDate + datedelta.MONTH

  records.append({
    "date": invoiceDate,
    "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(accrueAmount),
    "Soll/Haben-Kennzeichen": "S",
    "WKZ Umsatz": "EUR",
    "Konto": str(revenueAccount),
    "Gegenkonto (ohne BU-Schlüssel)": str(config.accounts["prap"]),
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
      "Konto": str(config.accounts["prap"]),
      "Gegenkonto (ohne BU-Schlüssel)": str(revenueAccount),
      "Buchungstext": "{} / Aufloesung Rueckstellung Monat {}/{}".format(text, periodsBooked + 1, revenueSpreadMonths),
    })

    periodDate = periodDate + datedelta.MONTH
    periodsBooked += 1
    remainingAmount -= periodAmount

  return records


def listCreditNotes(fromTime, toTime):
  creditNotes = stripe.CreditNote.list(
    expand=["data.invoice"]).auto_paging_iter()

  for creditNote in creditNotes:
    created = datetime.fromtimestamp(
      creditNote.created, timezone.utc).astimezone(config.accounting_tz)
    if created >= toTime:
      continue
    if created < fromTime:
      break

    yield creditNote
