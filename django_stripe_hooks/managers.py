from typing import TYPE_CHECKING, TypeVar, TypeGuard, Any

import stripe

from django.core.exceptions import ObjectDoesNotExist
from django.db import models, transaction, IntegrityError


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


# TODO: Refactor this, too complicated
class StripeManager(models.Manager[T]):
  def from_stripe(self, stripe_obj: stripe.StripeObject) -> T:
    """Updates or creates a Django instance from a Stripe API object."""

    try:
      with transaction.atomic():
        data, post_save, pre_save = self.model.deserialize(stripe_obj)
        if not data:
          raise ValueError(f"Deserialized data is empty, got {stripe_obj=}")

        # Resolve expanded FK dependencies before saving the parent.
        # Each related object is upserted (via its own from_stripe) so the
        # parent's FK column has a valid ID to reference.
        for field_name, fk_stripe_obj in pre_save.items():
          field = self.model._meta.get_field(field_name)
          if is_stripe_model(field.related_model):
            data[field_name] = field.related_model.objects.from_stripe(fk_stripe_obj)  # noqa: E501

        django_obj, created = self.update_or_create(
          id=data.pop('id'),
          defaults=data,
        )

        for field_name, objs in post_save.items():
          field = self.model._meta.get_field(field_name)
          if isinstance(field, models.ManyToManyField):
            getattr(django_obj, field_name).set(objs)
          elif isinstance(field, models.ManyToOneRel):
            for related_obj in objs:
              related_obj.save()
            django_obj.refresh_from_db()

      return django_obj
    except IntegrityError as outer_e:
      # Raise a specific DoesNotExist exception
      for field in self.model._meta.get_fields():
        if not isinstance(field, (models.ForeignKey, models.OneToOneField)):
          continue

        RelatedModel = field.related_model
        if not is_stripe_model(RelatedModel):
          continue

        try:
          if field.name in data:
            api_id = data[field.name].id
          elif field.attname in data:
            api_id = data[field.attname]
          else:
            raise KeyError(
              f"[{self.model.__name__}] "
              f"{RelatedModel.__name__} missing from deserialized data."
            )
          RelatedModel.objects.get(id=api_id)
        except ObjectDoesNotExist as inner_e:
          raise ObjectDoesNotExist(
            f"[{self.model.__name__}] "
            f"{RelatedModel.__name__} {api_id} does not exist."
          ) from inner_e
      raise IntegrityError(
        f"IntegrityError writing {self.model.__name__} with {data=}"
      ) from outer_e
