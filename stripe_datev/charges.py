import stripe
import decimal
from datetime import datetime, timezone
from . import customer, dateparser, output, config, invoices


def chargeHasInvoice(charge):
  return charge.invoice is not None


checkoutSessionsByPaymentIntent = {}


def getCheckoutSessionViaPaymentIntentCached(id):
  if id in checkoutSessionsByPaymentIntent:
    return checkoutSessionsByPaymentIntent[id]
  sessions = stripe.checkout.Session.list(
    payment_intent=id, expand=["data.line_items"]).data
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
  date_range = dateparser.find_date_range(
    desc, created, tz=config.accounting_tz)
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
        raise NotImplementedError(
          "Handling of partially refunded charges is not implemented yet")
    if charge.description is not None and "in_" in charge.description:
      print("Skipping charge referencing invoice",
            charge.id, charge.description)
      continue

    cus = customer.retrieveCustomer(charge.customer)
    session = getCheckoutSessionViaPaymentIntentCached(charge.payment_intent)

    accounting_props = customer.getAccountingProps(
      cus, checkout_session=session)
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
    tax_amount = decimal.Decimal(
      session.total_details.amount_tax) / 100 if session else None
    net_amount = charge_amount - tax_amount if tax_amount is not None else charge_amount

    tax_percentage = None if tax_amount is None else decimal.Decimal(
      tax_amount) / decimal.Decimal(net_amount) * 100

    revenue_items.append({
      "id": charge.id,
      "number": charge.receipt_number,
      "created": created,
      "amount_net": net_amount,
      "accounting_props": accounting_props,
      "customer": cus,
      "amount_with_tax": charge_amount,
      "tax_percentage": tax_percentage,
      "text": text,
      "line_items": [{
        "recognition_start": start,
        "recognition_end": end,
        "amount_net": net_amount,
        "text": text,
        "amount_with_tax": charge_amount
      }]
    })

  return revenue_items
