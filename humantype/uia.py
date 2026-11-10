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


def warm_up() -> None:
    """Makes the client and asks once, so the first fix does not pay ~120 ms for it."""
    try:
        if _client() is not None:
            text_before_caret(1)
    except Exception:
        pass


def caret_point() -> tuple[int, int] | None:
    """The bottom of the caret of the focused field, in screen pixels, for programs that draw
    their own caret (browsers, terminals, most modern apps). None when the field does not tell."""
    client = _client()
    if client is None:
        return None
    module, uia = client
    try:
        element = uia.GetFocusedElement()
        pattern = element.GetCurrentPattern(UIA_TextPatternId) if element else None
        if not pattern:
            return None
        selection = pattern.QueryInterface(module.IUIAutomationTextPattern).GetSelection()
        if not selection or selection.Length < 1:
            return None
        caret = selection.GetElement(0).Clone()
        caret.MoveEndpointByRange(TextPatternRangeEndpoint_Start, caret, TextPatternRangeEndpoint_End)
        # A caret is an empty range and has no rectangle of its own: take the character after it,
        # or, at the end of the text, the one before.
        for endpoint, step, right_edge in ((TextPatternRangeEndpoint_End, 1, False), (TextPatternRangeEndpoint_Start, -1, True)):
            near = caret.Clone()
            if not near.MoveEndpointByUnit(endpoint, TextUnit_Character, step):
                continue
            rects = near.GetBoundingRectangles()
            if rects and len(rects) >= 4 and rects[3] > 0:
                left, top, width, height = (rects[-4:] if right_edge else rects[:4])
                return int(left + (width if right_edge else 0)), int(top + height)
    except Exception as error:
        log.debug("the caret's place: %s", error)
    return None


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
