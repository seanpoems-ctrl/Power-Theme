import React from "react";
import useMarketStore from "./useMarketStore";

/**
 * GlobalAlertBanner — Sticky alert strip at the top of the dashboard.
 *
 * Visible when:
 *   • isReversalDetected is true  → animated emerald pulse, aggressive long signal
 *   • creditRegime === 'Stress'   → solid rose, capital protection warning
 *
 * Reversal takes priority over Stress if both are true.
 */
export default function GlobalAlertBanner() {
  const isReversalDetected = useMarketStore((s) => s.isReversalDetected);
  const reversalType       = useMarketStore((s) => s.reversalType);
  const creditRegime       = useMarketStore((s) => s.creditRegime);

  const showReversal = isReversalDetected;
  const showStress   = !showReversal && creditRegime === "Stress";

  if (!showReversal && !showStress) return null;

  if (showReversal) {
    return (
      <div
        role="alert"
        aria-live="assertive"
        className="
          relative z-30 w-full
          bg-emerald-600 animate-pulse
          flex items-center justify-center gap-3
          px-4 py-2.5
          text-white text-[13px] font-bold tracking-wide
          shadow-[0_2px_16px_rgba(5,150,105,0.5)]
        "
      >
        {/* Blinking pulse dot */}
        <span className="relative flex h-2.5 w-2.5 flex-shrink-0">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-white opacity-60" />
          <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-white" />
        </span>

        <span>
          🚨 REVERSAL SIGNAL:{" "}
          <span className="uppercase">{reversalType || "CANDLE"}</span>{" "}
          DETECTED — PROCEED WITH AGGRESSIVE LONG EXPOSURE
        </span>
      </div>
    );
  }

  // Stress mode
  return (
    <div
      role="alert"
      aria-live="polite"
      className="
        relative z-30 w-full
        bg-rose-700
        flex items-center justify-center gap-2
        px-4 py-2.5
        text-white text-[13px] font-semibold tracking-wide
        shadow-[0_2px_12px_rgba(190,18,60,0.45)]
      "
    >
      ⚠️ SYSTEMIC STRESS: CREDIT SPREADS &gt; 4.5% — PROTECT CAPITAL
    </div>
  );
}
