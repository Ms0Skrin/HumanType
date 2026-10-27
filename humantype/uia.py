"""What the focused field holds before the caret, through UI Automation: the Windows twin of what
AT-SPI tells on Linux. Asked by the worker right before it types, never by the hook, so a slow app
cannot stall the keyboard. Fields that do not tell (and any failure) answer None: the fix goes on
as it would without asking.
"""

import logging
import threading

log = logging.getLogger(__name__)

CLSID_CUIAutomation = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
UIA_TextPatternId = 10014
TextPatternRangeEndpoint_Start, TextPatternRangeEndpoint_End = 0, 1
TextUnit_Character = 0

_local = threading.local()  # COM objects belong to the thread that made them
_failed = False


def _client():
    global _failed
    if _failed:
        return None
    client = getattr(_local, "client", None)
    if client is None:
        try:
            import comtypes.client

            module = comtypes.client.GetModule("UIAutomationCore.dll")
            client = (module, comtypes.client.CreateObject(CLSID_CUIAutomation, interface=module.IUIAutomation))
            try:
                # A hung app must not hold a fix for long: the keys typed meanwhile wait for it.
                timeouts = client[1].QueryInterface(module.IUIAutomation2)
                timeouts.ConnectionTimeout = 400
                timeouts.TransactionTimeout = 300
            except Exception:
                pass
        except Exception:
            log.exception("UI Automation is not available")
            _failed = True
            return None
        _local.client = client
    return client


def text_before_caret(limit: int) -> str | None:
    """Up to `limit` characters right before the caret of the focused field, or None when it does not tell."""
    client = _client()
    if client is None:
        return None
    module, uia = client
    try:
        element = uia.GetFocusedElement()
        if not element:
            return None
        pattern = element.GetCurrentPattern(UIA_TextPatternId)
        if not pattern:
            return None
        text = pattern.QueryInterface(module.IUIAutomationTextPattern)
        selection = text.GetSelection()
        if not selection or selection.Length < 1:
            return None
        caret = selection.GetElement(0).Clone()
        # Collapse to the end of any selection: that is where the caret stands.
        caret.MoveEndpointByRange(TextPatternRangeEndpoint_Start, caret, TextPatternRangeEndpoint_End)
        caret.MoveEndpointByUnit(TextPatternRangeEndpoint_Start, TextUnit_Character, -limit)
        before = caret.GetText(-1)
    except Exception as error:
        log.debug("text before the caret: %s", error)
        return None
    if before is None:
        return None
    return before.replace("\r\n", "\n").replace("\r", "\n")[-limit:]
