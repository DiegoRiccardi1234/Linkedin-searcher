"""Reading the mailbox to find out which applications were actually sent.

The app never learns the outcome of an application, because applying happens on
LinkedIn or on the employer's own form. The one thing that always comes back is
a confirmation email — so this package reads the mailbox, recognises those
messages, and matches them to the postings the user opened from the archive.

Three rules hold across every module here, and each one is a promise to the
person whose mailbox this is:

* **Read only.** Nothing in this package can send, move, delete or even mark a
  message as read. On IMAP the mailbox is selected read-only and only headers
  are fetched; on Microsoft the token is scoped to ``Mail.Read``, which the
  server enforces regardless of what this code does. ``smtplib`` is never
  imported, and a test walks the source to keep it that way.
* **No model ever sees a message.** Recognition is regex tables and a company
  name comparison. A plausible guess on weak evidence is what produces false
  positives at scale, and unlike a scan verdict this one WRITES to the record of
  what you have applied for.
* **Uncertain means untouched.** When a message could belong to two offers, or
  to none, nothing changes: it goes to a review queue for a human to answer.
"""

from app.mail.config import MailAccount, default_host_for, load_account, save_account
from app.mail.errors import (
    MailAuthError,
    MailConfigError,
    MailReauthRequired,
    MailTransientError,
)
from app.mail.matcher import MailHeader, MatchResult, PendingJob, classify

__all__ = [
    "MailAccount",
    "MailAuthError",
    "MailConfigError",
    "MailHeader",
    "MailReauthRequired",
    "MailTransientError",
    "MatchResult",
    "PendingJob",
    "classify",
    "default_host_for",
    "load_account",
    "save_account",
]
