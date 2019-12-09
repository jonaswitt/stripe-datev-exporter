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
  return record

def getRevenueAccount(customer):
  if customer["country"] == "DE":
    return "8400"
  if customer["country"] in ["US", "AU", "CA", "JE", "CH", "VI"]:
    return "8338"
  if customer["country"] in ["ES", "IT", "GB"]:
    if "vat_id" in customer:
      return "8336"
    else:
      return "8339"
  print("Warning: using generic revenue account for customer {}".format(customer))
  return "8000"

def getCustomerAccount(customer):
  return "10001"

def getBookingType(customer, tax_percent):
  if customer["country"] == "DE" and tax_percent == 19:
    return "9"
  return "0"
