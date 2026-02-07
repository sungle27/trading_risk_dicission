from __future__ import annotations

from app.config import CFG
from app.market_regime import Regime
from app.decision_engine import Decision


def fmt_signal_message(
    symbol: str,
    mode: str,
    direction: str,
    price: float,
    score: int,
    high_conf: bool,
    regime: Regime,
    decision: Decision,
    meta: dict,
) -> str:
    tag = "🔥 HIGH CONF" if high_conf else ("🚨 MAIN" if mode == "main" else "🔔 EARLY")

    lines: list[str] = []
    lines.append(f"{tag} {direction} {symbol} @ {price:.4f}  (Score={score}/17)")
    lines.append(f"REGIME: {regime} | gate={decision.risk_mult:.2f}x")

    # Decision-support section (WHY)
    if CFG.ALERT_MODE_DECISION:
        lines.append("")
        lines.append("WHY:")
        gap = meta.get("ema_gap", 0.0) * 100
        vol = meta.get("volume_ratio", 0.0)
        spread = meta.get("spread", 0.0)
        lines.append(f"• ema_gap={gap:.2f}%")
        lines.append(f"• vol={vol:.2f}x")
        lines.append(f"• spread={spread:.4f} {'✅' if meta.get('spread_ok') else '❌'}")
        lines.append(f"• wick {'✅' if meta.get('wick_ok') else '❌'} | momentum {'✅' if meta.get('momentum_ok') else '❌'}")

        if mode == "main":
            lines.append(f"• ATR squeeze {'✅' if meta.get('atr_squeeze') else '❌'}")
            lines.append(f"• BreakHigh20 {'✅' if meta.get('breakout_highlow') else '❌'}")
            if meta.get("atr5_pct") is not None and meta.get("atr20_pct") is not None and meta.get("squeeze_ratio") is not None:
                lines.append(
                    f"  ATR5={meta['atr5_pct']*100:.2f}% | ATR20={meta['atr20_pct']*100:.2f}% | ratio={meta['squeeze_ratio']:.2f}"
                )

    # Execution-ready section (plan gợi ý)
    if CFG.ALERT_MODE_EXECUTION:
        lines.append("")
        lines.append("PLAN (gợi ý):")
        # stop gợi ý theo ATR20% nếu có, fallback theo gap
        stop_note = "Use structure-based stop"
        if meta.get("atr20_pct") is not None:
            atr20_pct = float(meta["atr20_pct"])
            stop_dist = price * (1.2 * atr20_pct)
            if direction == "LONG":
                stop = price - stop_dist
            else:
                stop = price + stop_dist
            lines.append(f"• Stop (ATR-based): {stop:.4f} (~1.2*ATR20)")
            stop_note = "ATR-based"
        lines.append(f"• Risk: 0.25%–1.0% NAV × gate ({decision.risk_mult:.2f}x)")
        lines.append(f"• Note: {stop_note} | tránh vào khi spread/wick xấu")

    return "\n".join(lines)


def fmt_regime_message(regime: Regime, reason: str) -> str:
    if regime == Regime.PANIC:
        return f"⛔ PANIC MODE ON\nreason: {reason}\nAction: BLOCK ALL new signals"
    if regime == Regime.RECOVERY:
        return f"⚠️ RECOVERY MODE\nreason: {reason}\nAction: block EARLY, MAIN selective (high_conf)"
    if regime == Regime.RANGE:
        return f"🟨 RANGE MODE\nreason: {reason}\nAction: block EARLY, MAIN selective"
    if regime == Regime.TREND:
        return f"🟩 TREND MODE\nreason: {reason}\nAction: MAIN prioritized"
    return f"📌 REGIME → {regime}\nreason: {reason}"
