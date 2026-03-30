from typing import Any, Protocol, cast, runtime_checkable

import stripe

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.views.decorators.csrf import csrf_exempt
from django.views.generic.base import View

from django_stripe_hooks.models import StripeModel


DJANGO_MODELS = {
  DjangoModel.__name__: DjangoModel
  for DjangoModel in StripeModel.__subclasses__()
}


@runtime_checkable
class StripeService(Protocol):
  def retrieve(
    self,
    id: str,
    params: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
  ) -> Any:
    ...


@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhooks(View):
  """Intercept Stripe webhooks and update local database."""

  @cached_property
  def stripe_client(self) -> stripe.StripeClient:
    return stripe.StripeClient(settings.STRIPE_SECRET_KEY)

  @property
  def stripe_name(self) -> str:
    return cast(str, self.event.data.object['object'])  # mypy wants key access

  @property
  def stripe_service(self) -> StripeService:
    service = getattr(self.stripe_client.v1, f'{self.stripe_name}s')
    return cast(StripeService, service)

  def resolve_django_model(self) -> type[StripeModel[Any]]:
    django_model_name = ''.join(map(
      lambda s: s.title(),
      self.stripe_name.split('_'),
    ))
    return DJANGO_MODELS[django_model_name]

  def get_stripe_service_params(self) -> dict[str, Any]:
    params = {}
    expands = {
      'coupon': ['applies_to'],
      'promotion_code': ['promotion.coupon'],
      'invoice': ['payment_intent'],
      'charge': ['balance_transaction'],
      'refund': ['balance_transaction'],
    }
    if expand := expands.get(self.stripe_name):
      params['expand'] = expand
    return params

  def post(self, request: HttpRequest) -> HttpResponse:
    # Construct the event
    self.event = self.stripe_client.construct_event(
      payload=request.body,
      sig_header=request.META['HTTP_STRIPE_SIGNATURE'],
      secret=settings.STRIPE_WEBHOOK_SECRET_KEY,
    )

    try:
      # Resolve the related Django Model
      DjangoModel = self.resolve_django_model()
    except KeyError as e:
      django_model_name = e.args[0]
      response = HttpResponse(
        f"[django-stripe-hooks] {django_model_name} not implemented.",
        status=500,
      )
    else:
      try:
        self.stripe_obj = self.stripe_service.retrieve(
          self.event.data.object['id'],  # mypy prefers key access
          params=self.get_stripe_service_params()
        )
      except stripe.error.InvalidRequestError:
        # Stripe object soft deleted, use event data
        self.django_obj = DjangoModel.from_stripe(self.event.data.object)
      else:
        self.django_obj = DjangoModel.from_stripe(self.stripe_obj)

      response = HttpResponse(
        "[django-stripe-hooks] Success!",
        status=200,
      )

    finally:
      # Allow authors to hook in
      author_hook = self.event.type.replace('.', '_')
      if hasattr(self, author_hook):
        response = getattr(self, author_hook)() or response

    return response
