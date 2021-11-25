import sys
import argparse
from datetime import datetime, timedelta
import datedelta
import pytz
import stripe
from stripe_datev import charges, invoices, payouts, output, customer
import os, os.path
import requests

stripe.api_key = "sk_live_"
stripe.api_version = "2019-08-14"

out_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'out')
if stripe.api_key.startswith("sk_test"):
  out_dir = os.path.join(out_dir, "test")
if not os.path.exists(out_dir):
  os.mkdir(out_dir)

class StripeDatevCli(object):

    def __init__(self, argv):
        self.argv = argv

    def run(self):
        parser = argparse.ArgumentParser(
          description='Stripe DATEV Exporter',
        )
        parser.add_argument('year', type=int, help='year to download data for')
        parser.add_argument('month', type=int, help='month to download data for')

        args = parser.parse_args(self.argv[1:])

        year = int(args.year)
        month = int(args.month)

        berlin = pytz.timezone('Europe/Berlin')
        if month > 0:
          fromTime = berlin.localize(datetime(year, month, 1, 0, 0, 0, 0))
          toTime = fromTime + datedelta.MONTH
        else:
          fromTime = berlin.localize(datetime(year, 1, 1, 0, 0, 0, 0))
          toTime = fromTime + datedelta.YEAR
        print("Retrieving data between {} and {}".format(fromTime.strftime("%Y-%m-%d"), (toTime - timedelta(0, 1)).strftime("%Y-%m-%d")))

        records = []

        invoiceObjects = invoices.listInvoices(fromTime, toTime)
        # print(invoiceObjects)

        overview_dir = os.path.join(out_dir, "overview")
        if not os.path.exists(overview_dir):
          os.mkdir(overview_dir)

        with open(os.path.join(overview_dir, "overview-{:04d}-{:02d}.csv".format(year, month)), "w", encoding="utf-8") as fp:
          fp.write(invoices.to_csv(invoiceObjects))

        monthly_recognition_dir = os.path.join(out_dir, "monthly_recognition")
        if not os.path.exists(monthly_recognition_dir):
          os.mkdir(monthly_recognition_dir)

        with open(os.path.join(monthly_recognition_dir, "monthly_recognition-{:04d}-{:02d}.csv".format(year, month)), "w", encoding="utf-8") as fp:
          fp.write(invoices.to_recognized_month_csv(invoiceObjects))

        pdfDir = os.path.join(out_dir, 'pdf')
        if not os.path.exists(pdfDir):
          os.mkdir(pdfDir)

        for inv in invoiceObjects:
          pdfLink = inv["invoice_pdf"]
          invDate = inv["date"]
          invNo = inv["invoice_number"]

          fileName = "{} {}.pdf".format(invDate.replace(tzinfo=berlin).strftime("%Y-%m-%d"), invNo)
          filePath = os.path.join(pdfDir, fileName)
          if os.path.exists(filePath):
            # print("{} exists, skipping".format(filePath))
            continue

          print("Downloading {} to {}".format(pdfLink, filePath))
          r = requests.get(pdfLink)
          if r.status_code != 200:
            print("HTTP status {}".format(r.status_code))
            continue
          with open(filePath, "wb") as fp:
            fp.write(r.content)

        records += invoices.createAccountingRecords(invoiceObjects, fromTime, toTime)
        # print(records)

        chargeObjects = charges.listCharges(fromTime, toTime)
        # print(chargeObjects)

        for charge in chargeObjects:
          fileName = "{} {}.html".format(charge["created"].replace(tzinfo=berlin).strftime("%Y-%m-%d"), charge.get("receipt_number", charge["id"]))
          filePath = os.path.join(pdfDir, fileName)
          if os.path.exists(filePath):
            # print("{} exists, skipping".format(filePath))
            continue

          pdfLink = charge["receipt_url"]
          print("Downloading {} to {}".format(pdfLink, filePath))
          r = requests.get(pdfLink)
          if r.status_code != 200:
            print("HTTP status {}".format(r.status_code))
            continue
          with open(filePath, "wb") as fp:
            fp.write(r.content)

        records += charges.createAccountingRecords(chargeObjects)
        # print(records)

        payoutObjects = payouts.listPayouts(fromTime, toTime)
        # print(payoutObjects)

        records += payouts.createAccountingRecords(payoutObjects)
        # print(records)

        datevDir = os.path.join(out_dir, 'datev')
        if not os.path.exists(datevDir):
          os.mkdir(datevDir)

        thisMonth = fromTime.astimezone(output.berlin).strftime("%Y-%m")
        with open(os.path.join(datevDir, "EXTF_{}.csv".format(thisMonth)), 'w', encoding="latin1", errors="replace", newline="\r\n") as fp:
          output.printRecords(fp, records, fromTime, toTime - timedelta(0, 1))

        nextMonthEnd = toTime + timedelta(3)
        nextMonth = nextMonthEnd.astimezone(output.berlin).strftime("%Y-%m")
        with open(os.path.join(datevDir, "EXTF_{}_Aus_Vormonat.csv".format(nextMonth)), 'w', encoding="latin1", errors="replace", newline="\r\n") as fp:
          output.printRecords(fp, records, toTime, nextMonthEnd)

    def run_records(self):
      records = []

      # Invoice before first revenue period
      records += invoices.accrualRecords(datetime(2019, 12, 18), 100, 10001, 8338, "Invoice 2", datetime(2020, 1, 1), 12, False)

      # Invoice in first revenue period
      records += invoices.accrualRecords(datetime(2020, 1, 13), 100, 10002, 8400, "Invoice 2", datetime(2020, 1, 1), 12, False)

      # fromTime = datetime(2020, 1, 1)
      fromTime = min([r["date"] for r in records])
      toTime = max([r["date"] for r in records])

      datevDir = os.path.join(out_dir, 'datev')
      if not os.path.exists(datevDir):
        os.mkdir(datevDir)
      with open(os.path.join(datevDir, "EXTF_accrual.csv"), 'w', encoding="latin1", errors="replace", newline="\r\n") as fp:
          output.printRecords(fp, records, fromTime, toTime)

    def run_validate_customers(self):
      customer.validate_customers()

if __name__ == '__main__':
    StripeDatevCli(sys.argv).run()
