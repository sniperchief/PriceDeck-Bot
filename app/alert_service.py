"""
Alert Service for PriceDeck
Background job that checks price alerts and sends WhatsApp notifications.
"""

import logging
import asyncio
from typing import Dict, Any, Optional
from app import database

logger = logging.getLogger(__name__)

# Check interval in seconds (10 minutes)
CHECK_INTERVAL = 600


def format_price(price: float) -> str:
    """
    Format price for display.
    - Round thousands (1000, 5000, 10000) → "1k", "5k", "10k"
    - Non-round amounts (1200, 4500) → "1,200", "4,500"
    """
    if price >= 1000 and price % 1000 == 0:
        return f"{int(price // 1000)}k"
    else:
        return f"{int(price):,}"

# Reference to the send_whatsapp_message function (set from main.py)
_send_message_func = None


def set_message_sender(func):
    """Set the WhatsApp message sending function"""
    global _send_message_func
    _send_message_func = func


async def check_and_trigger_alerts():
    """
    Main function to check all active alerts against current prices.
    Called periodically by the background task.
    """
    try:
        # Get all active alerts
        alerts = database.get_active_alerts()

        if not alerts:
            logger.debug("No active alerts to check")
            return

        logger.info(f"Checking {len(alerts)} active alerts...")
        triggered_count = 0

        for alert in alerts:
            try:
                triggered = await check_single_alert(alert)
                if triggered:
                    triggered_count += 1
            except Exception as e:
                logger.error(f"Error checking alert {alert.get('id')}: {e}")
                continue

        if triggered_count > 0:
            logger.info(f"Triggered {triggered_count} alerts")

    except Exception as e:
        logger.error(f"Error in check_and_trigger_alerts: {e}", exc_info=True)


async def check_single_alert(alert: Dict[str, Any]) -> bool:
    """
    Check a single alert against current prices.

    Args:
        alert: Alert record from database

    Returns:
        True if alert was triggered, False otherwise
    """
    alert_id = alert.get("id")
    commodity = alert.get("commodity")
    market = alert.get("market")  # Can be None for all-market alerts
    threshold = alert.get("threshold_price")
    direction = alert.get("direction")
    user_phone = alert.get("whatsapp_number")

    if not all([commodity, threshold, direction, user_phone]):
        logger.warning(f"Alert {alert_id} missing required fields")
        return False

    # Get latest price for this commodity
    latest_price = database.get_latest_price_for_commodity(commodity, market)

    if not latest_price:
        # No price data available for this commodity
        return False

    current_price = latest_price.get("price")
    if current_price is None:
        return False

    # Check if threshold is crossed
    should_trigger = False

    if direction == "below" and current_price <= threshold:
        should_trigger = True
    elif direction == "above" and current_price >= threshold:
        should_trigger = True

    if should_trigger:
        logger.info(f"Alert {alert_id} triggered: {commodity} is {current_price} ({direction} {threshold})")

        # Send notification
        await send_alert_notification(
            user_phone=user_phone,
            commodity=commodity,
            current_price=current_price,
            unit=latest_price.get("unit", "unit"),
            market=latest_price.get("market", "market"),
            direction=direction,
            threshold=threshold
        )

        # Deactivate alert
        database.deactivate_alert(alert_id)

        return True

    return False


def clean_name(name: str) -> str:
    """Convert standardized name to display name"""
    if not name:
        return ""
    return name.replace('_', ' ').title()


async def send_alert_notification(
    user_phone: str,
    commodity: str,
    current_price: float,
    unit: str,
    market: str,
    direction: str,
    threshold: float
):
    """
    Send WhatsApp notification when alert is triggered.
    """
    global _send_message_func

    if not _send_message_func:
        logger.error("Message sender function not set!")
        return

    # Format display values
    commodity_display = clean_name(commodity)
    market_display = clean_name(market)
    unit_display = clean_name(unit)
    price_display = format_price(current_price)
    direction_symbol = "📉" if direction == "below" else "📈"

    message = f"🔔 {commodity_display} is now {price_display}/{unit_display} at {market_display} {direction_symbol}"

    try:
        await _send_message_func(user_phone, message)
        logger.info(f"Alert notification sent to {user_phone}")
    except Exception as e:
        logger.error(f"Failed to send alert notification to {user_phone}: {e}")


async def alert_checker_loop():
    """
    Background task that runs the alert checker periodically.
    """
    logger.info(f"Starting alert checker loop (interval: {CHECK_INTERVAL}s)")

    # Wait a bit before first check to let the app fully start
    await asyncio.sleep(10)

    while True:
        try:
            await check_and_trigger_alerts()
        except Exception as e:
            logger.error(f"Error in alert checker loop: {e}", exc_info=True)

        await asyncio.sleep(CHECK_INTERVAL)
