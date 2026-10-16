"""Deterministic security checks that run BEFORE a navigation is allowed.

These need no network and no AI, so they always run. They catch the cheap,
common tricks: credentials hidden in URLs, punycode lookalike domains,
typosquatted brand names, javascript:/data: navigations and risky downloads.
"""
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlparse

LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}

# Registrable labels of brands that phishing pages most often imitate.
BRANDS = [
    'google', 'microsoft', 'paypal', 'apple', 'amazon', 'facebook', 'instagram',
    'netflix', 'chase', 'wellsfargo', 'bankofamerica', 'coinbase', 'binance',
    'steampowered', 'discord', 'github', 'dropbox', 'linkedin', 'outlook', 'icloud',
]

# Legitimate domains that would otherwise trip the lookalike check.
ALLOW = {
    'microsoftonline.com', 'microsoft.com', 'live.com', 'office.com', 'google.com',
    'googleapis.com', 'gstatic.com', 'googleusercontent.com', 'youtube.com',
    'amazonaws.com', 'amazon.com', 'paypal.com', 'paypalobjects.com', 'apple.com',
    'facebook.com', 'fbcdn.net', 'instagram.com', 'netflix.com', 'nflxvideo.net',
    'chase.com', 'wellsfargo.com', 'bankofamerica.com', 'coinbase.com', 'binance.com',
    'steampowered.com', 'steamcommunity.com', 'discord.com', 'discordapp.com',
    'github.com', 'githubusercontent.com', 'dropbox.com', 'linkedin.com', 'icloud.com',
    'outlook.com', 'brave.com', 'duckduckgo.com',
}

RISKY_TLDS = {'zip', 'mov'}

# Files that can run code on Windows. Downloads of these require confirmation.
DANGEROUS_EXTENSIONS = {
    '.exe', '.msi', '.msix', '.bat', '.cmd', '.scr', '.pif', '.com', '.vbs', '.vbe',
    '.js', '.jse', '.wsf', '.wsh', '.ps1', '.psm1', '.hta', '.jar', '.reg', '.lnk',
    '.iso', '.img', '.dll', '.cpl', '.msc', '.application', '.appx', '.appxbundle',
}


@dataclass(frozen=True)
class Verdict:
    level: str  # 'ok' | 'warn' | 'block'
    reason: str = ''


def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _registrable(host: str) -> str:
    parts = host.split('.')
    return '.'.join(parts[-2:]) if len(parts) >= 2 else host


def lookalike(host: str) -> str | None:
    """Return the brand a hostname imitates, or None."""
    host = host.lower()
    if _registrable(host) in ALLOW:
        return None
    labels = host.split('.')
    second = labels[-2] if len(labels) >= 2 else host
    for brand in BRANDS:
        if second == brand:
            continue  # exact brand on an unknown TLD is handled by the operator, not here
        # typosquat: paypa1, gooogle, microsofft
        if len(second) >= 5 and _levenshtein(second, brand) <= 1:
            return brand
        # brand glued to decoys: paypal-secure, login-microsoft, apple.id-verify
        if brand in second and any(c in second for c in '-_0123456789'):
            return brand
        if brand in labels[:-2]:  # paypal.com.evil.net
            return brand
    return None


def check_navigation(url: str) -> Verdict:
    try:
        p = urlparse(url)
    except ValueError:
        return Verdict('block', 'Malformed URL')

    scheme = p.scheme.lower()
    if scheme in ('javascript', 'data', 'vbscript'):
        return Verdict('block', f'{scheme}: URLs cannot be opened as a page')
    if scheme == 'file':
        return Verdict('block', 'Local file access is disabled in this browser')
    if scheme not in ('http', 'https', 'about', 'edge'):
        return Verdict('ok')

    host = (p.hostname or '').lower()
    if not host:
        return Verdict('ok')
    if host in LOCAL_HOSTS:
        return Verdict('ok')

    # These two are warnings, not hard blocks: real sites do use them (e.g. international
    # domain names), so you can always continue after reading why it looks suspicious.
    if p.username is not None or '@' in p.netloc:
        return Verdict('warn', 'The address hides text before an @ sign - a classic trick to disguise '
                               'which site a link really opens')
    if any(label.startswith('xn--') for label in host.split('.')):
        return Verdict('warn', 'International (punycode) domain name - it can look like a familiar name '
                               'but be a different site')

    try:
        ip_address(host.strip('[]'))
        return Verdict('warn', 'Site is a raw IP address, not a domain name')
    except ValueError:
        pass

    brand = lookalike(host)
    if brand:
        return Verdict('warn', f'Domain looks like it is imitating {brand}')
    if host.rsplit('.', 1)[-1] in RISKY_TLDS:
        return Verdict('warn', f'.{host.rsplit(".", 1)[-1]} domains are commonly abused')
    if len(url) > 2000:
        return Verdict('warn', 'Unusually long URL')
    return Verdict('ok')


def is_dangerous_download(filename: str) -> bool:
    name = filename.lower()
    return any(name.endswith(ext) for ext in DANGEROUS_EXTENSIONS)
