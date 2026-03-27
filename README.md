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

tar -xvf stripe_1.39.0_linux_x86_64.tar.gz

sudo mv stripe /usr/local/bin/

rm stripe.tar.gz

stripe --version

```

2) Run test: `poetry run pytest -s`

Whenever tests fail, you can check `stripe_cli.log` for more details.
