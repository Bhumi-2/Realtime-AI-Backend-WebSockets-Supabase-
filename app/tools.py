from typing import Dict, Any
import random
import time

async def fetch_account_balance(user_id: str) -> Dict[str, Any]:
    await _tiny_delay()
    bal = round(random.uniform(120.0, 9340.0), 2)
    return {"user_id": user_id, "currency": "USD", "balance": bal}

async def fetch_order_status(order_id: str) -> Dict[str, Any]:
    await _tiny_delay()
    status = random.choice(["PROCESSING", "SHIPPED", "DELIVERED", "ON_HOLD"])
    return {"order_id": order_id, "status": status, "eta_days": random.randint(1, 7)}

async def _tiny_delay() -> None:
    time.sleep(0.05)
