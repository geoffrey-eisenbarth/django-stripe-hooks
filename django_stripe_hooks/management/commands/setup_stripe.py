from typing import Any

import stripe

from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import NoReverseMatch, reverse

from django_stripe_hooks.models import StripeModel


# Hosts that are never valid webhook targets
EXCLUDED_HOSTS = {'localhost', '127.0.0.1', '0.0.0.0', '*'}


class Command(BaseCommand):
  """Create or update a Stripe webhook endpoint for this application."""

  help = 'Create or update a Stripe webhook endpoint based on ALLOWED_HOSTS.'

  def select_url(self) -> str | None:
    """Provide a webhook URL from ALLOWED_HOSTS for the user to confirm."""

    hosts = [
      h for h in settings.ALLOWED_HOSTS
      if h not in EXCLUDED_HOSTS and not h.startswith('.')
    ]

    if not hosts:
      self.stdout.write(self.style.ERROR(
        'No production hosts found in ALLOWED_HOSTS. '
        'Add your domain (e.g. "example.com") before running this command.'
      ))
      return None

    if len(hosts) == 1:
      host = hosts[0]
    else:
      self.stdout.write(
        'Multiple hosts found in ALLOWED_HOSTS:'
      )
      for i, h in enumerate(hosts, 1):
        self.stdout.write(f'  {i}. {h}')
      while True:
        raw = input(
          'Enter the number of the host to use: '
        ).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(hosts):
          host = hosts[int(raw) - 1]
          break
        self.stdout.write(self.style.ERROR(
          f'Please enter a number between 1 and {len(hosts)}.'
        ))

    try:
      path = reverse('stripe_webhooks')
    except NoReverseMatch:
      self.stdout.write(
        '\nCould not resolve the "stripe_webhooks" URL name. '
        "This is expected if you're using a custom URL for the webhook view."
      )
      path = input(
        'Enter the webhook path (e.g. /payments/stripe/webhooks/): '
      ).strip()
      if not path.startswith('/'):
        path = '/' + path

    url = f'https://{host}{path}'

    self.stdout.write(
      f'\nWebhook URL: {self.style.SUCCESS(url)}'
    )
    confirm = input(
      'Proceed with this URL? [y/N] '
    ).strip().lower()
    if confirm != 'y':
      self.stdout.write('Aborted.')
      return None

    return url

  def select_events(self) -> list[str]:
    """Walk through each model's events, letting the user select per-model."""
    selected: list[str] = []

    models_with_events = [
      cls for cls in StripeModel.__subclasses__()
      if getattr(cls, 'WEBHOOK_EVENTS', ())
    ]

    for cls in models_with_events:
      events: tuple[str, ...] = cls.WEBHOOK_EVENTS
      self.stdout.write(f'\n{cls.__name__}:')
      for i, event in enumerate(events, 1):
        self.stdout.write(f'  [ ] {i}. {event}')

      raw = input(
        '  Select (comma-separated numbers), "a" for all, or Enter to skip: '
      ).strip().lower()

      if raw == 'a':
        selected.extend(events)
      elif raw:
        for part in raw.split(','):
          part = part.strip()
          if part.isdigit() and 1 <= int(part) <= len(events):
            selected.append(events[int(part) - 1])
          else:
            self.stdout.write(self.style.WARNING(
              f'  Ignoring unrecognised value: {part!r}'
            ))

    if not selected:
      return []

    self.stdout.write('\nSelected events:')
    for event in sorted(selected):
      self.stdout.write(f'  [x] {event}')

    confirm = input('\nConfirm event selection? [y/N] ').strip().lower()
    if confirm != 'y':
      self.stdout.write('Aborted.')
      return []

    return selected

  def create_or_update(self, url: str, events: list[str]) -> None:
    """Call the Stripe API to create or update the webhook endpoint."""

    client = stripe.StripeClient(settings.STRIPE_SECRET_KEY)
    endpoint_id = getattr(settings, 'STRIPE_WEBHOOK_ENDPOINT_ID', None)

    try:
      if endpoint_id:
        endpoint = client.v1.webhook_endpoints.update(
          endpoint_id,
          params={
            'url': url,
            'enabled_events': events,
          },  # type: ignore[arg-type]
        )
        self.stdout.write(self.style.SUCCESS(
          f'\n✓ Webhook endpoint updated: {endpoint.id}'
        ))
      else:
        endpoint = client.v1.webhook_endpoints.create(params={
          'url': url,
          'enabled_events': events,  # type: ignore[typeddict-item]
        })
        self.stdout.write(self.style.SUCCESS(
          f'\n✓ Webhook endpoint created: {endpoint.id}'
        ))
        self.stdout.write(
          '\nAdd these to your settings.py:\n\n'
          f'    STRIPE_WEBHOOK_ENDPOINT_ID = "{endpoint.id}"\n'
          f'    STRIPE_WEBHOOK_SECRET_KEY  = "{endpoint.secret}"\n'
          '\n'
        )
    except stripe.AuthenticationError:
      self.stdout.write(self.style.ERROR(
        'Authentication failed. Check that STRIPE_SECRET_KEY is correct.'
      ))
    except stripe.StripeError as e:
      self.stdout.write(self.style.ERROR(f'Stripe error: {e}'))

  def handle(self, *args: Any, **options: Any) -> None:
    if not (url := self.select_url()):
      return None

    if not (events := self.select_events()):
      self.stdout.write(self.style.ERROR('No events selected. Aborting.'))
      return None

    self.create_or_update(url, events)
