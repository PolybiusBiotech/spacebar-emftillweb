# Expire unpaid kiosk orders whose timeout has passed. Run on a schedule
# (e.g. once a minute via cron or a systemd timer) — it is a one-shot sweep,
# not a long-running daemon: it expires what is due and exits.

from django.core.management.base import BaseCommand

from emf.kiosk import expire_orders
from emf.tilldb import tillsession


class Command(BaseCommand):
    help = ("Expire unpaid kiosk orders whose timeout has passed "
            "(run on a schedule).")

    def handle(self, *args, **options):
        with tillsession() as s:
            expired = expire_orders(s, location=None)
            s.commit()

        self.stdout.write(f"Expired {len(expired)} kiosk order(s).")
