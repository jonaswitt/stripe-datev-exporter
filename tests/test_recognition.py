from stripe_datev import config, recognition
import unittest
import decimal
import datetime


class RecognitionTest(unittest.TestCase):

  def test_split_months_simple(self):
    months = recognition.split_months(config.accounting_tz.localize(datetime.datetime(
      2020, 4, 1)), config.accounting_tz.localize(datetime.datetime(2020, 6, 30, 23, 59, 59)), [decimal.Decimal('100')])

    self.assertEqual(len(months), 3)
    self.assertEqual(months[0]["amounts"], [decimal.Decimal('32.97')])

  def test_split_months_negative(self):
    months = recognition.split_months(datetime.datetime(2022, 11, 15, 17, 3, 22, tzinfo=config.accounting_tz), datetime.datetime(
      2023, 8, 16, 11, 52, 12, tzinfo=config.accounting_tz), [decimal.Decimal('-14.11')])

    self.assertEqual(len(months), 10)
    self.assertEqual(months[-1]["amounts"], [decimal.Decimal('-0.78')])
