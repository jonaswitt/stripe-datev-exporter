import pytz
import tomli

with open('config.toml', 'rb') as f:
  config = tomli.load(f)

company = config["company"]
accounting_tz = pytz.timezone(company["timezone"])

datev = config["datev"]
accounts = config["accounts"]
