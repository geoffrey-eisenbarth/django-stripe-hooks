from decimal import Decimal
import time
import uuid
from typing import TypeVar, Any, Generator

import stripe
import pytest

from django.apps import apps
from django.conf import settings
from django.core.handlers.wsgi import WSGIHandler
from django.core.signals import got_request_exception
from django.db import models as models
from django.http import HttpRequest

from django_stripe_hooks import models as stripe_models
from django_stripe_hooks.managers import is_stripe_model
from pytest_django.live_server_helper import LiveServer

T = TypeVar('T', bound='stripe_models.StripeModel[Any]')


@pytest.mark.django_db(transaction=True)
class TestWebhooks:

  @pytest.fixture(autouse=True)
  def setup_stripe(self) -> None:
    self.stripe_client = stripe.StripeClient(settings.STRIPE_SECRET_KEY)

  @pytest.fixture(autouse=True)
  def fail_on_server_exception(self) -> Generator[None, None, None]:
    exceptions = []

    def signal_handler(
      sender: WSGIHandler,
      request: HttpRequest,
      **kwargs: Any,
    ) -> None:
      # Capture the actual exception object for the error message.
      exc = kwargs.get('exception')
      if exc is None:
        exc_info = kwargs.get('exc_info')
        if exc_info:
          exc = exc_info[1]  # (type, value, traceback) → value
      if exc is not None:
        exceptions.append(exc)

    # Connect the signal before the test starts
    got_request_exception.connect(signal_handler)

    yield

    # Disconnect after the test to prevent side effects in other tests
    got_request_exception.disconnect(signal_handler)

    if exceptions:
      # Fail the test with the details of the first exception found
      pytest.fail(
        f'Server-side exception during webhook processing: {exceptions[0]}'
      )

  def wait_for_object(
    self,
    model_class: type[T],
    timeout: int = 60,
    **kwargs: Any,
  ) -> T:
    """Generic polling logic to detect object existence."""
    retries = timeout * 2
    while retries > 0:
      if obj := model_class.objects.filter(**kwargs).first():
        return obj
      time.sleep(0.5)
      retries -= 1
    pytest.fail(
      f'Timed out waiting for create on {model_class.__name__} with {kwargs=}'
    )

  def assert_fk_integrity(self) -> None:
    """Verify no dangling FK references across all synced models.

    Since db_constraint=False is used throughout (Stripe webhooks may arrive
    out of order), this check compensates by asserting that all FK IDs stored
    in the DB resolve to an existing object by the end of the test.
    """
    broken = []
    for cls in apps.get_app_config('django_stripe_hooks').get_models():
      if not is_stripe_model(cls):
        continue
      for obj in cls.objects.all():
        for field in obj._meta.get_fields():
          if not isinstance(field, (models.ForeignKey, models.OneToOneField)):
            continue
          if (fk_id := getattr(obj, field.attname)) is None:
            continue
          assert is_stripe_model(field.related_model)
          if not field.related_model.objects.filter(id=fk_id).exists():
            broken.append(
              f"{cls.__name__}({obj.id}).{field.name}_id="
              f"{fk_id} → {field.related_model.__name__} missing"
            )

    assert not broken, "Dangling FK references:\n" + "\n".join(broken)

  def test_crud(self, live_server: LiveServer) -> None:
    """Integration testing for Product and Billing primatives.

    Notes
    -----
    Each method of TestWebhooks will set up its own database and rollback
    before the next test, so it is important to cram everything into one
    method in order to ensure that delayed webhooks still have access to
    the relevant database objects.
    """

    # Product primatives
    s_product = self.stripe_client.v1.products.create(params={
      'name': f'Test Product {uuid.uuid4().hex[:8]}',
      'description': 'Description for test product',
      'statement_descriptor': 'STATEMENT',
      'metadata': {'category': 'Test Product'},
    })
    d_product = self.wait_for_object(
      stripe_models.Product,
      id=s_product.id,
    )

    s_price = self.stripe_client.v1.prices.create(params={
      'product': s_product.id,
      'nickname': 'Test Nickname',
      'unit_amount': 2000,
      'currency': 'usd',
      'recurring': {'interval': 'year'},
      'metadata': {'category': 'Test Price'},
    })
    d_price = self.wait_for_object(
      stripe_models.Price,
      id=s_price.id,
      product=d_product,
    )

    s_coupon = self.stripe_client.v1.coupons.create(params={
      'name': f'Test Coupon {uuid.uuid4().hex[:8]}',
      'percent_off': 20,
      'duration': 'once',
      'applies_to': {'products': [s_product.id]},
    })
    d_coupon = self.wait_for_object(
      stripe_models.Coupon,
      id=s_coupon.id,
      products=d_product,
    )

    s_promo = self.stripe_client.v1.promotion_codes.create(params={
      'promotion': {
        'type': 'coupon',
        'coupon': s_coupon.id,
      },
      'code': f'PROMO_{uuid.uuid4().hex[:8].upper()}'
    })
    self.wait_for_object(
      stripe_models.PromotionCode,
      id=s_promo.id,
      coupon=d_coupon,
    )

    # Billing primatives
    s_customer = self.stripe_client.v1.customers.create(params={
      'name': 'Test Customer',
      'email': 'test@customer.com',
    })
    d_customer = self.wait_for_object(
      stripe_models.Customer,
      id=s_customer.id
    )

    s_payment_method = self.stripe_client.v1.payment_methods.create(params={
      'type': 'card',
      'card': {'token': 'tok_visa'},
    })
    self.stripe_client.v1.payment_methods.attach(
      s_payment_method.id,
      params={'customer': s_customer.id},
    )
    d_payment_method = self.wait_for_object(
      stripe_models.PaymentMethod,
      id=s_payment_method.id,
      customer=d_customer,
    )

    s_subscription = self.stripe_client.v1.subscriptions.create(params={
      'customer': s_customer.id,
      'items': [{'price': s_price.id, 'quantity': 1}],
      'discounts': [{'promotion_code': s_promo.id}],
      'default_payment_method': s_payment_method.id,
      'collection_method': 'charge_automatically',
      'payment_behavior': 'default_incomplete',
    })

    d_payment_intent = self.wait_for_object(
      stripe_models.PaymentIntent,
      customer=d_customer,
      payment_method=d_payment_method,
    )

    # Confirm PaymentIntent, verify Invoice and Subscription updated
    s_payment_intent = self.stripe_client.v1.payment_intents.confirm(
      d_payment_intent.id,
    )
    d_subscription = self.wait_for_object(
      stripe_models.Subscription,
      id=s_subscription.id,
      status='active',
      customer=d_customer,
      default_payment_method=d_payment_method,
    )
    self.wait_for_object(
      stripe_models.Invoice,
      status='paid',
      customer=d_customer,
      subscription=d_subscription,
    )

    # Refund the charge
    d_charge = self.wait_for_object(
      stripe_models.Charge,
      customer=d_customer,
      payment_intent_id=s_payment_intent.id,
    )
    s_refund = self.stripe_client.v1.refunds.create(params={
      'amount': s_payment_intent.amount,
      'payment_intent': s_payment_intent.id,
      'reason': 'requested_by_customer',
    })
    d_balance_txn = self.wait_for_object(
      stripe_models.BalanceTransaction,
      refunds=s_refund.id,
    )
    self.wait_for_object(
      stripe_models.Refund,
      id=s_refund.id,
      charge=d_charge,
      balance_transaction=d_balance_txn,
    )

    # Test ManyToMany and ReverseForeignKey relations
    d_coupon.refresh_from_db()
    assert d_coupon.products.exists()
    d_subscription.refresh_from_db()
    assert d_subscription.items.exists()

    # Test currency unit conversion
    assert d_price.unit_amount == Decimal(20.00)

    # Test deletion webhooks and soft deletes
    s_product = self.stripe_client.v1.products.create(params={
      'name': f'Delete Product {uuid.uuid4().hex[:8]}',
      'description': 'Description for deleted product',
      'statement_descriptor': 'STATEMENT',
      'metadata': {'category': 'Test Product'},
    })
    d_product = self.wait_for_object(
      stripe_models.Product,
      id=s_product.id,
    )

    self.stripe_client.v1.products.delete(s_product.id)
    self.stripe_client.v1.coupons.delete(s_coupon.id)
    self.stripe_client.v1.customers.delete(s_customer.id)

    # Assert soft deletes
    self.wait_for_object(
      stripe_models.Product,
      id=s_product.id,
      active=False,
    )
    self.wait_for_object(
      stripe_models.Coupon,
      id=s_coupon.id,
      valid=False,
    )
    self.wait_for_object(
      stripe_models.Customer,
      id=s_customer.id,
      deleted=True,
    )
    self.wait_for_object(
      stripe_models.Subscription,
      id=s_subscription.id,
      status='canceled',
    )

    # Wait for any delayed webhooks to arrive, then verify FK integrity.
    # Retry up to 60 seconds to give CI headroom for late webhook delivery.
    deadline = time.monotonic() + 60
    while True:
      time.sleep(5)
      try:
        self.assert_fk_integrity()
        break
      except AssertionError:
        if time.monotonic() >= deadline:
          raise
