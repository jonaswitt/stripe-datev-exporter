import unittest
import pytz
import calendar
import datetime
import re

MONTHS = [
  ["Jan", "January"],
  ["Feb", "February"],
  ["Mar", "March"],
  ["Apr", "April"],
  ["May"],
  ["Jun", "June"],
  ["Jul", "July"],
  ["Aug", "August"],
  ["Sep", "Sept", "September"],
  ["Oct", "October"],
  ["Nov", "November"],
  ["Dec", "December"],
]


def flatten(t):
  return [item for sublist in t for item in sublist]


YEAR_REGEX = re.compile(
  "(?<=[\D^])(2019|2020|2021|2022|2023|2024|2025|2026)(?=\D|$)", re.M)
MONTH_REGEX = re.compile(
  "(?<=[\W^])({})(?=\W|$)".format("|".join(flatten(MONTHS))), re.M)
DAY_REGEX = re.compile("(?<=[\D^])([0-9]{1,2})(?:st|nd|rd|th)(?=\W|$)", re.M)


def find_date_range(text, ref_date=None, tz=None):
  years = [int(y) for y in YEAR_REGEX.findall(text)]
  months = []
  for match in MONTH_REGEX.findall(text):
    for month_idx, patterns in enumerate(MONTHS):
      if next((pattern for pattern in patterns if re.match(pattern, match)), None):
        months.append(month_idx + 1)
  days = [int(d) for d in DAY_REGEX.findall(text)]
  # print(years, months, days)

  foundYear = True
  if len(years) >= 2:
    year1 = years[0]
    year2 = years[1]
  elif len(years) == 1:
    year1 = years[0]
    year2 = years[0]
  else:
    if not ref_date:
      return None
    foundYear = False
    year1 = ref_date.year
    year2 = ref_date.year

  if len(months) >= 2:
    month1 = months[0]
    month2 = months[1]
  elif len(months) == 1:
    month1 = months[0]
    month2 = months[0]
  else:
    if not foundYear:
      return None
    if len(days) > 0:
      return None
    month1 = 1
    month2 = 12

  if len(days) >= 2:
    day1 = days[0]
    day2 = days[1]
  elif len(days) == 1:
    day1 = days[0]
    day2 = days[0]
  else:
    day1 = 1
    day2 = calendar.monthrange(year2, month2)[1]

  start = datetime.datetime(year1, month1, day1, 0, 0, 0)
  end = datetime.datetime(year2, month2, day2, 23, 59, 59)
  if tz is not None:
    start = tz.localize(start)
    end = tz.localize(end)

  return (start, end)


class DateParserTestSuite(unittest.TestCase):

  ref_date = datetime.datetime(2021, 5, 10)
  tz = pytz.timezone('Europe/Berlin')

  def assertStringRange(self, strRange, start, end):
    r = find_date_range(strRange, ref_date=self.ref_date, tz=self.tz)
    if r is None:
      self.assertIsNone(start, "Could not parse range")
      self.assertIsNone(end, "Could not parse range")
    else:
      self.assertIsNotNone(start, "Did not expect start of range")
      self.assertIsNotNone(end, "Did not expect end of range")
      self.assertEqual(r[0], self.tz.localize(
        start), "Start of range does not match: '{}'".format(strRange))
      self.assertEqual(r[1], self.tz.localize(
        end), "End of range does not match: '{}'".format(strRange))

  def test_parsing(self):
    self.assertStringRange(
      "Njord Analytics and Player; (Cape31); Fri May 7th 2021",
      datetime.datetime(2021, 5, 7), datetime.datetime(2021, 5, 7, 23, 59, 59)
    )

    self.assertStringRange(
      "Njord Analytics & Njord Player, RC44, valid Jan-Nov 2021",
      datetime.datetime(2021, 1, 1), datetime.datetime(
        2021, 11, 30, 23, 59, 59)
    )

    self.assertStringRange(
      "Njord Player & Fleet Race reports, per day, May 20th-23rd",
      datetime.datetime(2021, 5, 20), datetime.datetime(
        2021, 5, 23, 23, 59, 59)
    )

    self.assertStringRange(
      "Njord Player, SailGP, valid Jun 1st 2021 – Apr 30th 2022",
      datetime.datetime(2021, 6, 1), datetime.datetime(2022, 4, 30, 23, 59, 59)
    )

    self.assertStringRange(
      "Njord Analytics and Player; (ClubSwan 36); Tue Jun 22nd 2021",
      datetime.datetime(2021, 6, 22), datetime.datetime(
        2021, 6, 22, 23, 59, 59)
    )

    self.assertStringRange(
      "Njord Analytics & Njord Player; 2x Laser Radial; valid November 1st 2021 to December 31st 2024 (price per year)",
      datetime.datetime(2021, 11, 1), datetime.datetime(
        2024, 12, 31, 23, 59, 59)
    )

    self.assertStringRange(
      "Njord Player, TP52, Menorca (Sat 25th - 30th 2021)",
      None, None
    )

    self.assertStringRange(
      "Njord Analytics and Player; M32; valid Sept 1st 2021 - Sept 4th 2022",
      datetime.datetime(2021, 9, 1), datetime.datetime(2022, 9, 4, 23, 59, 59)
    )

    self.assertStringRange(
      "Njord Analytics & Njord Player, SailGP (8 boats), valid Jan 1st 2022 - Mar 31st 2022 (incl. loading all data from 2021/22 SailGP season)",
      datetime.datetime(2022, 1, 1), datetime.datetime(2022, 3, 31, 23, 59, 59)
    )


if __name__ == '__main__':
  unittest.main()
