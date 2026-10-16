"""Brave-style privacy features that are not ad blocking.

- Tracking-link cleaning: click identifiers such as fbclid and gclid let ad
  networks follow you from one site to the next. They are removed from the
  address before a page is opened (Brave calls this query filtering).
- Global Privacy Control: every request says "do not sell or share my data"
  (the Sec-GPC header) and pages can read navigator.globalPrivacyControl.
  California and Colorado treat this signal as a legally binding opt-out.
"""
from urllib.parse import unquote_plus, urlsplit, urlunsplit

# Cross-site click and email identifiers. Campaign tags like utm_source are left alone:
# they name the campaign, not the person, and sites use them for referral credit.
TRACKING_PARAMS = frozenset("""
fbclid gclid gclsrc gbraid wbraid dclid msclkid yclid ymclid ysclid twclid ttclid li_fat_id igshid
epik srsltid mc_eid _hsenc __hsfp __hssc __hstc hsCtaTracking mkt_tok oly_anon_id oly_enc_id
rb_clickid s_cid vero_conv vero_id wickedid _openstat ml_subscriber ml_subscriber_hash ss_email_id
bsft_clkid bsft_uid irclickid _branch_match_id _branch_referrer unicorn_click_id at_recipient_id
at_recipient_list sc_customer sc_eh sc_uid et_rid guce_referrer guce_referrer_sig _kx
""".split())

GPC_JS = """
(() => {
  if ('globalPrivacyControl' in Navigator.prototype) return;
  try {
    Object.defineProperty(Navigator.prototype, 'globalPrivacyControl',
      {get() { return true; }, configurable: true, enumerable: true});
  } catch (e) {}
})();
"""


def strip_tracking(url: str) -> str:
    """The URL without tracking parameters; everything else is kept byte for byte."""
    parts = urlsplit(url)
    if not parts.query or parts.scheme not in ('http', 'https'):
        return url
    fields = parts.query.split('&')
    kept = [f for f in fields if unquote_plus(f.split('=', 1)[0]) not in TRACKING_PARAMS]
    if len(kept) == len(fields):
        return url
    return urlunsplit(parts._replace(query='&'.join(kept)))
