# Stripe DATEV Exporter

## Requirements

* Tested on Python 3.9
* Dependencies listed in `requirements.txt`

## Environment

Consider using Python's [virtualenv](https://pypi.org/project/virtualenv/) or alternatives. To setup virtualenv initially:

```
virtualenv -p python3 venv
```

To activate in your current shell:

```
. venv/bin/activate
```

Then install dependencies:

```
pip install -r requirements.txt
```

## Notes

Run the `fill_account_numbers` command before `download`. This assigns the `accountNumber` metadata to each customer, this will be the account number for all booking records related to this customer. You can assign the `accountNumber` metadata using a different approach, if you like, but every customer (which has any transactions) needs this metadata.
