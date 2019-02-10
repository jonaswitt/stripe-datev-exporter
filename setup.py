from distutils.core import setup

setup(
    name='Stripe DATEV Exporter',
    version='0.0.1',
    packages=['stripe_datev',],
    install_requires=[
      'stripe',
    ]
    url='https://github.com/jonaswitt/stripe-datev-exporter',
    author='Jonas Witt',
    author_email='mail@jonaswitt.com',
    long_description=open('README.md').read(),
)
