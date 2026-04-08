from typing import TYPE_CHECKING, TypeVar, TypeGuard, Any, Literal
import threading

import stripe

from django.conf import settings
from django.db import models, transaction


if TYPE_CHECKING:
  from django_stripe_hooks.models import (
    Customer, FundingInstructions, StripeModel,
  )


_write_depth = threading.local()


class allow_stripe_write:
  """Context manager that permits StripeModel.save() and .delete() writes.

  Uses a reentrant counter so nested from_stripe() calls don't prematurely
  revoke write access for the outer call.

  Note: QuerySet.update(), QuerySet.delete(), bulk_create(), and bulk_update()
  bypass save()/delete() entirely and are not guarded by this mechanism.
  """

  def __enter__(self) -> 'allow_stripe_write':
    _write_depth.count = getattr(_write_depth, 'count', 0) + 1
    return self

  def __exit__(self, *args: Any) -> None:
    _write_depth.count = max(0, getattr(_write_depth, 'count', 0) - 1)


T = TypeVar('T', bound='StripeModel[stripe.StripeObject]')
PreSave = dict[
  models.ForeignKey[Any, Any] | models.OneToOneField[Any, Any],
  stripe.StripeObject
]
PostSave = dict[
  models.ManyToOneRel | models.ManyToManyField[Any, Any],
  list[str | stripe.StripeObject]
]
BankTransferType = Literal[
  'eu_bank_transfer',
  'gb_bank_transfer',
  'jp_bank_transfer',
  'mx_bank_transfer',
  'us_bank_transfer',
]


def is_stripe_model(
  val: Any,
) -> TypeGuard['type[StripeModel[stripe.StripeObject]]']:
   return (
     isinstance(val, type)
     and issubclass(val, models.Model)
     and hasattr(val, 'deserialize')
   )


class FundingInstructionsManager(models.Manager['FundingInstructions']):
  def from_stripe(
    self,
    customer: 'Customer',
    bank_transfer_type: BankTransferType,
    currency: str,
  ) -> 'FundingInstructions':
    """Retrieves details from Stripe API and updates or creates object."""
    stripe_client = stripe.StripeClient(settings.STRIPE_SECRET_KEY)
    stripe_obj = stripe_client.v1.customers.funding_instructions.create(
      customer.id,
      params={
        'funding_type': 'bank_transfer',
        'bank_transfer': {'type': bank_transfer_type},
        'currency': currency,
      },
    )
    data = self.model.deserialize(stripe_obj)
    django_obj, created = self.update_or_create(
      customer=customer,
      defaults=data,
    )
    return django_obj


class StripeManager(models.Manager[T]):
  def from_stripe(
    self,
    stripe_obj: stripe.StripeObject,
  ) -> T:
    """Updates or creates a Django instance from a Stripe API object.

    ``deserialize()`` returns a single nested dict — no StripeObjects remain
    after that call. This method then walks the model fields to separate:

    - pre_save: FK fields whose value is a nested dict (expanded object)
    - post_save: reverse FK / M2M fields whose value is a list of dicts

    Pre-save FKs are recursively upserted first so the parent's FK column
    has a valid ID. Post-save relations are handled after the parent is
    upserted.
    """
    data = self.model.deserialize(stripe_obj)
    if not data:
      raise ValueError(f"Deserialized data is empty, got {stripe_obj=}")

    pre_save: PreSave = {}
    post_save: PostSave = {}

    for field in self.model._meta.get_fields():
      if isinstance(field, (models.ForeignKey, models.OneToOneField)):
        if field.name in data and isinstance(data[field.name], stripe.StripeObject):  # noqa: E501
          pre_save[field] = data.pop(field.name)
      elif isinstance(field, (models.ManyToOneRel, models.ManyToManyField)):
        if field.name in data:
          post_save[field] = data.pop(field.name)

    # Resolve pre-save FKs outside the parent's transaction
    for field, related_stripe_obj in pre_save.items():
      assert is_stripe_model(field.related_model)
      related_obj = field.related_model.objects.from_stripe(related_stripe_obj)  # noqa: E501
      data[field.attname] = related_obj.id

    with allow_stripe_write(), transaction.atomic():
      django_obj, created = self.update_or_create(
        id=data.pop('id'),
        defaults=data,
      )

      for field, related_stripe_objs in post_save.items():
        assert is_stripe_model(field.related_model)
        if isinstance(field, models.ManyToManyField):
          getattr(django_obj, field.name).set(
            field.related_model.objects.filter(id__in=related_stripe_objs)
          )
        elif isinstance(field, models.ManyToOneRel):
          for id_or_related_stripe_obj in related_stripe_objs:
            if isinstance(id_or_related_stripe_obj, str):
              # Only a bare ID — no object data to deserialize. The related
              # object's own webhook will set the FK back to this object.
              continue
            related_stripe_obj = id_or_related_stripe_obj
            if getattr(related_stripe_obj, field.field.name) != stripe_obj['id']:  # noqa: E501
              setattr(related_stripe_obj, field.field.name, stripe_obj['id'])
            related_obj = field.related_model.objects.from_stripe(related_stripe_obj)  # noqa: E501
          django_obj.refresh_from_db()

    return django_obj
