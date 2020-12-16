import stripe

def getCustomerDetails(customer_id):
  customer = stripe.Customer.retrieve(customer_id)
  # print(customer)

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

def getRevenueAccount(customer, invoice):
  country = customer.get("country", None)
  invoice_tax = invoice.get("tax", None)
  tax_exempt = customer.get("tax_exempt", None)
  # TODO: use tax status at time of invoice creation
  # tax_exempt = invoice.get("customer_tax_exempt", None)
  vat_id = customer.get("vat_id", None)

  if country == "DE":
    if invoice_tax is None:
      print("Warning: no tax in DE invoice", invoice["id"])
    if tax_exempt != "none":
      print("Warning: DE customer tax status is", tax_exempt, customer["id"])
    return "8400"

  if tax_exempt == "reverse":
    if invoice_tax is not None:
      print("Warning: tax on invoice of reverse charge customer", invoice["id"])
    if country in ["ES", "IT", "GB"] and vat_id is None:
      print("Warning: EU reverse charge customer without VAT ID")
    return "8337"

  elif tax_exempt == "none":
    print("Warning: configure taxation for", country, "customer", customer["id"])

  elif tax_exempt == "exempt":
    print("Warning: exempt customer, treating like 'reverse'", customer["id"])
    return "8337"

  else:
    print("Warning: unknown tax status for customer", customer["id"])

  return "8000"

def getCustomerAccount(customer):
  return "10001"

def getDatevTaxKey(customer, invoice):
  country = customer.get("country", None)
  invoice_tax = invoice.get("tax", None)
  tax_exempt = customer.get("tax_exempt", None)
  # TODO: use tax status at time of invoice creation
  # tax_exempt = invoice.get("customer_tax_exempt", None)

  if country == "DE":
    return "9"

  if tax_exempt == "reverse":
    return "94"

  return "0"

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