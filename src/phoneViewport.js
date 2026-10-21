import React from "react";

// ── Phones: the page itself is locked to a 1600px-wide desktop layout (public/index.html viewport), which makes full-screen modals tiny.
// While a full-screen modal is open on a touch phone, switch the viewport meta to the device width so the modal lays out natively
// (its own responsive classes then apply), and restore the desktop viewport + scroll position when the last modal closes.
let _phoneModalCount = 0, _phoneSavedScroll = 0, _origViewport = null;
export const _isPhone = () => {
  try { return window.matchMedia("(pointer: coarse)").matches && Math.min(window.screen.width, window.screen.height) < 600; } catch { return false; }
};
const _viewportMeta = () => document.querySelector('meta[name="viewport"]');
export const usePhoneViewport = () => {
  React.useEffect(() => {
    if (!_isPhone()) return undefined;
    const meta = _viewportMeta();
    if (!meta) return undefined;
    if (_phoneModalCount++ === 0) {
      _origViewport = meta.getAttribute("content");
      _phoneSavedScroll = window.scrollY;
      meta.setAttribute("content", "width=device-width, initial-scale=1");
    }
    return () => {
      if (--_phoneModalCount === 0) {
        meta.setAttribute("content", _origViewport || "width=1600, shrink-to-fit=no");
        setTimeout(() => window.scrollTo(0, _phoneSavedScroll), 80);
      }
    };
  }, []);
};

// Mount this inside a modal to get the phone layout while it is open (the hook above, but tied to the modal's lifetime).
export const PhoneViewportGuard = () => { usePhoneViewport(); return null; };
