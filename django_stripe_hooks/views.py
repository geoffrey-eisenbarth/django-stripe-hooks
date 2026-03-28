from typing import Any

import stripe

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic.base import View

from django_stripe_hooks.models import (
  StripeModel,
  PaymentIntent, PaymentMethod, Invoice,
  Charge, Refund, BalanceTransaction,
)


DJANGO_MODELS = {
  DjangoModel.__name__: DjangoModel
  for DjangoModel in StripeModel.__subclasses__()
}

EVENT_NAMES = """
customer_created
promotion_code_updated
payment_method_attached
payment_method_automatically_updated
invoice_updated:
  invoice_finalized
  invoice_voided
  invoice_paid
customer_subscription_created (create SubscriptionItems too?)
customer_subscription_updated
charge_refunded
charge_succeeded

customer_deleted
customer_subscription_deleted (not deleted, just set to status=cancelled)

"""


@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhooks(View):
  """Intercept Stripe webhooks and update local database."""

  def resolve_django_model(self, stripe_name: str) -> type[StripeModel[Any]]:
    django_model_name = ''.join(map(
      lambda s: s.title(),
      stripe_name.split('_'),
    ))
    return DJANGO_MODELS[django_model_name]

  def post(self, request: HttpRequest) -> HttpResponse:
    # Construct the event
    self.event = stripe.Webhook.construct_event(  # type: ignore[no-untyped-call]  # noqa: E501
      payload=request.body,
      sig_header=request.META['HTTP_STRIPE_SIGNATURE'],
      secret=settings.STRIPE_WEBHOOK_SECRET_KEY,
    )

    try:
      # Resolve the related Django Model
      DjangoModel = self.resolve_django_model(self.event.data.object.object)
    except KeyError as e:
      django_model_name = e.args[0]
      response = HttpResponse(
        f"[django-stripe-hooks] {django_model_name} not implemented.",
        status=500,
      )
    else:
      # Refresh from Stripe and create/update locally
      StripeClass = getattr(stripe, DjangoModel.__name__)
      self.stripe_obj = StripeClass.retrieve(self.event.data.object.id)
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

  # TODO: related_objs
  # TODO: how to expand=['payment_method']?
  def invoice_updated(self) -> HttpResponse | None:
    # Update or create related objects locally
    if self.stripe_obj.payment_intent:
      stripe_pi = stripe.PaymentIntent.retrieve(
        self.stripe_obj.payment_intent,
        expand=['payment_method'],
      )
      PaymentIntent.from_stripe(stripe_pi)

      stripe_pm = stripe_pi.payment_method
      assert isinstance(stripe_pm, stripe.PaymentMethod)
      if stripe_pm.type == 'card':
        PaymentMethod.from_stripe(stripe_pm)
      elif stripe_pm.type == 'customer_balance':
        pass
      else:
        raise NotImplementedError(
          f"Unsupported PaymentMethod type: {stripe_pm.type}"
        )

    # Update or create Invoice locally
    Invoice.from_stripe(self.stripe_obj)

    return None

  # TODO: related_objs
  def charge_refunded(self) -> HttpResponse | None:
    for stripe_re in self.stripe_obj.refunds.data:
      stripe_txn = stripe.BalanceTransaction.retrieve(
        stripe_re.balance_transaction
      )
      BalanceTransaction.from_stripe(stripe_txn)
      Refund.from_stripe(stripe_re)

    return None

  # TODO: related_objs
  def charge_succeeded(self) -> HttpResponse | None:
    # Create the related BalanceTransaction first
    stripe_txn = stripe.BalanceTransaction.retrieve(
      self.stripe_obj.balance_transaction
    )
    BalanceTransaction.from_stripe(stripe_txn)

    # Now create the Charge
    Charge.from_stripe(self.stripe_obj)

    return None
