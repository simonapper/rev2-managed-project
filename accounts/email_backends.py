# accounts/email_backends.py
import ssl
import certifi

from django.core.mail.backends.smtp import EmailBackend
from django.utils.functional import cached_property


class GmailTLSEmailBackend(EmailBackend):
    """
    SMTP backend that supplies a real SSLContext object for STARTTLS.
    """

    @cached_property
    def ssl_context(self):
        # MUST return an ssl.SSLContext instance, not a function.
        return ssl.create_default_context(cafile=certifi.where())
