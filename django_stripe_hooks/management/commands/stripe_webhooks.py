from typing import Any

import stripe

from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import NoReverseMatch, reverse

from django_stripe_hooks.models import StripeModel


# Hosts that are never valid webhook targets
EXCLUDED_HOSTS = {'localhost', '127.0.0.1', '0.0.0.0', '*'}


class Command(BaseCommand):
  """Create, update, or disable a Stripe webhook endpoint."""

  help = 'Create, update, disable, or delete Stripe webhook endpoints.'

  def fetch_endpoints(
    self,
    client: stripe.StripeClient,
  ) -> list[stripe.WebhookEndpoint]:
    """Return all webhook endpoints from Stripe."""
    return list(
      client.v1.webhook_endpoints.list(
        params={'limit': 100},
      ).auto_paging_iter()
    )

  def select_endpoint(
    self,
    endpoints: list[stripe.WebhookEndpoint],
  ) -> stripe.WebhookEndpoint | str | None:
    """Let the user pick an existing endpoint, create new, or quit.

    Returns the chosen endpoint, 'new', or None to quit.
    """
    self.stdout.write('\nExisting endpoints:')
    for i, ep in enumerate(endpoints, 1):
      self.stdout.write(f'  {i}. {ep.url} ({ep.status})')

    while True:
      raw = input(
        '\nSelect an endpoint to manage, "n" to create a new one, or "q" to quit: '  # noqa: E501
      ).strip().lower()
      if raw == 'q':
        return None
      if raw == 'n':
        return 'new'
      if raw.isdigit() and 1 <= int(raw) <= len(endpoints):
        return endpoints[int(raw) - 1]
      self.stdout.write(self.style.ERROR(
        f'Please enter a number between 1 and {len(endpoints)}, "n", or "q".'
      ))

  def select_action(
    self,
    endpoint: stripe.WebhookEndpoint,
  ) -> str | None:
    """Ask what to do with an existing endpoint."""
    self.stdout.write(f'\nManaging: {endpoint.url} ({endpoint.status})')
    while True:
      raw = input(
        '  [u] Update\n  [d] Disable\n  [x] Delete\n  [q] Quit\n> '
      ).strip().lower()
      if raw in ('u', 'd', 'x', 'q'):
        return None if raw == 'q' else raw
      self.stdout.write(self.style.ERROR(
        'Please enter "u", "d", "x", or "q".'
      ))

  def select_url(self, default_url: str | None = None) -> str | None:
    """Provide a webhook URL for the user to confirm."""

    if default_url:
      self.stdout.write(f'\nCurrent URL: {self.style.SUCCESS(default_url)}')
      keep = input('Keep this URL? [Y/n] ').strip().lower()
      if keep != 'n':
        return default_url
      while True:
        url = input('Enter the full webhook URL: ').strip()
        if url.startswith('https://'):
          return url
        self.stdout.write(self.style.ERROR('URL must start with https://'))

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
      self.stdout.write('Multiple hosts found in ALLOWED_HOSTS:')
      for i, h in enumerate(hosts, 1):
        self.stdout.write(f'  {i}. {h}')
      while True:
        raw = input('Enter the number of the host to use: ').strip()
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

    while True:
      self.stdout.write(f'\nWebhook URL: {self.style.SUCCESS(url)}')
      confirm = input('Proceed with this URL? [y/N] ').strip().lower()
      if confirm == 'y':
        return url
      url = input('Enter the full webhook URL: ').strip()
      if not url.startswith('https://'):
        self.stdout.write(self.style.ERROR('URL must start with https://'))
        url = f'https://{host}{path}'

  def select_events(
    self,
    current_events: list[str] | None = None,
  ) -> list[str] | None:
    """Walk through each model's events, letting the user select per-model.

    At least one event must be selected. Returns None if the user quits.
    If current_events is provided, the user is asked whether to keep them.
    """
    if current_events:
      self.stdout.write('\nCurrent events:')
      for event in sorted(current_events):
        self.stdout.write(f'  {event}')
      keep = input('Keep these events? [Y/n] ').strip().lower()
      if keep != 'n':
        return current_events

    models_with_events = [
      cls for cls in StripeModel.__subclasses__()
      if getattr(cls, 'WEBHOOK_EVENTS', ())
    ]

    while True:
      selected: list[str] = []

      for cls in models_with_events:
        events: tuple[str, ...] = cls.WEBHOOK_EVENTS
        self.stdout.write(f'\n{cls.__name__}:')
        for i, event in enumerate(events, 1):
          self.stdout.write(f'  {i}. {event}')

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
        self.stdout.write(self.style.ERROR(
          '\nAt least one event must be selected.'
        ))
        raw = input('Re-select [r] or quit [q]? ').strip().lower()
        if raw == 'r':
          continue
        self.stdout.write('Aborted.')
        return None

      self.stdout.write('\nSelected events:')
      for event in sorted(selected):
        self.stdout.write(f'  [x] {event}')

      confirm = input('\nConfirm event selection? [y/N] ').strip().lower()
      if confirm == 'y':
        return selected
      self.stdout.write('Aborted.')
      return None

  def update_endpoint(
    self,
    client: stripe.StripeClient,
    endpoint_id: str,
    url: str,
    events: list[str],
  ) -> None:
    try:
      endpoint = client.v1.webhook_endpoints.update(
        endpoint_id,
        params={
          'url': url,
          'enabled_events': events,
        },  # type: ignore[arg-type]
      )
      if endpoint.status == 'enabled':
        self.stdout.write(self.style.SUCCESS(
          f'\n✓ Webhook endpoint updated: {endpoint.id}'
        ))
      else:
        self.stdout.write(self.style.WARNING(
          f'Endpoint {endpoint.id} updated but is not enabled. '
          f'Current status: {endpoint.status}.'
        ))
    except stripe.AuthenticationError:
      self.stdout.write(self.style.ERROR(
        'Authentication failed. Check that STRIPE_SECRET_KEY is correct.'
      ))
    except stripe.StripeError as e:
      self.stdout.write(self.style.ERROR(f'Stripe error: {e}'))

  def disable_endpoint(
    self,
    client: stripe.StripeClient,
    endpoint_id: str,
  ) -> None:
    try:
      endpoint = client.v1.webhook_endpoints.update(
        endpoint_id,
        params={'disabled': True},
      )
      if endpoint.status == 'disabled':
        self.stdout.write(self.style.SUCCESS(
          f'\n✓ Webhook endpoint disabled: {endpoint.id}'
        ))
      else:
        self.stdout.write(self.style.WARNING(
          f'Endpoint {endpoint.id} updated but is not disabled. '
          f'Current status: {endpoint.status}'
        ))
    except stripe.AuthenticationError:
      self.stdout.write(self.style.ERROR(
        'Authentication failed. Check that STRIPE_SECRET_KEY is correct.'
      ))
    except stripe.StripeError as e:
      self.stdout.write(self.style.ERROR(f'Stripe error: {e}'))

  def create_endpoint(
    self,
    client: stripe.StripeClient,
    url: str,
    events: list[str],
  ) -> None:
    try:
      endpoint = client.v1.webhook_endpoints.create(params={
        'url': url,
        'enabled_events': events,  # type: ignore[typeddict-item]
      })
      if endpoint.status == 'enabled':
        self.stdout.write(self.style.SUCCESS(
          f'\n✓ Webhook endpoint created: {endpoint.id}'
        ))
      else:
        self.stdout.write(self.style.WARNING(
          f'Endpoint {endpoint.id} created but is not enabled. '
          f'Current status: {endpoint.status}'
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

  def delete_endpoint(
    self,
    client: stripe.StripeClient,
    endpoint_id: str,
  ) -> None:
    try:
      deleted = client.v1.webhook_endpoints.delete(endpoint_id)
      if deleted.deleted:
        self.stdout.write(self.style.SUCCESS(
          f'\n✓ Webhook endpoint deleted: {deleted.id}'
        ))
      else:
        self.stdout.write(self.style.WARNING(
          f'Delete request succeeded but endpoint {deleted.id} may not have been removed.'  # noqa: E501
        ))
    except stripe.AuthenticationError:
      self.stdout.write(self.style.ERROR(
        'Authentication failed. Check that STRIPE_SECRET_KEY is correct.'
      ))
    except stripe.StripeError as e:
      self.stdout.write(self.style.ERROR(f'Stripe error: {e}'))

  def handle(self, *args: Any, **options: Any) -> None:
    client = stripe.StripeClient(settings.STRIPE_SECRET_KEY)

    self.stdout.write('Fetching webhook endpoints from Stripe...')
    endpoints = self.fetch_endpoints(client)

    if not endpoints:
      self.stdout.write('No existing endpoints found.')
      if not (url := self.select_url()):
        return
      if (events := self.select_events()) is None:
        return
      self.create_endpoint(client, url, events)
      return

    choice = self.select_endpoint(endpoints)
    if choice is None:
      return

    if choice == 'new':
      if not (url := self.select_url()):
        return
      if (events := self.select_events()) is None:
        return
      self.create_endpoint(client, url, events)
      return

    assert isinstance(choice, stripe.WebhookEndpoint)
    endpoint = choice
    action = self.select_action(endpoint)
    if action is None:
      return

    if action == 'd':
      confirm = input(
        f'Disable {endpoint.url}? [y/N] '
      ).strip().lower()
      if confirm == 'y':
        self.disable_endpoint(client, endpoint.id)
      else:
        self.stdout.write('Aborted.')
    elif action == 'x':
      confirm = input(
        f'Permanently delete {endpoint.url}? [y/N] '
      ).strip().lower()
      if confirm == 'y':
        self.delete_endpoint(client, endpoint.id)
      else:
        self.stdout.write('Aborted.')
    elif action == 'u':
      if not (url := self.select_url(default_url=endpoint.url)):
        return
      if (events := self.select_events(
        current_events=list(endpoint.enabled_events)
      )) is None:
        return
      self.update_endpoint(client, endpoint.id, url, events)
