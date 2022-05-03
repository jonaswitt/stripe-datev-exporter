from datetime import datetime, timezone
import sys
import stripe

from stripe_datev import config, output

customers_cached = {}

def retrieveCustomer(id):
  if isinstance(id, str):
    if id in customers_cached:
      return customers_cached[id]
    cus = stripe.Customer.retrieve(id)
    customers_cached[cus.id] = cus
    return cus
  elif isinstance(id, stripe.Customer):
    customers_cached[id.id] = id
    return id
  else:
    raise "Unexpected retrieveCustomer() argument: {}".format(id)

def getCustomerName(customer):
  if customer.get("deleted", False):
    return customer.id
  if customer.description is not None:
    return customer.description
  else:
    return customer.name

tax_ids_cached = {}

def getCustomerTaxId(customer):
  if customer.id in tax_ids_cached:
    return tax_ids_cached[customer.id]
  if "tax_ids" in customer:
    tax_id = next((tax_id for tax_id in customer.tax_ids.data if tax_id.type == "eu_vat" and tax_id.verification.status == "verified"), None)
    tax_id = tax_id.value if tax_id is not None else None
  else:
    ids = stripe.Customer.list_tax_ids(customer.id, limit=10).data
    tax_id = ids[0].value if len(ids) > 0 else None
  tax_ids_cached[customer.id] = tax_id
  return tax_id

country_codes_eu = [
  "AT",
  "BE",
  "BG",
  "CY",
  "CZ",
  "DK",
  "EE",
  "FI",
  "FR",
  "DE",
  "GR",
  "HU",
  "IE",
  "IT",
  "LV",
  "LT",
  "LU",
  "MT",
  "NL",
  "PL",
  "PT",
  "RO",
  "SK",
  "SI",
  "ES",
  "SE",
]

def getAccountingProps(customer, invoice=None, checkout_session=None):
  props = {
    "vat_region": "World",
  }

  if (invoice is None or datetime.fromtimestamp(invoice.created, timezone.utc) >= datetime(2022, 1, 1, 0, 0).astimezone(config.accounting_tz)):
    if not customer.metadata.get("accountNumber", None):
      raise Exception("Expected 'accountNumber' in metadata")
    props["customer_account"] = customer.metadata["accountNumber"]
  else:
    props["customer_account"] = "10001"

  address = customer.address or customer.shipping.address
  country = address.country

  invoice_tax = None
  if invoice is not None:
    invoice_tax = invoice.get("tax", None)
  elif checkout_session is not None:
    invoice_tax = checkout_session.get("total_details", {}).get("amount_tax", None)

  # use tax status at time of invoice creation
  if invoice is not None and "customer_tax_exempt" in invoice:
    tax_exempt = invoice["customer_tax_exempt"]
  else:
    tax_exempt = customer.tax_exempt

  vat_id = getCustomerTaxId(customer)

  props = dict(props, **{
    "country": country,
    "vat_id": vat_id,
    "tax_exempt": tax_exempt,
    "invoice_tax": invoice_tax,
    "datev_tax_key": "",
  })

  if country == "DE":
    if invoice is not None and invoice_tax is None:
      print("Warning: no tax in DE invoice", invoice["id"])
    if tax_exempt != "none":
      print("Warning: DE customer tax status is", tax_exempt, customer.id)
    props["revenue_account"] = "8400"
    # props["datev_tax_key"] = "9"
    props["vat_region"] = "DE"
    return props

  if country in country_codes_eu:
    props["vat_region"] = "EU"

  if tax_exempt == "reverse" or tax_exempt == "exempt" or invoice_tax is None or invoice_tax == 0:
    if invoice is not None:
      if tax_exempt == "exempt":
        print("Warning: tax exempt customer, treating like 'reverse'", customer.id)
        props["tax_exempt"] = "reverse"
      if tax_exempt == "none":
        print("Warning: taxable customer without tax on invoice, treating like 'reverse'", customer.id, invoice.get("id", "n/a") if invoice is not None else "n/a")
        props["tax_exempt"] = "reverse"
      if not (invoice_tax is None or invoice_tax == 0):
        print("Warning: tax on invoice of reverse charge customer", invoice.get("id", "n/a") if invoice is not None else "n/a")
      if country in country_codes_eu and vat_id is None:
        print("Warning: EU reverse charge customer without VAT ID", customer.id)

    if country in country_codes_eu and vat_id is not None:
      props["revenue_account"] = "8336"
    else:
      props["revenue_account"] = "8338"

    # props["datev_tax_key"] = "94"
    return props

  elif tax_exempt == "none":
    # print("Warning: configure taxation for", country, "customer", customer.id)
    # Unter Bagtellgrenze MOSS
    pass

  else:
    print("Warning: unknown tax status for customer", customer.id)

  props["revenue_account"] = "8400"
  return props

def getRevenueAccount(customer, invoice=None, checkout_session=None):
  return getAccountingProps(customer, invoice=invoice, checkout_session=checkout_session)["revenue_account"]

def getCustomerAccount(customer, invoice=None, checkout_session=None):
  return getAccountingProps(customer, invoice=invoice, checkout_session=checkout_session)["customer_account"]

def getDatevTaxKey(customer, invoice=None, checkout_session=None):
  return getAccountingProps(customer, invoice=invoice, checkout_session=checkout_session)["datev_tax_key"]

def validate_customers():
  customer_count = 0
  for customer in stripe.Customer.list(expand=["data.tax_ids"]).auto_paging_iter():
    if not customer.address:
      print("Warning: customer without address", customer.id)

    if customer.tax_exempt == "exempt":
      print("Warning: exempt customer", customer.id)

    getAccountingProps(customer)

    customer_count += 1

  print("Validated {} customers".format(customer_count))

def fill_account_numbers():
  highest_account_number = None
  fill_customers = []
  for customer in stripe.Customer.list().auto_paging_iter():
    if "accountNumber" in customer.metadata:
      highest_account_number = int(customer.metadata["accountNumber"])
      break
    fill_customers.append(customer)

  if highest_account_number is None:
    highest_account_number = 10100 - 1

  print("{} customers without account number, highest number is {}".format(len(fill_customers), highest_account_number))

  for customer in reversed(fill_customers):
    # print(customer.id, customer.metadata)

    highest_account_number += 1
    metadata_new = {
      "accountNumber": str(highest_account_number)
    }

    for old_key in ["subscribedNetPrice", "subscribedProduct", "subscribedProductName", "subscribedTaxRate", "subscribedTotal"]:
      if old_key in customer.metadata:
        metadata_new[old_key] = ""

    # print("Update", metadata_new)
    stripe.Customer.modify(customer.id, metadata=metadata_new)

    print(customer.id, highest_account_number)

def list_account_numbers():
  customer_it = stripe.Customer.list(expand=["data.tax_ids"]).auto_paging_iter()
  output.printAccounts(sys.stdout, customer_it)
