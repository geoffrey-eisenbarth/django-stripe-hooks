# Stripe Webhooks for Django

[![codecov](https://codecov.io/gh/geoffrey-eisenbarth/django-stripe-hooks/graph/badge.svg?token=4L51B3LIUJ)](https://codecov.io/gh/geoffrey-eisenbarth/django-stripe-hooks)

`django-stripe-hooks` is a Django application that keeps your local database in sync with Stripe by consuming webhook events.
Instead of making manual API calls every time you need Stripe data, this library automatically maintains local, up-to-date copies of Stripe objects in your database.

Incoming webhooks are signature-verified and routed to `update_or_create` calls on the corresponding Django model.
Your database reflects Stripe's authoritative state; you query it like any other Django model.

> **Warning:** This package is under active development.
> We will not make API stability promises until a stable version is released.

---

## Supported Models

| Stripe Object | Django Model |
|---|---|
| Product | `Product` |
| Price | `Price` |
| Coupon | `Coupon` |
| PromotionCode | `PromotionCode` |
| Discount | `Discount` |
| Customer | `Customer` |
| PaymentMethod | `PaymentMethod` |
| PaymentIntent | `PaymentIntent` |
| Subscription | `Subscription` |
| SubscriptionItem | `SubscriptionItem` |
| Invoice | `Invoice` |
| InvoiceLineItem | `InvoiceLineItem` |
| InvoicePayment | `InvoicePayment` |
| BalanceTransaction | `BalanceTransaction` |
| Charge | `Charge` |
| Refund | `Refund` |

---

## Installation and Configuration

### 1. Install the package

```
pip install django-stripe-hooks
```

### 2. Add to `INSTALLED_APPS`

```python
INSTALLED_APPS = [
  # ...
  'django_stripe_hooks',
]
```

### 3. Add Stripe credentials to `settings.py`

Retrieve your API keys from the [Stripe Dashboard](https://dashboard.stripe.com/apikeys).

```python
STRIPE_PUBLIC_KEY = "pk_live_..."
STRIPE_SECRET_KEY = "sk_live_..."
STRIPE_WEBHOOK_SECRET_KEY = "whsec_..."  # filled in after step 5
```

> **Security:** Never commit API keys to version control. Use environment variables or a secrets manager.

### 4. Include the webhook URL

In your project's `urls.py`:

```python
from django.urls import path, include

urlpatterns = [
  path('stripe/', include('django_stripe_hooks.urls')),
  # ...
]
```

This exposes `https://yourdomain.com/stripe/webhooks/` as the webhook endpoint.

If you prefer to use a custom URL (e.g. because you are [subclassing `StripeWebhooks`](#author-hooks)), see the [Custom URL](#custom-url) section below.

### 5. Run the setup management command

```
python manage.py setup_stripe
```

This interactive command will:

1. Detect your production domain(s) from `ALLOWED_HOSTS` and confirm the webhook URL.
2. Walk through each supported model and let you choose which Stripe event types to subscribe to.
3. Create (or update) the webhook endpoint in Stripe via the API.

After creation, the command prints two values to add to your `settings.py`:

```python
STRIPE_WEBHOOK_ENDPOINT_ID = "we_..."    # used to update the endpoint later
STRIPE_WEBHOOK_SECRET_KEY  = "whsec_..." # used to verify incoming signatures
```

> **Important:** `STRIPE_WEBHOOK_SECRET_KEY` is only returned once by Stripe. Copy it immediately.

Re-run `python manage.py setup_stripe` any time you need to update the endpoint URL or event subscriptions.
With `STRIPE_WEBHOOK_ENDPOINT_ID` set in your settings, it will update the existing endpoint rather than creating a new one.

### 6. Run migrations

```
python manage.py migrate
```

---

## API Version

This package pins the Stripe Python SDK to version 14.x, which uses API version `2026-02-25.clover`.
The `setup_stripe` management command creates webhook endpoints using the SDK's API version automatically — no manual version selection in the Stripe Dashboard is required.

---

## Author Hooks

To react to specific webhook events (e.g. sending a welcome email when a subscription is created), subclass `StripeWebhooks` and add a method named after the event type with dots replaced by underscores:

```python
from django_stripe_hooks.views import StripeWebhooks

class MyStripeWebhooks(StripeWebhooks):

  def customer_subscription_created(self) -> None:
    subscription = self.django_obj
    send_mail(
      subject="Thanks for subscribing!",
      message="...",
      from_email="noreply@example.com",
      recipient_list=[subscription.customer.email],
    )

  def invoice_paid(self) -> None:
    # self.event    — the raw Stripe Event object
    # self.stripe_obj — the fetched Stripe object (Invoice in this case)
    # self.django_obj — the corresponding Django model instance
    ...
```

Hook methods are called after the local database has been updated, so `self.django_obj` always reflects the latest state.

Hook methods may optionally return an `HttpResponse`.
If they return `None`, the default `200 OK` response is used.

> **Note:** If the object type is not implemented by this library (e.g. a `KeyError` during model resolution), the hook is still called but `self.stripe_obj` and `self.django_obj` will not be set. Guard accordingly if you handle unimplemented event types.

### Custom URL

If you use a subclassed view, remove the `django_stripe_hooks.urls` include and register your view directly:

```python
# urls.py
from django.urls import path
from myapp.views import MyStripeWebhooks

urlpatterns = [
  path('payments/webhooks/', MyStripeWebhooks.as_view(), name='stripe_webhooks'),
]
```

Keep the `name='stripe_webhooks'` so the `setup_stripe` management command can resolve the path automatically.
If you use a different name, the command will prompt you to enter the path manually.

---

## Webhook Delivery Caveats

Stripe delivers webhooks **asynchronously and without guaranteed ordering**.
A few consequences to be aware of:

- **Events may arrive out of order.**
  For example, an `invoice.paid` event may arrive before the `customer.created` event for the same customer.
  The library handles this with `db_constraint=False` on all foreign keys — Stripe is the authoritative source, and referential integrity is enforced at the application level rather than the database level.

- **Events may be retried.**
  If your endpoint returns a non-2xx response, Stripe will retry the event.
  The library's `update_or_create` logic is idempotent, so retries are safe.

- **Events may be delayed.**
  In practice, most webhooks arrive within a few seconds, but network conditions and Stripe processing can delay delivery by minutes.
  Do not rely on webhooks for time-critical operations where you need an immediate response.

- **Not all objects generate webhooks.** 
  `SubscriptionItem`, `InvoiceLineItem`, `InvoicePayment`, and `BalanceTransaction` do not have their own Stripe webhook events.   They are populated as nested objects within parent webhook payloads (e.g. a `customer.subscription.updated` event includes subscription items).

---

## Testing

To install dev dependencies and set up the test environment:

```
git clone git@github.com:geoffrey-eisenbarth/django-stripe-hooks.git
cd django-stripe-hooks
pip install poetry
poetry install --with dev
```

The integration test suite requires the [Stripe CLI](https://stripe.com/docs/stripe-cli) to forward webhook events to your local server.

### 1. Install the Stripe CLI

```
curl -L https://github.com/stripe/stripe-cli/releases/download/v1.39.0/stripe_1.39.0_linux_x86_64.tar.gz | sudo tar -xz -C /usr/local/bin stripe
stripe --version
```

### 2. Configure test credentials

Copy `.env.example` to `.env` and fill in your Stripe test-mode keys:

```
STRIPE_PUBLIC_KEY=pk_test_...
STRIPE_SECRET_KEY=sk_test_...
```

`STRIPE_WEBHOOK_SECRET_KEY` is set automatically by the test suite via the Stripe CLI tunnel.

### 3. Run tests

```
poetry run pytest -s
```

To generate a coverage report:

```
poetry run pytest --cov --cov-branch --cov-report=xml
```

### Troubleshooting

- `OSError: [Errno 98] Address already in use` — run `pkill stripe` to clear any hanging CLI processes.
- Detailed webhook forwarding logs are written to `tests/stripe_cli.log`.

---

## License

Distributed under the MIT license.

## Support

[Open an issue](https://github.com/geoffrey-eisenbarth/django-stripe-hooks/issues) for bugs or questions.
