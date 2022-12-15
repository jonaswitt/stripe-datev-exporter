import decimal
from functools import reduce
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

    def run(self, argv):
        parser = argparse.ArgumentParser(
          description='Stripe utility',
        )
        parser.add_argument('command', type=str, help='Subcommand to run', choices=[
          'download',
          'validate_customers',
          'fill_account_numbers',
          'list_accounts',
          'opos'
        ])

        args = parser.parse_args(argv[1:2])
        getattr(self, args.command)(argv[2:])

    def download(self, argv):
        parser = argparse.ArgumentParser(prog="stripe-datev-cli.py download")
        parser.add_argument('year', type=int, help='year to download data for')
        parser.add_argument('month', type=int, help='month to download data for')

        args = parser.parse_args(argv)

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

        invoices = list(reversed(list(stripe_datev.invoices.listFinalizedInvoices(fromTime, toTime))))
        print("Retrieved {} invoice(s), total {} EUR".format(len(invoices), sum([decimal.Decimal(i.total) / 100 for i in invoices])))

        revenue_items = stripe_datev.invoices.createRevenueItems(invoices)

        charges = list(stripe_datev.charges.listChargesRaw(fromTime, toTime))
        print("Retrieved {} charge(s), total {} EUR".format(len(charges), sum([decimal.Decimal(c.amount) / 100 for c in charges])))

        direct_charges = list(filter(lambda charge: not stripe_datev.charges.chargeHasInvoice(charge), charges))
        revenue_items += stripe_datev.charges.createRevenueItems(direct_charges)

        overview_dir = os.path.join(out_dir, "overview")
        if not os.path.exists(overview_dir):
          os.mkdir(overview_dir)

        with open(os.path.join(overview_dir, "overview-{:04d}-{:02d}.csv".format(year, month)), "w", encoding="utf-8") as fp:
          fp.write(stripe_datev.invoices.to_csv(invoices))
          print("Wrote {} invoices      to {}".format(str(len(invoices)).rjust(4, " "), os.path.relpath(fp.name, os.getcwd())))

        monthly_recognition_dir = os.path.join(out_dir, "monthly_recognition")
        if not os.path.exists(monthly_recognition_dir):
          os.mkdir(monthly_recognition_dir)

        with open(os.path.join(monthly_recognition_dir, "monthly_recognition-{}.csv".format(thisMonth)), "w", encoding="utf-8") as fp:
          fp.write(stripe_datev.invoices.to_recognized_month_csv2(revenue_items))
          print("Wrote {} revenue items to {}".format(str(len(revenue_items)).rjust(4, " "), os.path.relpath(fp.name, os.getcwd())))

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

        charges_by_month = {}
        for record in charge_records:
          month = record["date"].strftime("%Y-%m")
          charges_by_month[month] = charges_by_month.get(month, []) + [record]

        for month, records in charges_by_month.items():
          if month == thisMonth:
            name = "EXTF_{}_Charges.csv".format(thisMonth)
          else:
            name = "EXTF_{}_Charges_From_{}.csv".format(month, thisMonth)
          stripe_datev.output.writeRecords(os.path.join(datevDir, name), records)

        # Datev payouts

        payoutObjects = list(stripe_datev.payouts.listPayouts(fromTime, toTime))
        print("Retrieved {} payout(s), total {} EUR".format(len(payoutObjects), sum([r["amount"] for r in payoutObjects])))

        payout_records = stripe_datev.payouts.createAccountingRecords(payoutObjects)
        stripe_datev.output.writeRecords(os.path.join(datevDir, "EXTF_{}_Payouts.csv".format(thisMonth)), payout_records)

        balance_transactions = list(stripe.BalanceTransaction.list(
          created={
            "lt": int(toTime.timestamp()),
            "gte": int(fromTime.timestamp()),
          },
          type="contribution",
        ).auto_paging_iter())
        print("Retrieved {} contribution(s), total {} EUR".format(len(balance_transactions), sum([-decimal.Decimal(b["amount"]) / 100 for b in balance_transactions])))

        contribution_records = stripe_datev.payouts.createAccountingRecordsContributions(balance_transactions)
        stripe_datev.output.writeRecords(os.path.join(datevDir, "EXTF_{}_Contributions.csv".format(thisMonth)), contribution_records)

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

    def validate_customers(self, argv):
      stripe_datev.customer.validate_customers()

    def fill_account_numbers(self, argv):
      stripe_datev.customer.fill_account_numbers()

    def list_accounts(self, argv):
      stripe_datev.customer.list_account_numbers(argv[0] if len(argv) > 0 else None)

    def opos(self, argv):
      eoy = stripe_datev.config.accounting_tz.localize(datetime.now())
      # eoy = stripe_datev.config.accounting_tz.localize(datetime(2022, 4, 24, 0, 0, 0, 0) - timedelta(seconds=1))
      print("Unpaid invoices as of", eoy)

      invoices = stripe.Invoice.list(
        created={
          "lte": int(eoy.timestamp()),
          "gte": int((eoy - datedelta.YEAR).timestamp()),
        },
        status="open", # comment out if 'eoy' is not now()
        expand=["data.customer"]
      ).auto_paging_iter()

      totals = []
      for invoice in invoices:
        if invoice.status_transitions.get("marked_uncollectible_at", None) or invoice.status_transitions.get("voided_at", None):
          continue
        due_date = stripe_datev.config.accounting_tz.localize(datetime.utcfromtimestamp(invoice.due_date if invoice.due_date else invoice.created))
        paid_at = invoice.status_transitions.get("paid_at", None)
        customer = stripe_datev.customer.retrieveCustomer(invoice.customer)
        if not paid_at or stripe_datev.config.accounting_tz.localize(datetime.utcfromtimestamp(paid_at)) > eoy:
          total = decimal.Decimal(invoice.total) / 100
          totals.append(total)
          print(invoice.number.ljust(13, " "), format(total, ",.2f").rjust(10, " "), "EUR", customer.email.ljust(35, " "), "due", due_date.date(), "({} overdue)".format(eoy - due_date) if due_date < eoy else "")

      total = reduce(lambda x, y: x + y, totals, decimal.Decimal(0))
      print("TOTAL        ", format(total, ",.2f").rjust(10, " "), "EUR")

if __name__ == '__main__':
    StripeDatevCli().run(sys.argv)
