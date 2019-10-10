import sys
import argparse
from datetime import datetime, timedelta
import datedelta
import pytz
import stripe
from stripe_datev import charges, invoices, payouts, output

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
        fromTime = berlin.localize(datetime(year, month, 1, 0, 0, 0, 0))
        toTime = fromTime + datedelta.MONTH
        print("Retrieving data between {} and {}".format(fromTime.strftime("%Y-%m-%d"), (toTime - timedelta(0, 1)).strftime("%Y-%m-%d")))

        records = []

        invoiceObjects = invoices.listInvoices(fromTime, toTime)
        # print(invoiceObjects)

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

        thisMonth = fromTime.astimezone(output.berlin).strftime("%Y-%m")
        with open("out/EXTF_{}.csv".format(thisMonth), 'w', encoding="latin1", errors="replace", newline="\r\n") as fp:
          output.printRecords(fp, records, fromTime, toTime - timedelta(0, 1))

        nextMonthEnd = toTime + timedelta(3)
        nextMonth = nextMonthEnd.astimezone(output.berlin).strftime("%Y-%m")
        with open("out/EXTF_{}_Aus_Vormonat.csv".format(nextMonth), 'w', encoding="latin1", errors="replace", newline="\r\n") as fp:
          output.printRecords(fp, records, toTime, nextMonthEnd)

if __name__ == '__main__':
    StripeDatevCli(sys.argv).run()
