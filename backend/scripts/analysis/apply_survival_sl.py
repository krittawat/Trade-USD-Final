import MetaTrader5 as mt5

def add_survival_sl():
    if not mt5.initialize():
        print("MT5 Init Failed")
        return

    # Target the toxic USOILm position
    ticket = 1027555200
    positions = mt5.positions_get(ticket=ticket)
    
    if not positions:
        print(f"Position {ticket} not found.")
        mt5.shutdown()
        return

    pos = positions[0]
    # Survival SL: If price drops another $6.1 (to 86.0), exit to save remaining equity.
    survival_sl = 86.0
    
    if pos.sl == survival_sl:
        print("Survival SL already set.")
        mt5.shutdown()
        return

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl": survival_sl,
        "tp": pos.tp
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"Failed to set SL: {result.comment} ({result.retcode})")
    else:
        print(f"SUCCESS: Survival SL set at {survival_sl} for position {ticket}")

    mt5.shutdown()

if __name__ == "__main__":
    add_survival_sl()
