import stripe

def getCustomerDetails(customer):
  record = {
    "id": customer.id
  }
  if "deleted" in customer and customer.deleted:
    record["name"] = customer.id
  else:
    if customer.description is not None:
      record["name"] = customer.description
    else:
      record["name"] = customer.name
    if customer.address is not None:
      record["country"] = customer.address.country
    elif customer.shipping is not None:
      record["country"] = customer.shipping.address.country
    if customer.tax_info and customer.tax_info.type == "vat":
      record["vat_id"] = customer.tax_info.tax_id
    if customer.tax_exempt:
      record["tax_exempt"] = customer.tax_exempt
  return record

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
  country = customer.get("country", None)

  invoice_tax = None
  if invoice is not None:
    invoice_tax = invoice.get("tax", None)
  elif checkout_session is not None:
    invoice_tax = checkout_session.get("total_details", {}).get("amount_tax", None)

  # use tax status at time of invoice creation
  if invoice is not None and "customer_tax_exempt" in invoice:
    tax_exempt = invoice["customer_tax_exempt"]
  else:
    tax_exempt = customer.get("tax_exempt", None)

  vat_id = customer.get("vat_id", None)

  props = {
    "country": country,
    "vat_id": vat_id,
    "tax_exempt": tax_exempt,
    "invoice_tax": invoice_tax,
    "customer_account": "10001",
    "vat_region": "World",
    "datev_tax_key": "",
  }

  if country == "DE":
    if invoice is not None and invoice_tax is None:
      print("Warning: no tax in DE invoice", invoice["id"])
    if tax_exempt != "none":
      print("Warning: DE customer tax status is", tax_exempt, customer["id"])
    props["revenue_account"] = "8400"
    # props["datev_tax_key"] = "9"
    props["vat_region"] = "DE"
    return props

  if country in country_codes_eu:
    props["vat_region"] = "EU"

  if tax_exempt == "reverse" or tax_exempt == "exempt" or invoice_tax is None or invoice_tax == 0:
    if tax_exempt == "exempt":
      print("Warning: tax exempt customer, treating like 'reverse'", customer["id"])
      props["tax_exempt"] = "reverse"
    if tax_exempt == "none":
      print("Warning: taxable customer without tax on invoice, treating like 'reverse'", customer["id"])
      props["tax_exempt"] = "reverse"
    if not (invoice_tax is None or invoice_tax == 0):
      print("Warning: tax on invoice of reverse charge customer", invoice.get("id", "n/a") if invoice is not None else "n/a")
    if country in country_codes_eu and vat_id is None:
      print("Warning: EU reverse charge customer without VAT ID")

    if country in country_codes_eu and vat_id is None:
      props["revenue_account"] = "8336"
    else:
      props["revenue_account"] = "8338"

    # props["datev_tax_key"] = "94"
    return props

  elif tax_exempt == "none":
    # print("Warning: configure taxation for", country, "customer", customer["id"])
    # Unter Bagtellgrenze MOSS
    pass

  else:
    print("Warning: unknown tax status for customer", customer["id"])

  props["revenue_account"] = "8400"
  return props

def getRevenueAccount(customer, invoice=None, checkout_session=None):
  return getAccountingProps(customer, invoice=invoice, checkout_session=checkout_session)["revenue_account"]

def getCustomerAccount(customer, checkout_session=None):
  return getAccountingProps(customer, checkout_session=checkout_session)["customer_account"]

def getDatevTaxKey(customer, invoice=None, checkout_session=None):
  return getAccountingProps(customer, invoice=invoice, checkout_session=checkout_session)["datev_tax_key"]

def all_customers():
  starting_after = None
  while True:
    response = stripe.Customer.list(
      starting_after=starting_after,
      limit=10
    )
    # print("Fetched {} customers".format(len(response.data)))
    if len(response.data) == 0:
      break
    starting_after = response.data[-1].id
    for item in response.data:
      yield item

def validate_customers():
  for customer in all_customers():
    # print(customer)
    if not customer.address:
      print("Warning: customer without address", customer.id)
      continue

    country = customer.address.country
    tax_exempt = customer.tax_exempt
    vat_id = customer.tax_info.tax_id if customer.tax_info is not None else None

    if country == "DE":
      if tax_exempt != "none":
        print("Warning: DE customer tax status is", tax_exempt, customer.id)

    elif tax_exempt == "reverse":
      if country in ["ES", "IT", "GB"] and vat_id is None:
        print("Warning: EU reverse charge customer without VAT ID", customer.id)

    # elif tax_exempt == "none":
    #   print("Warning: configure taxation for", country, "customer", customer.id)

    elif tax_exempt == "exempt":
      print("Warning: exempt customer", customer.id)
