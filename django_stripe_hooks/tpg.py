from accounts.stripe.tasks import send_welcome_email_job

from www.shop.config import (
  NEWSLETTER_NICKNAME, FORECAST_ALL_NICKNAME, FORECAST_MAJ_NICKNAME,
)


from django_ctct.models import Contact, ContactList


NICKNAME_TO_CONTACTLIST = {
  NEWSLETTER_NICKNAME: 'Newsletter: Paid',
  FORECAST_ALL_NICKNAME: 'Forecast: All Texas Regions',
  FORECAST_MAJ_NICKNAME: 'Forecast: Major Texas Metros',
}

def get_contact_list(subscription: Subscription) -> ContactList | None:
  """Grabs relevant CTCT ContactList."""

  for item in subscription.items.all():
    if list_name := NICKNAME_TO_CONTACTLIST.get(item.price.nickname):
      contact_list = ContactList.objects.filter(name=list_name).first()
      return contact_list
  return None


class View:
  def invoice_paid(self) -> None:
    """Occurs when an Invoice is paid."""
    if self.stripe_obj.billing_reason == 'subscription_create':
      # Send welcome email to subscriber
      send_welcome_email_job.enqueue(invoice=self.stripe_obj)

  def customer_subscription_updated(self) -> None:
    """Occurs whenever a subscription changes, such as switching
    from one plan to another, or changing status from trial to active.

    NOTES
    -----
    Do NOT upgade SubscriptionItems based on the response from Stripe,
    since it may be outdated.

    """

    # Update the Subscription object
    django_sub = Subscription.from_stripe(self.stripe_obj)

    # Add to appropriate ContactLists
    if contact_list := get_contact_list(django_sub):
      customer = Customer.objects.get(id=self.stripe_obj.customer)
      contact = Contact.objects.get(email=customer.email)
      if django_sub.status.is_active:
        contact.lists.add(contact_list)
        if contact_list.name.startswith('Forecast:'):
          # Avoid belonging to multiple Forecast ContactLists during upgrade
          others = ContactList.objects.filter(name__startswith='Forecast:')
          others = others.exclude(pk=contact_list.pk)
          contact.lists.remove(*others)
      else:
        contact.lists.remove(contact_list)

  def customer_subscription_deleted(self) -> None:
    """Occurs when a customer's subscription ends.

    Notes
    -----
    Subscriptions are not deleted, just set to `status='cancelled'`.

    """

    # Update the Subscription object
    django_sub = Subscription.from_stripe(self.stripe_obj)

    # Remove User from relevant CTCT list
    if contact_list := get_contact_list(django_sub):
      customer = Customer.objects.get(id=self.stripe_obj.customer)
      contact = Contact.objects.get(email=customer.email)
      contact.lists.remove(contact_list)


