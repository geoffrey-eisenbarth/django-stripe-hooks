from accounts.stripe.tasks import send_welcome_email_job

from rangefilter.filters import DateRangeFilter

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


class BalanceTransactionResource(resources.ModelResource):
  """Import-Export Resource for BalanceTransactions."""
  customer_name = Field()
  customer_email = Field()
  invoice_url = Field()
  products = Field()

  class Meta:
    model = BalanceTransaction
    fields = (
      'id',
      'customer_name',
      'customer_email',
      'products',
      'type',
      'currency',
      'amount',
      'fee',
      'net',
      'status',
      'available_on',
      'invoice_url',
    )
    export_order = fields

  def dehydrate_customer_name(self, bt: BalanceTransaction) -> str:
    if charge := bt.charges.first():
      name = charge.customer.name
    elif refund := bt.refunds.first():
      name = refund.charge.customer.name
    else:
      name = 'N/A'
    return name

  def dehydrate_customer_email(self, bt: BalanceTransaction) -> str:
    if charge := bt.charges.first():
      email = charge.customer.email
    elif refund := bt.refunds.first():
      email = refund.charge.customer.email
    else:
      email = 'N/A'
    return email

  def dehydrate_invoice_url(self, bt: BalanceTransaction) -> str:
    if charge := bt.charges.first():
      url = charge.invoice.invoice_pdf
    else:
      url = 'N/A'
    return url

  def dehydrate_products(self, bt: BalanceTransaction) -> str:
    if charge := bt.charges.first():
      items = charge.invoice.subscription.items.all()
      products = ', '.join(items.values_list('price__nickname', flat=True))
    else:
      products = 'N/A'
    return products


@admin.register(BalanceTransaction)
class BalanceTransactionAdmin(
  ExportActionMixin,
  StripeModelAdmin[BalanceTransaction]
):
  resource_classes = (
    BalanceTransactionResource,
  )
  formats = (
    base_formats.CSV,
    base_formats.XLS,
    base_formats.XLSX,
    base_formats.JSON,
  )
  
  list_filter = (
    ('available_on', DateRangeFilter),
  )

class PromotionCodeAdmin:
  def link(self, obj: PromotionCode) -> str:
    if product := obj.coupon.products.first():
      html = format_html(
        '<a href="{url}?{params}">{link_symbol}</a>',
        url=reverse('shopping_cart', host='www'),
        params=f'price={product.prices.first().id}&promo={obj.id}',
        link_symbol=mark_safe('&#128279'),
      )
    else:
      html = 'N/A'
    return html
