
def escape_csv_field(field_value, sep=","):
  if field_value is None:
    field_value = ""
  field_value = field_value.replace("\r\n", " ").replace(
    "\r", " ").replace("\n", " ").replace(sep, ";")
  return field_value


def lines_to_csv(lines_rows, sep=",", nl="\n"):
  return nl.join(map(lambda l: sep.join(map(lambda f: escape_csv_field(f, sep=sep), l)), lines_rows))
