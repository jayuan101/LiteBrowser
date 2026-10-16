"""Makes Lite a candidate for Windows' default browser.

Windows 10/11 never lets a program set the default silently: the app registers
itself (http/https URL associations, under HKCU so no admin is needed) and the
button opens the Default apps page straight on Lite's entry, where one click
by the user completes the switch.
"""
import os
import winreg

CLIENT_KEY = r'Software\Clients\StartMenuInternet\LiteBrowser'
PROGID = 'LiteBrowser.URL'
REGISTERED_AS = 'LiteBrowser'
USER_CHOICE = r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice'


def register(exe_path: str, client_key: str = CLIENT_KEY, progid: str = PROGID,
             registered_as: str = REGISTERED_AS):
    """Write Lite's browser capabilities into the user's registry."""
    icon = f'"{exe_path},0"'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, client_key) as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, 'Lite')
        with winreg.CreateKey(k, 'DefaultIcon') as sub:
            winreg.SetValueEx(sub, None, 0, winreg.REG_SZ, icon)
        with winreg.CreateKey(k, 'Capabilities') as cap:
            winreg.SetValueEx(cap, 'ApplicationName', 0, winreg.REG_SZ, 'Lite')
            winreg.SetValueEx(cap, 'ApplicationIcon', 0, winreg.REG_SZ, icon)
            winreg.SetValueEx(cap, 'ApplicationDescription', 0, winreg.REG_SZ,
                              'A lightweight, privacy-first web browser.')
            with winreg.CreateKey(cap, 'URLAssociations') as ua:
                winreg.SetValueEx(ua, 'http', 0, winreg.REG_SZ, progid)
                winreg.SetValueEx(ua, 'https', 0, winreg.REG_SZ, progid)
        with winreg.CreateKey(k, r'shell\open\command') as sub:
            winreg.SetValueEx(sub, None, 0, winreg.REG_SZ, f'"{exe_path}"')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf'Software\Classes\{progid}') as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, 'Lite Document')
        with winreg.CreateKey(k, 'DefaultIcon') as sub:
            winreg.SetValueEx(sub, None, 0, winreg.REG_SZ, icon)
        with winreg.CreateKey(k, r'shell\open\command') as sub:
            winreg.SetValueEx(sub, None, 0, winreg.REG_SZ, f'"{exe_path}" "%1"')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\RegisteredApplications') as k:
        winreg.SetValueEx(k, registered_as, 0, winreg.REG_SZ, client_key + r'\Capabilities')


def is_default() -> bool:
    """True when Windows currently opens links with Lite."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, USER_CHOICE) as k:
            progid, _ = winreg.QueryValueEx(k, 'ProgId')
            return str(progid).startswith('LiteBrowser')
    except OSError:
        return False


def open_settings():
    """The Default apps page, already scrolled to Lite's entry."""
    os.startfile(f'ms-settings:defaultapps?registeredAppUser={REGISTERED_AS}')
