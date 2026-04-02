from typing import TYPE_CHECKING, TypeVar, TypeGuard, Any

import stripe

from django.db import models, transaction


if TYPE_CHECKING:
  from django_stripe_hooks.models import StripeModel


T = TypeVar('T', bound='StripeModel[stripe.StripeObject]')


def is_stripe_model(
  val: Any,
) -> TypeGuard['type[StripeModel[stripe.StripeObject]]']:
   return (
     isinstance(val, type)
     and issubclass(val, models.Model)
     and hasattr(val, 'deserialize')
   )


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
    with transaction.atomic():
      data = self.model.deserialize(stripe_obj)
      if not data:
        raise ValueError(f"Deserialized data is empty, got {stripe_obj=}")

      pre_save: dict[str, stripe.StripeObject] = {}
      post_save: dict[str, list[stripe.StripeObject]] = {}

      for field in self.model._meta.get_fields():
        if isinstance(field, (models.ForeignKey, models.OneToOneField)):
          if field.name in data and isinstance(data[field.name], dict):
            pre_save[field.name] = data.pop(field.name)
        elif isinstance(field, (models.ManyToOneRel, models.ManyToManyField)):
          if field.name in data:
            post_save[field.name] = data.pop(field.name)

      for field_name, related_stripe_obj in pre_save.items():
        field = self.model._meta.get_field(field_name)
        if is_stripe_model(field.related_model):
          related_obj = field.related_model.objects.from_stripe(related_stripe_obj)  # noqa: E501
          data[field_name] = related_obj

      django_obj, created = self.update_or_create(
        id=data.pop('id'),
        defaults=data,
      )

      for field_name, related_stripe_objs in post_save.items():
        field = self.model._meta.get_field(field_name)
        if not is_stripe_model(field.related_model):
          continue

        if isinstance(field, models.ManyToManyField):
          ids = [d['id'] for d in related_stripe_objs]
          getattr(django_obj, field_name).set(
            field.related_model.objects.filter(id__in=ids)
          )
        elif isinstance(field, models.ManyToOneRel):
          for related_stripe_obj in related_stripe_objs:
            if getattr(related_stripe_obj, field.field.name) != stripe_obj['id']:  # noqa: E501
              setattr(related_stripe_obj, field.field.name, stripe_obj['id'])
            related_obj = field.related_model.objects.from_stripe(related_stripe_obj)  # noqa: E501
          django_obj.refresh_from_db()

    return django_obj
