import time
from typing import Any, Protocol, runtime_checkable

import stripe

import django_stripe_hooks.models as stripe_models


@runtime_checkable
class StripeService(Protocol):
  def retrieve(
    self,
    id: str,
    params: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
  ) -> stripe.StripeObject:
    ...


def fetch(service: StripeService, id: str) -> stripe.StripeObject | None:
  """Fetches a Stripe object with retries and handles soft-deleted objects.

  Notes
  -----
  This util will expand all the necessary fields that StripeModels expect.

  If repeatedly fetching from a single service, consider the following:

  ```
  fetch_product = partial(fetch, stripe_client.v1.products)
  stripe_obj = fetch_product(id)
  ```

  """
  model_name = service.__class__.__name__.replace('Service', '')
  StripeModel = getattr(stripe_models, model_name)

  for attempt in range(5):
    try:
      stripe_obj = service.retrieve(id, params={
        'expand': StripeModel.API_EXPAND_FIELDS,
      })
    except stripe.error.RateLimitError:
      time.sleep(0.5 * (attempt + 1))
    except stripe.error.InvalidRequestError as e:
      is_404 = getattr(e, "http_status", None) == 404
      is_missing = getattr(e, "code", None) == 'resource_missing'
      if is_404 or is_missing:
        # Stripe object soft deleted, return None
        return None
      else:
        raise e
    else:
      return stripe_obj

  return None
