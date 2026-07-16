import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Item, NotificationSettings, SearchQuery

logger = logging.getLogger(__name__)

_env = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape(["html"]),
)


def build_digest_context(db: Session, ns: NotificationSettings) -> dict:
    searches = db.query(SearchQuery).filter(SearchQuery.enabled.is_(True)).order_by(SearchQuery.sort_order).all()

    buy_it_now_groups = []
    ending_soon_groups = []

    import datetime
    lookahead = datetime.datetime.utcnow() + datetime.timedelta(hours=ns.ending_soon_lookahead_hours)

    for search in searches:
        bin_items = [
            i for i in search.items
            if i.status == "active" and i.is_deal and i.listing_type == "FIXED_PRICE"
        ]
        if bin_items:
            buy_it_now_groups.append((search.name, sorted(bin_items, key=lambda i: i.ratio_to_median or 1)))

        ending_items = [
            i for i in search.items
            if i.status == "active" and i.is_deal and i.listing_type == "AUCTION"
            and i.end_time is not None and i.end_time <= lookahead
        ]
        if ending_items:
            ending_soon_groups.append((search.name, sorted(ending_items, key=lambda i: i.end_time)))

    return {
        "buy_it_now_groups": buy_it_now_groups,
        "ending_soon_groups": ending_soon_groups,
        "lookahead_hours": ns.ending_soon_lookahead_hours,
    }


def send_daily_digest(db: Session) -> bool:
    ns = db.get(NotificationSettings, 1)
    if ns is None or not ns.daily_digest_enabled or not ns.recipient_email:
        return False

    context = build_digest_context(db, ns)
    if not context["buy_it_now_groups"] and not context["ending_soon_groups"]:
        logger.info("Daily digest: nothing to send today")
        return False

    template = _env.get_template("email_digest.html")
    html = template.render(**context)

    return _send_email(ns.recipient_email, "eBay Deal Finder - Daily Digest", html)


def _send_email(to_address: str, subject: str, html_body: str) -> bool:
    if not settings.smtp_host or not settings.smtp_from_address:
        logger.warning("SMTP is not configured (SMTP_HOST/SMTP_FROM_ADDRESS) - skipping send")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_address
    msg["To"] = to_address
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.sendmail(settings.smtp_from_address, [to_address], msg.as_string())
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_address)
        return False
