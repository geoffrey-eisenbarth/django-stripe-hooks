# Django Stripe Hooks

TODO

## Installing

1) Add to `INSTALLED_APPS`:

2) Add to `urls.py`:

`path('stripe/', include('django_stripe_hooks.urls')),`


## Contributing

### Running Tests

1) The test environment requires Stripe CLI package to be installed.

```
curl -L https://github.com/stripe/stripe-cli/releases/download/v1.39.0/stripe_1.39.0_linux_x86_64.tar.gz -o stripe.tar.gz
sudo tar -xvf stripe.tar.gz -C /usr/local/bin
rm stripe.tar.gz

stripe --version

```

2) Run test: `poetry run pytest -s`

To generate coverage report, use `poetry run pytest --cov --cov-branch --cov-report=xml`

Whenever tests fail, you can check `stripe_cli.log` for more details.
