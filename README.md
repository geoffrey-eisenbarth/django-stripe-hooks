# Stripe Webhooks for Django

[![codecov](https://codecov.io/gh/geoffrey-eisenbarth/django-stripe-hooks/graph/badge.svg?token=4L51B3LIUJ)](https://codecov.io/gh/geoffrey-eisenbarth/django-stripe-hooks)

`django-stripe-hooks` is a Django application designed to synchronize your local database with Stripe's data models using webhooks.
Instead of making manual API calls every time you need information, this library automatically maintains local, up-to-date copies of Stripe objects—such as Customers, Subscriptions, Products, Prices, and more—directly in your Django project.
It handles incoming webhook signals, manages signature verification, and ensures your local state mirrors Stripe's authoritative data.

**Warning:** This package is under active development.
While it is our intention to develop with a consistent API going forward, we will not make promises until a later version is released.


## Installation and Configuration

1) Install the package:

`pip install django-stripe-hooks`

2) Add to `INSTALLED_APPS`:

In your Django project's settings.py file, add `'django_stripe_hooks'` to the `'INSTALLED_APPS'` list:

```
INSTALLED_APPS = [
  # ... django apps
  'django_stripe_hooks',
  # ... other apps
]
```

3) Include URLs:

In your project's `urls.py` file, include the `django_stripe_hooks` URLs:

```
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
  path('admin/', admin.site.urls),
  path('stripe/', include('django_stripe_hooks.urls')),
  # ... other URL patterns
]
```

4) Stripe API Credentials:

Head to your Stripe Dashboard to retrieve your API keys.
Configure them in your `settings.py`:

```
# Required settings
STRIPE_PUBLIC_KEY = "pk_test_..."
STRIPE_SECRET_KEY = "sk_test_..."
STRIPE_WEBHOOK_SECRET_KEY = "whsec_..."
```

Important: Store your API credentials securely.
Avoid committing them directly to your version control repository.

Make sure Stripe is sending their webhook request to the proper address.
This must be set up on your Stripe Dashboard.

**Important**: This app pins the Python Stripe SDK to version 14, which utilizes API version `2026-02-25.clover`.
You must set your webhook version to `2026-02-25.clover` in your Stripe Dashboard to ensure webhooks use the correct request format.

5) Run Migrations:

`python manage.py migrate`


## Usage

`django_stripe_hooks` automatically validates incoming webhook signatures using your `STRIPE_WEBHOOK_SECRET` and creates/updates the corresponding local models.
To react to specific events (e.g., sending a welcome email after a local Customer is created), you can extend the `StripeWebhooks` view.
Add a method based on the webhook event name (replacing dots (`.`) with underscores (`_`)).
When your method is run, you will have access to the following:

- `self.event`: the Stripe event object,
- `self.stripe_obj`: the relevant Stripe object, and
- `self.django_obj`: an updated Django model instance corresponding to `self.stripe_obj`.

```
from django_stripe_hooks.views import StripeWebhooks

class MyStripeWebhooks(StripeWebhooks):
  def customer_subscription_created(self) -> None:
    subscription = self.django_obj
    send_mail(
      subject="Thank you for your order!",
      message="...",
      from_email="me@email.com",
      recipient_list=[subscription.customer.emai],
      html_message="...",
    )
```

If you're adding your own webhooks, be sure to add them to a `urls.py` and remove the `django-stripe-hooks` entry from step 3 above.


## Testing

To install dev dependencies and set up the test environment:

```
> git clone git@github.com:geoffrey-eisenbarth/django-stripe-hooks.git
> cd django-stripe-hooks
> pip install poetry
> poetry install --with dev
```

The test suite requires the Stripe CLI to simulate webhook traffic via a local tunnel.

1) Download and install Stripe CLI:

```
> curl -L https://github.com/stripe/stripe-cli/releases/download/v1.39.0/stripe_1.39.0_linux_x86_64.tar.gz
> sudo tar -xz -C /usr/local/bin stripe
> stripe --version  # verify installation
```

2) Run the test suite with pytest:

```
> poetry run pytest -s
```

To generate a coverage report and XML for CI:

```
> poetry run pytest --cov --cov-branch --cov-report=xml
```

### Troubleshooting

If you encounter `OSError: [Errno 98] Address already in use`, run `pkill stripe` to clear any hanging CLI processes.

Detailed logs for the webhook forwarding can be found in `tests/stripe_cli.log`.


## License

This package is currently distributed under the MIT license.


## Support

If you have any issues or questions, please feel free to [open an issue](https://github.com/geoffrey-eisenbarth/django-stripe-hooks/issues)!
