"""
Hide the Remote API key and the PIN in the driver's log (fork patch BL-9).

ucapi 0.7.0 logs every message to and from the Remote at DEBUG (it sets its own logger
to DEBUG, whatever UC_LOG_LEVEL says) and hides only the values of keys it knows, such
as "password". The setup flow's Backup sends the whole configuration, the Remote API
key included, as one JSON string (a text field's value), and Restore sends it back as
another (``restore_data``): there was no key name to hide, so the key reached the log
in clear. ucapi-framework also logs each setup answer, the PIN included, at DEBUG (its
logger stays at WARNING here, but nothing stops someone raising it) and a restored
device it can't read at WARNING.

A filter on the root logger's handlers sees every record that reaches the log, from
any logger, and masks the values of SECRET_KEYS wherever they appear: as JSON (plain,
or escaped inside a JSON string, as the Backup text field's value is) or as Python
repr (a dict's ``'key': 'value'``, a dataclass's ``key='value'``).

:copyright: (c) 2026 by madalone.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
import re

SECRET_KEYS = ("remote_api_key", "password")
REDACTED = "***REDACTED***"

_KEYS = "|".join(SECRET_KEYS)
# "key": "value" as JSON. Inside a JSON string (the Backup text field, restore_data) the
# quotes are escaped, once or more, so the value ends at the next quote or backslash.
_JSON = re.compile(r'((?:\\)*"(?:' + _KEYS + r')(?:\\)*"\s*:\s*(?:\\)*")[^"\\]*')
# 'key': 'value' (a dict's repr) and key='value' (a dataclass's repr), either quote.
_REPR = re.compile(
    r"((?:'(?:" + _KEYS + r")'\s*:\s*|\b(?:" + _KEYS + r")=)(['\"]))(.*?)\2"
)


def redact(text: str) -> str:
    """Return ``text`` with the values of SECRET_KEYS masked."""
    text = _JSON.sub(lambda m: m.group(1) + REDACTED, text)
    return _REPR.sub(lambda m: m.group(1) + REDACTED + m.group(2), text)


class RedactSecrets(logging.Filter):
    """Mask the values of SECRET_KEYS in every record a handler emits; drop none."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Mask the secrets in ``record``'s message. Always keeps the record."""
        try:
            message = record.getMessage()
        except Exception:  # pylint: disable=broad-exception-caught
            return True  # the handler reports a record it can't format, as before
        if not any(key in message for key in SECRET_KEYS):
            return True
        masked = redact(message)
        if masked != message:
            record.msg = masked
            record.args = None
        return True


def install() -> None:
    """Add the filter to every handler of the root logger.

    Call it after ``logging.basicConfig()``: records from every logger reach the log
    through those handlers.
    """
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RedactSecrets) for f in handler.filters):
            handler.addFilter(RedactSecrets())
