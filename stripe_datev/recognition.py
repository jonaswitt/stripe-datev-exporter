import unittest
import calendar
import datetime
import decimal


def split_months(start, end, amounts):
  amounts = [decimal.Decimal(amount) for amount in amounts]

  if start == end:
    return [{
      "start": start,
      "end": end,
      "amounts": amounts,
    }]
  total_duration = end - start
  current_month = start

  remaining_amounts = list(amounts)
  months = []
  while current_month <= end:
    start_of_month = current_month.replace(day=1, hour=0, minute=0, second=0)
    end_of_month = current_month.replace(day=calendar.monthrange(
      current_month.year, current_month.month)[1], hour=23, minute=59, second=59, tzinfo=None)
    if start_of_month.tzinfo:
      end_of_month = start_of_month.tzinfo.localize(end_of_month)

    month_duration = min(end, end_of_month) - max(start,
                                                  start_of_month) + datetime.timedelta(seconds=1)
    perc_of_total = decimal.Decimal.from_float(month_duration / total_duration)

    month_amounts = [
      (amount * perc_of_total).quantize(decimal.Decimal(10) ** -2) for amount in amounts]

    remaining_amounts = [remaining_amount - month_amounts[idx]
                         for idx, remaining_amount in enumerate(remaining_amounts)]

    months.append({
      "start": start_of_month,
      "end": end_of_month,
      "amounts": month_amounts,
    })

    current_month = end_of_month + datetime.timedelta(seconds=1)

  months[-1]["amounts"] = [month_amount + remaining_amounts[idx]
                           for idx, month_amount in enumerate(months[-1]["amounts"])]

  if not any(amount != 0 for amount in months[-1]["amounts"]):
    months = months[:-1]

  for idx, amount in enumerate(amounts):
    assert amount == sum(month["amounts"][idx] for month in months)

  return months


class RecognitionTestSuite(unittest.TestCase):
  def test_split(self):
    self.assertEqual(
      split_months(datetime.datetime(2021, 5, 1), datetime.datetime(
        2022, 4, 30), [decimal.Decimal(100)]),
      [
        {'start': datetime.datetime(2021, 5, 1, 0, 0), 'end': datetime.datetime(
          2021, 5, 31, 23, 59, 59), 'amounts': [decimal.Decimal('8.52')]},
        {'start': datetime.datetime(2021, 6, 1, 0, 0), 'end': datetime.datetime(
          2021, 6, 30, 23, 59, 59), 'amounts': [decimal.Decimal('8.24')]},
        {'start': datetime.datetime(2021, 7, 1, 0, 0), 'end': datetime.datetime(
          2021, 7, 31, 23, 59, 59), 'amounts': [decimal.Decimal('8.52')]},
        {'start': datetime.datetime(2021, 8, 1, 0, 0), 'end': datetime.datetime(
          2021, 8, 31, 23, 59, 59), 'amounts': [decimal.Decimal('8.52')]},
        {'start': datetime.datetime(2021, 9, 1, 0, 0), 'end': datetime.datetime(
          2021, 9, 30, 23, 59, 59), 'amounts': [decimal.Decimal('8.24')]},
        {'start': datetime.datetime(2021, 10, 1, 0, 0), 'end': datetime.datetime(
          2021, 10, 31, 23, 59, 59), 'amounts': [decimal.Decimal('8.52')]},
        {'start': datetime.datetime(2021, 11, 1, 0, 0), 'end': datetime.datetime(
          2021, 11, 30, 23, 59, 59), 'amounts': [decimal.Decimal('8.24')]},
        {'start': datetime.datetime(2021, 12, 1, 0, 0), 'end': datetime.datetime(
          2021, 12, 31, 23, 59, 59), 'amounts': [decimal.Decimal('8.52')]},
        {'start': datetime.datetime(2022, 1, 1, 0, 0), 'end': datetime.datetime(
          2022, 1, 31, 23, 59, 59), 'amounts': [decimal.Decimal('8.52')]},
        {'start': datetime.datetime(2022, 2, 1, 0, 0), 'end': datetime.datetime(
          2022, 2, 28, 23, 59, 59), 'amounts': [decimal.Decimal('7.69')]},
        {'start': datetime.datetime(2022, 3, 1, 0, 0), 'end': datetime.datetime(
          2022, 3, 31, 23, 59, 59), 'amounts': [decimal.Decimal('8.52')]},
        {'start': datetime.datetime(2022, 4, 1, 0, 0), 'end': datetime.datetime(
          2022, 4, 30, 23, 59, 59), 'amounts': [decimal.Decimal('7.95')]}
      ]
    )


if __name__ == '__main__':
  unittest.main()
