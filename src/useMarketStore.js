import { create } from "zustand";

/**
 * useMarketStore — Global macro alert + reversal state
 *
 * Populated by the 60-second polling loop in App.js.
 * Reads market_intelligence.json (market_briefing_engine.py schema)
 * with fallback to the old market_intelligence.py schema.
 *
 * creditRegime values use the canonical labels from BAMLH0A0HYM2:
 *   'Complacent' | 'Yellow Flag' | 'Stress'
 */
const useMarketStore = create((set) => ({
  // ── State ──────────────────────────────────────────────────────────────────
  creditRegime:       "Complacent",  // 'Complacent' | 'Yellow Flag' | 'Stress'
  isReversalDetected: false,
  reversalType:       null,          // 'Hammer' | 'Engulfing' | 'Undercut' | null
  marketMood:         "Neutral",     // 'Fear' | 'Greed' | 'Neutral'
  activeBrief:        "",            // Latest brief as a Markdown string

  // ── Action ─────────────────────────────────────────────────────────────────
  /**
   * updateFromIntel(data)
   * Accepts the JSON from market_intelligence.json and updates all state.
   * Handles both schemas:
   *   NEW (market_briefing_engine.py):  data.regime.credit_status, data.assets, data.fred
   *   OLD (market_intelligence.py):    data.credit.regime, data.indices, data.reversal_signals
   */
  updateFromIntel: (data) =>
    set(() => {
      if (!data || typeof data !== "object") return {};

      const isNewSchema = !!(data.regime || data.fred);

      // ── Credit regime ──────────────────────────────────────────────────────
      let creditRegime;
      if (isNewSchema) {
        creditRegime = data.regime?.credit_status || "Complacent";
      } else {
        creditRegime = data.credit?.regime || "Complacent";
      }

      // ── Reversal ───────────────────────────────────────────────────────────
      let isReversalDetected, reversalDesc;
      if (isNewSchema) {
        isReversalDetected = data.regime?.reversal?.signal_detected || false;
        reversalDesc       = data.regime?.reversal?.signal_description || "";
      } else {
        isReversalDetected = data.reversal_signals?.signal_detected || false;
        reversalDesc       = data.reversal_signals?.signal_description || "";
      }

      // Parse the reversal type from the description string
      let reversalType = null;
      if (isReversalDetected) {
        if (/undercut/i.test(reversalDesc))   reversalType = "Undercut";
        else if (/engulfing/i.test(reversalDesc)) reversalType = "Engulfing";
        else if (/hammer/i.test(reversalDesc))    reversalType = "Hammer";
        else                                       reversalType = "Hammer";
      }

      // ── Market mood (VIX-based) ────────────────────────────────────────────
      const vixPrice = isNewSchema
        ? (data.assets?.["^VIX"]?.price ?? data.assets?.vix?.price)
        : (data.indices?.vix?.price);
      let marketMood = "Neutral";
      if (vixPrice != null) {
        if (vixPrice >= 30)      marketMood = "Fear";
        else if (vixPrice < 18)  marketMood = "Greed";
      }

      // ── Active brief (Markdown) ────────────────────────────────────────────
      const ana = data.analysis || {};
      const briefParts = [
        ana.title          ? `# ${ana.title}` : "",
        ana.mood           ? `**Mood:** ${ana.mood_emoji || ""} ${ana.mood}` : "",
        ana.narrative      || "",
        ana.macro_section  ? `## Macro\n${ana.macro_section}` : "",
        ana.analysis_para2 ? `## Mechanical Catalyst\n${ana.analysis_para2}` : "",
        ana.technical_signal ? `## Technical Signal\n${ana.technical_signal}` : "",
      ].filter(Boolean);
      const activeBrief = briefParts.join("\n\n");

      return { creditRegime, isReversalDetected, reversalType, marketMood, activeBrief };
    }),
}));

export default useMarketStore;
