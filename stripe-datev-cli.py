import sys
import argparse
from datetime import datetime, timedelta
import datedelta
import pytz
import stripe
from stripe_datev import charges, invoices, payouts, output
import os, os.path
import requests

stripe.api_key = "sk_live_"
stripe.api_version = "2019-08-14"

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

        pdfDir = os.path.join('out', 'pdf')
        if not os.path.exists(pdfDir):
          os.mkdir(pdfDir)

        for inv in invoiceObjects:
          pdfLink = inv["invoice_pdf"]
          invDate = inv["date"]
          invNo = inv["invoice_number"]

          fileName = "{} {}.pdf".format(invDate.replace(tzinfo=berlin).strftime("%Y-%m-%d"), invNo)
          filePath = os.path.join(pdfDir, fileName)
          if os.path.exists(filePath):
            print("{} exists, skipping".format(filePath))
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

        records += charges.createAccountingRecords(chargeObjects)
        # print(records)

        payoutObjects = payouts.listPayouts(fromTime, toTime)
        # print(payoutObjects)

        records += payouts.createAccountingRecords(payoutObjects)
        # print(records)

        datevDir = os.path.join('out', 'datev')
        if not os.path.exists(datevDir):
          os.mkdir(datevDir)

        thisMonth = fromTime.astimezone(output.berlin).strftime("%Y-%m")
        with open(os.path.join(datevDir, "EXTF_{}.csv".format(thisMonth)), 'w', encoding="latin1", errors="replace", newline="\r\n") as fp:
          output.printRecords(fp, records, fromTime, toTime - timedelta(0, 1))

        nextMonthEnd = toTime + timedelta(3)
        nextMonth = nextMonthEnd.astimezone(output.berlin).strftime("%Y-%m")
        with open(os.path.join(datevDir, "EXTF_{}_Aus_Vormonat.csv".format(nextMonth)), 'w', encoding="latin1", errors="replace", newline="\r\n") as fp:
          output.printRecords(fp, records, toTime, nextMonthEnd)

if __name__ == '__main__':
    StripeDatevCli(sys.argv).run()
