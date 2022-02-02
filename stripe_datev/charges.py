import stripe
import decimal
from datetime import datetime, timezone
from . import customer, dateparser, output, config

def listChargesRaw(fromTime, toTime):
  starting_after = None
  charges = []
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
      if not charge.paid or not charge.captured:
        continue

      charges.append(charge)

    if not response.has_more:
      break

  return charges

def chargeHasInvoice(charge):
  return charge.invoice is not None

checkoutSessionsByPaymentIntent = {}

def getCheckoutSessionViaPaymentIntentCached(id):
  if id in checkoutSessionsByPaymentIntent:
    return checkoutSessionsByPaymentIntent[id]
  sessions = stripe.checkout.Session.list(payment_intent=id, expand=["data.line_items"]).data
  if len(sessions) > 0:
    session = sessions[0]
  else:
    session = None
  checkoutSessionsByPaymentIntent[id] = session
  return session

def getChargeDescription(charge):
  if not charge.description and charge.payment_intent:
    try:
      session = getCheckoutSessionViaPaymentIntentCached(charge.payment_intent)
      return ", ".join(map(lambda li: li.description, session.line_items.data))
    except:
      pass
  return charge.description

def getChargeRecognitionRange(charge):
  desc = getChargeDescription(charge)
  created = datetime.fromtimestamp(charge.created, timezone.utc)
  date_range = dateparser.find_date_range(desc, created, tz=config.accounting_tz)
  if date_range is not None:
    return date_range
  else:
    print("Warning: unknown period for charge --", charge.id, desc)
    return created, created

def createRevenueItems(charges):
  revenue_items = []
  for charge in charges:
    if charge.refunded:
      if charge.refunds.data[0].amount == charge.amount:
        print("Skipping fully refunded charge", charge.id)
        continue
      else:
        raise NotImplementedError("Handling of partially refunded charges is not implemented yet")

    cus = customer.retrieveCustomer(charge.customer)
    session = getCheckoutSessionViaPaymentIntentCached(charge.payment_intent)

    accounting_props = customer.getAccountingProps(customer.getCustomerDetails(cus), checkout_session=session)
    if charge.receipt_number:
      text = "Receipt {}".format(charge.receipt_number)
    else:
      text = "Charge {}".format(charge.id)

    description = getChargeDescription(charge)
    if description:
      text += " / {}".format(description)

    created = datetime.fromtimestamp(charge.created, timezone.utc)
    start, end = getChargeRecognitionRange(charge)

    charge_amount = decimal.Decimal(charge.amount) / 100
    tax_amount = decimal.Decimal(session.total_details.amount_tax) / 100 if session else None
    net_amount = charge_amount - tax_amount if tax_amount is not None else charge_amount

    revenue_items.append({
      "id": charge.id,
      "number": charge.receipt_number,
      "created": created,
      "amount_net": net_amount,
      "accounting_props": accounting_props,
      "customer": cus,
      "amount_with_tax": charge_amount,
      "line_items": [{
        "recognition_start": start,
        "recognition_end": end,
        "amount_net": net_amount,
        "text": text,
        "amount_with_tax": charge_amount
      }]
    })

  return revenue_items

def createAccountingRecords(charges):
  records = []

  for charge in charges:
    acc_props = customer.getAccountingProps(None)
    created = datetime.fromtimestamp(charge.created, timezone.utc)

    balance_transaction = stripe.BalanceTransaction.retrieve(charge.balance_transaction)
    assert len(balance_transaction.fee_details) == 1
    assert balance_transaction.fee_details[0].currency == "eur"
    fee_amount = decimal.Decimal(balance_transaction.fee_details[0].amount) / 100
    fee_desc = balance_transaction.fee_details[0].description

    records.append({
      "date": created,
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(decimal.Decimal(charge.amount) / 100),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": "1201",
      "Gegenkonto (ohne BU-Schlüssel)": acc_props["customer_account"],
      "Buchungstext": "Stripe Payment ({})".format(charge.id),
    })

    records.append({
      "date": created,
      "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(fee_amount),
      "Soll/Haben-Kennzeichen": "S",
      "WKZ Umsatz": "EUR",
      "Konto": "70025",
      "Gegenkonto (ohne BU-Schlüssel)": "1201",
      "Buchungstext": "{} ({})".format(fee_desc or "Stripe Fee", charge.id),
    })

    if charge.refunded or len(charge.refunds.data) > 0:
      assert len(charge.refunds.data) == 1
      refund = charge.refunds.data[0]

      refund_created = datetime.fromtimestamp(refund.created, timezone.utc)
      records.append({
        "date": refund_created,
        "Umsatz (ohne Soll/Haben-Kz)": output.formatDecimal(decimal.Decimal(refund.amount) / 100),
        "Soll/Haben-Kennzeichen": "S",
        "WKZ Umsatz": "EUR",
        "Konto": acc_props["customer_account"],
        "Gegenkonto (ohne BU-Schlüssel)": "1201",
        "Buchungstext": "Stripe Payment Refund ({})".format(charge.id),
      })

  return records

