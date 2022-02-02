import decimal
import sys
import argparse
from datetime import datetime, timedelta, timezone
import datedelta
import stripe
import stripe_datev.invoices, \
  stripe_datev.charges, \
  stripe_datev.customer, \
  stripe_datev.payouts, \
  stripe_datev.recognition, \
  stripe_datev.output, \
  stripe_datev.config
import os, os.path
import requests
import dotenv

dotenv.load_dotenv()

if "STRIPE_API_KEY" not in os.environ:
  print("Require STRIPE_API_KEY environment variable to be set")
  sys.exit(1)

stripe.api_key = os.environ["STRIPE_API_KEY"]
stripe.api_version = "2020-08-27"

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

        if month > 0:
          fromTime = stripe_datev.config.accounting_tz.localize(datetime(year, month, 1, 0, 0, 0, 0))
          toTime = fromTime + datedelta.MONTH
        else:
          fromTime = stripe_datev.config.accounting_tz.localize(datetime(year, 1, 1, 0, 0, 0, 0))
          toTime = fromTime + datedelta.YEAR
        print("Retrieving data between {} and {}".format(fromTime.strftime("%Y-%m-%d"), (toTime - timedelta(0, 1)).strftime("%Y-%m-%d")))
        thisMonth = fromTime.astimezone(stripe_datev.config.accounting_tz).strftime("%Y-%m")

        invoices = stripe_datev.invoices.listFinalizedInvoices(fromTime, toTime)
        print("Retrieved {} invoice(s), total {} EUR".format(len(invoices), sum([decimal.Decimal(i.total) / 100 for i in invoices])))

        revenue_items = stripe_datev.invoices.createRevenueItems(invoices)

        charges = stripe_datev.charges.listChargesRaw(fromTime, toTime)
        print("Retrieved {} charge(s), total {} EUR".format(len(charges), sum([decimal.Decimal(c.amount) / 100 for c in charges])))

        direct_charges = list(filter(lambda charge: not stripe_datev.charges.chargeHasInvoice(charge), charges))
        revenue_items += stripe_datev.charges.createRevenueItems(direct_charges)

        overview_dir = os.path.join(out_dir, "overview")
        if not os.path.exists(overview_dir):
          os.mkdir(overview_dir)

        with open(os.path.join(overview_dir, "overview-{:04d}-{:02d}.csv".format(year, month)), "w", encoding="utf-8") as fp:
          fp.write(stripe_datev.invoices.to_csv(invoices))

        monthly_recognition_dir = os.path.join(out_dir, "monthly_recognition")
        if not os.path.exists(monthly_recognition_dir):
          os.mkdir(monthly_recognition_dir)

        with open(os.path.join(monthly_recognition_dir, "monthly_recognition-{}.csv".format(thisMonth)), "w", encoding="utf-8") as fp:
          fp.write(stripe_datev.invoices.to_recognized_month_csv2(revenue_items))

        datevDir = os.path.join(out_dir, 'datev')
        if not os.path.exists(datevDir):
          os.mkdir(datevDir)

        # Datev Revenue

        records = []
        for revenue_item in revenue_items:
          records += stripe_datev.invoices.createAccountingRecords(revenue_item)

        records_by_month = {}
        for record in records:
          month = record["date"].strftime("%Y-%m")
          records_by_month[month] = records_by_month.get(month, []) + [record]

        for month, records in records_by_month.items():
          if month == thisMonth:
            name = "EXTF_{}_Revenue.csv".format(thisMonth)
          else:
            name = "EXTF_{}_Revenue_From_{}.csv".format(month, thisMonth)
          stripe_datev.output.writeRecords(os.path.join(datevDir, name), records)

        # Datev charges

        charge_records = stripe_datev.charges.createAccountingRecords(charges)
        stripe_datev.output.writeRecords(os.path.join(datevDir, "EXTF_{}_Charges.csv".format(thisMonth)), charge_records)

        # Datev payouts

        payoutObjects = stripe_datev.payouts.listPayouts(fromTime, toTime)
        payout_records = stripe_datev.payouts.createAccountingRecords(payoutObjects)
        stripe_datev.output.writeRecords(os.path.join(datevDir, "EXTF_{}_Payouts.csv".format(thisMonth)), payout_records)

        # PDF

        pdfDir = os.path.join(out_dir, 'pdf')
        if not os.path.exists(pdfDir):
          os.mkdir(pdfDir)

        for invoice in invoices:
          pdfLink = invoice.invoice_pdf
          finalized_date = datetime.fromtimestamp(invoice.status_transitions.finalized_at, timezone.utc).astimezone(stripe_datev.config.accounting_tz)
          invNo = invoice.number

          fileName = "{} {}.pdf".format(finalized_date.strftime("%Y-%m-%d"), invNo)
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

        for charge in charges:
          fileName = "{} {}.html".format(datetime.fromtimestamp(charge.created, timezone.utc).strftime("%Y-%m-%d"), charge.receipt_number or charge.id)
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


    def run_records(self):
      records = []

      # Invoice before first revenue period
      records += stripe_datev.invoices.accrualRecords(datetime(2019, 12, 18), 100, 10001, 8338, "Invoice 2", datetime(2020, 1, 1), 12, False)

      # Invoice in first revenue period
      records += stripe_datev.invoices.accrualRecords(datetime(2020, 1, 13), 100, 10002, 8400, "Invoice 2", datetime(2020, 1, 1), 12, False)

      # fromTime = datetime(2020, 1, 1)
      fromTime = min([r["date"] for r in records])
      toTime = max([r["date"] for r in records])

      datevDir = os.path.join(out_dir, 'datev')
      if not os.path.exists(datevDir):
        os.mkdir(datevDir)
      with open(os.path.join(datevDir, "EXTF_accrual.csv"), 'w', encoding="latin1", errors="replace", newline="\r\n") as fp:
          stripe_datev.output.printRecords(fp, records, fromTime, toTime)

    def run_validate_customers(self):
      stripe_datev.customer.validate_customers()

if __name__ == '__main__':
    StripeDatevCli(sys.argv).run()
