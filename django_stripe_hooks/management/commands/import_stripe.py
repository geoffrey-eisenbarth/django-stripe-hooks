import re
from typing import Any

import stripe

from django.conf import settings
from django.core.management.base import BaseCommand

from django_stripe_hooks.models import StripeModel


# Models with no standalone Stripe list endpoint, or imported via a parent
SKIP_MODELS: frozenset[str] = frozenset({
  'ConfirmationToken',  # no Stripe list API
  'Discount',           # no standalone list endpoint
  'InvoiceLineItem',    # requires invoice parameter
  'InvoicePayment',     # requires invoice parameter
  'SubscriptionItem',   # imported inline via Subscription
})


def _service_name(model_name: str) -> str:
  """Convert CamelCase model name to Stripe service name."""
  snake = re.sub(r'(?<!^)(?=[A-Z])', '_', model_name).lower()
  return f'{snake}s'


class Command(BaseCommand):
  help = 'Import all Stripe data into the local database.'

  def handle(self, *args: Any, **options: Any) -> None:
    client = stripe.StripeClient(settings.STRIPE_SECRET_KEY)

    total_imported = 0
    total_errors = 0

    for Model in StripeModel.__subclasses__():
      if Model.__name__ in SKIP_MODELS:
        continue

      service_name = _service_name(Model.__name__)
      service = getattr(client.v1, service_name, None)

      if service is None or not hasattr(service, 'list'):
        continue

      self.stdout.write(f'Importing {Model.__name__}...')
      count = 0
      errors = 0

      # Stripe allows a maximum of 4 expansion levels; data. counts as one
      expand = [
        f'data.{f}' for f in Model.API_EXPAND_FIELDS
        if len(f.split('.')) < 4
      ]
      params: dict[str, Any] = {'limit': 100}
      if expand:
        params['expand'] = expand

      try:
        for stripe_obj in service.list(params=params).auto_paging_iter():
          try:
            Model.objects.from_stripe(stripe_obj)
            count += 1
          except Exception as e:
            errors += 1
            self.stdout.write(self.style.WARNING(
              f'  Error importing {stripe_obj.id}: {e}'
            ))
      except stripe.AuthenticationError:
        self.stdout.write(self.style.ERROR(
          'Authentication failed. Check that STRIPE_SECRET_KEY is correct.'
        ))
        return
      except stripe.StripeError as e:
        self.stdout.write(self.style.ERROR(f'  Stripe error: {e}'))
        total_errors += 1
        continue

      msg = self.style.SUCCESS(f'  ✓ {count} imported')
      if errors:
        msg += self.style.WARNING(f', {errors} errors')
      self.stdout.write(msg)

      total_imported += count
      total_errors += errors

    self.stdout.write(
      f'\nDone. {total_imported} total imported, {total_errors} errors.'
    )
