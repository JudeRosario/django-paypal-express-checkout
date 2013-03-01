"""Forms for the ``paypal_express_checkout`` app."""
import httplib
import urllib
import urllib2
import urlparse

from django import forms
from django.conf import settings
from django.core.urlresolvers import reverse
from django.http import Http404
from django.utils.timezone import now
from django.shortcuts import redirect

from paypal_express_checkout.constants import PAYMENT_STATUS, PAYPAL_DEFAULTS
from paypal_express_checkout.models import (
    Item,
    PaymentTransaction,
    PaymentTransactionError,
)


class PayPalFormMixin(object):
    """Common methods for the PayPal forms."""
    def call_paypal(self, post_data):
        """
        Gets the PayPal API URL from the settings and posts ``post_data``.

        :param post_data: The full post data for PayPal containing all needed
          information for the current transaction step.

        """
        try:
            response = urllib2.urlopen(
                settings.API_PAYPAL, data=urllib.urlencode(post_data))
        except (
                urllib2.HTTPError,
                urllib2.URLError,
                httplib.HTTPException), ex:
            self.log_error(ex)
        else:
            parsed_response = urlparse.parse_qs(response.read())
            return parsed_response

    def log_error(self, error_message):
        """
        Saves error information as a ``PaymentTransactionError`` object.

        :param error_message: The message of the exception or response string
          from PayPal.

        """
        payment_error = PaymentTransactionError()
        payment_error.user = self.user
        payment_error.response = error_message
        payment_error.save()
        return payment_error


class DoExpressCheckoutForm(PayPalFormMixin, forms.Form):
    """
    Takes the input from the ``DoExpressCheckoutView``, validates it and
    takes care of the PayPal API operations.

    """
    token = forms.CharField()

    payerID = forms.CharField()

    def __init__(self, user, *args, **kwargs):
        super(DoExpressCheckoutForm, self).__init__(*args, **kwargs)
        try:
            self.transaction = PaymentTransaction.objects.get(
                user=user, transaction_id=self.data['token'])
        except PaymentTransaction.DoesNotExist:
            raise Http404

    def do_checkout(self):
        """Calls PayPal to make the 'DoExpressCheckoutPayment' procedure."""
        post_data = PAYPAL_DEFAULTS
        post_data.update({
            'METHOD': 'DoExpressCheckoutPayment',
            'TOKEN': self.transaction.transaction_id,
            'PAYERID': self.data['payerID'],
            'PAYMENTREQUEST_0_AMT': self.transaction.value,
        })
        parsed_response = self.call_paypal(post_data)
        if parsed_response.get('ACK')[0] == 'Success':
            transaction_id = parsed_response.get(
                'PAYMENTINFO_0_TRANSACTIONID')[0]
            self.transaction.transaction_id = transaction_id
            self.transaction.status = 'pending'
            self.transaction.save()
            return redirect(reverse('paypal_success'))
        elif parsed_response.get('ACK')[0] == 'Failure':
            self.log_error(parsed_response)
            return redirect(reverse('paypal_success'))


class SetExpressCheckoutForm(PayPalFormMixin, forms.Form):
    """
    Takes the input from the ``SetExpressCheckoutView``, validates it and
    takes care of the PayPal API operations.

    """
    item = forms.ModelChoiceField(
        queryset=Item.objects.all()
    )

    quantity = forms.IntegerField(
        required=False,
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super(SetExpressCheckoutForm, self).__init__(*args, **kwargs)

    def set_checkout(self, items):
        """
        Calls PayPal to make the 'SetExpressCheckout' procedure.

        :param items: A list of ``Item`` objects.

        """

        # Gathering data

        if type(items) != list:
            items = [items]
        post_data = PAYPAL_DEFAULTS
        quantity = self.cleaned_data.get('quantity')
        # TODO currently the implementation only allows one single item, so
        # this method is just expanded for future use and quantity has to be
        # set for each item
        total_value = 0
        for counter, item in enumerate(items):
            total_value += item.value * quantity
            post_data.update({
                'L_PAYMENTREQUEST_0_NAME{0}'.format(counter): item.name,
                'L_PAYMENTREQUEST_0_DESC{0}'.format(counter): item.description,
                'L_PAYMENTREQUEST_0_AMT{0}'.format(counter): item.value,
                'L_PAYMENTREQUEST_0_QTY{0}'.format(counter): quantity,
            })
        post_data.update({
            'METHOD': 'SetExpressCheckout',
            'PAYMENTREQUEST_0_AMT': total_value,
            'PAYMENTREQUEST_0_ITEMAMT': total_value,
            'RETURNURL': settings.HOSTNAME + reverse(
                'paypal_confirm'),
            'CANCELURL': settings.HOSTNAME + reverse(
                'paypal_canceled')
        })

        # making the post to paypal and handling the results

        parsed_response = self.call_paypal(post_data)
        if parsed_response.get('ACK')[0] == 'Success':
            token = parsed_response.get('TOKEN')[0]
            transaction = PaymentTransaction(
                user=self.user,
                date=now(),
                transaction_id=token,
                value=total_value,
                status=PAYMENT_STATUS['checkout'],
            )
            transaction.save()
            return redirect(settings.LOGIN_PAYPAL + token)
        elif parsed_response.get('ACK')[0] == 'Failure':
            self.log_error(parsed_response)
            return redirect(reverse('paypal_error'))
