"""
Claude API integration for PriceDeck WhatsApp bot.
Handles natural language understanding for commodity price intelligence.
"""

import logging
import asyncio
import json
import re
from typing import Optional, Dict, Any, List
from collections import defaultdict
from pydantic import BaseModel
from anthropic import AsyncAnthropic
from app.config import ANTHROPIC_API_KEY
from app import database

logger = logging.getLogger(__name__)

# =====================================================
# CONVERSATION MEMORY
# =====================================================

# Store conversation history per user (last 10 messages)
conversation_history: Dict[str, List[Dict[str, str]]] = defaultdict(list)
MAX_HISTORY = 10  # Keep last 10 message pairs

# Store pending price reports awaiting confirmation
pending_price_reports: Dict[str, Dict[str, Any]] = {}

# Store partial price reports awaiting market selection
partial_price_reports: Dict[str, Dict[str, Any]] = {}

# Store partial alerts awaiting completion
partial_alerts: Dict[str, Dict[str, Any]] = {}

# Store user action context (check_price, report_price, set_alert)
user_action_context: Dict[str, str] = {}

# Store partial cart/checkout data
partial_cart: Dict[str, Dict[str, Any]] = {}


# =====================================================
# PYDANTIC MODELS
# =====================================================

class PriceReportData(BaseModel):
    """Extracted data from a price report message"""
    commodity: str
    commodity_raw: str
    price: float
    unit: str
    unit_raw: str
    market: str
    market_raw: str


class PriceQueryData(BaseModel):
    """Extracted data from a price query message"""
    commodity: Optional[str] = None
    market: Optional[str] = None
    query_type: str


class AlertData(BaseModel):
    """Extracted data from an alert setup message"""
    commodity: str
    threshold_price: float
    direction: str
    unit: Optional[str] = None
    market: Optional[str] = None


# =====================================================
# HELPER FUNCTIONS
# =====================================================


def clean_name(name: str) -> str:
    """Convert standardized name to display name (remove underscores, title case)"""
    if not name:
        return ""
    return name.replace('_', ' ').title()


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


def _get_help_message() -> str:
    """Return standard help message"""
    return (
        "📍 *Report*: \"Garri 45k bag Ogbete\"\n"
        "🔍 *Check*: \"How much is rice?\"\n"
        "🔔 *Alert*: \"Tell me when rice drops below 50k\""
    )


# =====================================================
# CONVERSATIONAL SYSTEM PROMPT
# =====================================================

SYSTEM_PROMPT = """You are PriceDeck, a WhatsApp bot for commodity prices in Enugu markets.

YOUR JOB: Extract data from user messages and return JSON. System handles responses and missing fields.

MARKETS: {markets}
COMMODITIES: garri, rice, beans, tomatoes, pepper, palm oil, yam, plantain, beef, chicken, fish, eggs, onions, crayfish, cement
UNITS: paint, half_paint, cup, bag, half_bag, mudu, kg, piece, basket, congo

COMMODITY VARIETIES (use these exact names when user specifies type):
- Garri: garri_white, garri_yellow, garri_ijebu
- Rice: rice_local, rice_foreign, rice_ofada
- Beans: beans_oloyin, beans_brown, beans_iron

DETECT THESE INTENTS:

1. PRICE REPORT - User sharing or reporting a price
IMPORTANT: Return JSON even if price is missing. Return null for any field NOT explicitly mentioned.

Examples:
- "I bought garri" or "I bought rice today" → commodity only, no price
```json
{{"action": "save_price", "commodity": "garri", "price": null, "unit": null, "market": null}}
```
- "rice 6k" → commodity and price, no variety
```json
{{"action": "save_price", "commodity": "rice", "price": 6000, "unit": null, "market": null}}
```
- "yellow garri 20k paint" → variety specified
```json
{{"action": "save_price", "commodity": "garri_yellow", "price": 20000, "unit": "paint", "market": null}}
```
- "local rice 45k bag ogbete" → all fields present
```json
{{"action": "save_price", "commodity": "rice_local", "price": 45000, "unit": "bag", "market": "ogbete_main"}}
```
- "oloyin beans 35k" → variety specified (oloyin/honey beans)
```json
{{"action": "save_price", "commodity": "beans_oloyin", "price": 35000, "unit": null, "market": null}}
```

2. PRICE QUERY - User asking for a price
Examples: "how much is rice?", "wetin be beans price?", "price of yellow garri"
```json
{{"action": "query_price", "commodity": "rice", "market": null}}
```

3. ALERT - User wants notification when price changes
Examples: "tell me when rice drops below 40k", "alert me if beans go above 50000"
```json
{{"action": "set_alert", "commodity": "rice", "threshold_price": 40000, "direction": "below"}}
```

4. GREETING/CHAT - Hello, hi, help, thanks, unclear messages
→ Reply briefly (1-2 lines), NO JSON
→ For greetings: Welcome them, ask which market they visited today

RULES:
- Convert k to thousands: 35k = 35000
- If user specifies variety (yellow garri, local rice, oloyin beans), use the full name (garri_yellow, rice_local, beans_oloyin)
- If user doesn't specify variety for garri/rice/beans, just use base name (garri, rice, beans) - system will ask
- Standardize market names: Ogbete → ogbete_main
- If user mentions buying/purchasing a commodity (e.g., "I bought garri", "I got rice"), return JSON with price: null
- For price reports: ALWAYS return JSON even if fields missing - use null for missing fields
- NEVER guess price, unit, or market - if user didn't say it, return null
- NEVER add extra text with JSON - return JSON alone"""


# =====================================================
# MAIN ENTRY POINT
# =====================================================

async def process_message(
    message_text: str,
    user_phone: str,
    available_markets: List[Dict[str, Any]],
    user_name: str = None
) -> str:
    """
    Process incoming WhatsApp message using Claude for NLU.

    Args:
        message_text: The raw message from WhatsApp
        user_phone: Sender's WhatsApp number
        available_markets: List of active markets from cache/DB
        user_name: User's WhatsApp profile name (optional)

    Returns:
        Response message to send back via WhatsApp
    """
    global conversation_history, pending_price_reports, partial_price_reports, partial_alerts, user_action_context

    try:
        # Handle empty messages
        if not message_text or not message_text.strip():
            return _get_help_message()

        # IMPORTANT: Check cart/checkout flow FIRST (before menu triggers)
        # This prevents addresses like "Chime Avenue" from triggering menu (contains "hi")
        if user_phone in partial_cart:
            partial = partial_cart[user_phone]
            awaiting = partial.get("awaiting")

            if awaiting == "quantity":
                return await handle_cart_quantity_input(user_phone, message_text)
            elif awaiting == "new_quantity":
                return await handle_quantity_change_input(user_phone, message_text)
            elif awaiting == "delivery_address":
                return await handle_checkout_address_input(user_phone, message_text)
            elif awaiting == "contact_phone":
                return await handle_checkout_phone_input(user_phone, message_text)

        # Check if this is a menu trigger (greetings, help, etc.)
        if is_menu_trigger(message_text):
            # Clear any stale partial data
            if user_phone in partial_price_reports:
                del partial_price_reports[user_phone]
            if user_phone in pending_price_reports:
                del pending_price_reports[user_phone]
            if user_phone in partial_alerts:
                del partial_alerts[user_phone]
            if user_phone in user_action_context:
                del user_action_context[user_phone]
            if user_phone in partial_cart:
                del partial_cart[user_phone]
            # Return marker for main menu
            return "__MAIN_MENU__"

        # Check if user is confirming a pending price report
        if user_phone in pending_price_reports:
            return await handle_price_confirmation(message_text, user_phone)

        # Check if user is setting an alert (awaiting threshold)
        if user_phone in partial_alerts:
            partial = partial_alerts[user_phone]
            if partial.get("awaiting") == "threshold":
                return await handle_alert_threshold_input(user_phone, message_text)

        # Check if user is providing custom input (price, unit, or market)
        if user_phone in partial_price_reports:
            result = await handle_custom_input(message_text, user_phone)
            # If result is a menu marker, return it
            if result == "__MAIN_MENU__":
                return result
            # Otherwise return the result (could be next step or message)
            return result

        # Build markets list for prompt
        if available_markets:
            markets_str = ", ".join([m.get('slug', '') for m in available_markets])
        else:
            markets_str = "ogbete_main, new_haven, abakpa, artisan, gariki"

        # Build system prompt
        system = SYSTEM_PROMPT.format(markets=markets_str)

        # Add user context to system prompt if available
        if user_name:
            system += f"\n\nCurrent user's name: {user_name}"

        # Get conversation history for this user
        history = conversation_history[user_phone].copy()

        # Add current message to history
        history.append({"role": "user", "content": message_text})

        # Create Claude client
        client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

        # Call Claude with conversation history
        try:
            response = await asyncio.wait_for(
                client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=1024,
                    system=system,
                    messages=history
                ),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            logger.error("Claude API timeout")
            return "I'm taking longer than expected. Please try again."
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return "I'm having trouble right now. Please try again in a moment."

        # Extract response text
        response_text = response.content[0].text
        logger.info(f"Claude response: {response_text}")

        # Update conversation history
        conversation_history[user_phone].append({"role": "user", "content": message_text})
        conversation_history[user_phone].append({"role": "assistant", "content": response_text})

        # Trim history to max length
        if len(conversation_history[user_phone]) > MAX_HISTORY * 2:
            conversation_history[user_phone] = conversation_history[user_phone][-MAX_HISTORY * 2:]

        # Check for action JSON in response (with or without code block markers)
        json_match = re.search(r'```json\s*(\{[\s\S]*?\})\s*```', response_text)

        # Also try to match raw JSON without code blocks
        if not json_match:
            json_match = re.search(r'(\{\s*"action"\s*:\s*"[^"]+"\s*,[\s\S]*?\})', response_text)

        if json_match:
            try:
                action_data = json.loads(json_match.group(1))
                action = action_data.get("action")
                logger.info(f"Detected action: {action}, data: {action_data}")

                # Execute action - handlers generate all response text
                if action == "save_price":
                    # Check for missing required fields
                    commodity = action_data.get("commodity")
                    price = action_data.get("price")
                    unit = action_data.get("unit")
                    market = action_data.get("market")

                    if not commodity:
                        return "What commodity is this price for?"
                    if not price:
                        # Store partial data and ask for price
                        partial_price_reports[user_phone] = {
                            "commodity": commodity,
                            "unit": unit,  # May be None
                            "market": market,  # May be None
                            "awaiting": "price"
                        }
                        return "What's the price?"

                    # Commodities that need variety selection
                    variety_commodities = ["garri", "rice", "beans"]

                    # Check if commodity needs variety and doesn't already have one
                    # e.g., "garri" needs variety, but "garri_yellow" already has it
                    base_commodity = commodity.split("_")[0] if "_" in commodity else commodity
                    has_variety = "_" in commodity and base_commodity in variety_commodities
                    needs_variety = base_commodity in variety_commodities and not has_variety

                    if needs_variety:
                        # Store partial data and trigger variety buttons
                        partial_price_reports[user_phone] = {
                            "commodity": commodity,
                            "price": float(price),
                            "unit": unit,  # May be None
                            "market": market,  # May be None
                            "awaiting": "variety"
                        }
                        return f"__SELECT_VARIETY__:{commodity}"

                    # Check unit
                    if not unit:
                        # Store partial data and trigger unit list
                        partial_price_reports[user_phone] = {
                            "commodity": commodity,
                            "price": float(price),
                            "awaiting": "unit"
                        }
                        return "__SELECT_UNIT__"

                    # Check market
                    if not market:
                        # Store partial data and trigger market list
                        partial_price_reports[user_phone] = {
                            "commodity": commodity,
                            "price": float(price),
                            "unit": unit,
                            "awaiting": "market"
                        }
                        return "__SELECT_MARKET__"

                    data = PriceReportData(
                        commodity=commodity,
                        commodity_raw=commodity,
                        price=float(price),
                        unit=unit,
                        unit_raw=unit,
                        market=market,
                        market_raw=market
                    )
                    return await handle_report_price(data, user_phone)

                elif action == "query_price":
                    commodity = action_data.get("commodity")
                    market = action_data.get("market")

                    if commodity and market:
                        data = PriceQueryData(commodity=commodity, market=market, query_type="both")
                    elif commodity:
                        data = PriceQueryData(commodity=commodity, query_type="by_commodity")
                    else:
                        return "What commodity would you like to check?"

                    return await handle_query_prices(data)

                elif action == "set_alert":
                    data = AlertData(
                        commodity=action_data.get("commodity"),
                        threshold_price=float(action_data.get("threshold_price", 0)),
                        direction=action_data.get("direction", "below"),
                        market=action_data.get("market")
                    )
                    return await handle_set_alert(data, user_phone)

            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.warning(f"Failed to parse action JSON: {e}")
                # Remove JSON and return any remaining text, or fallback message
                clean_response = re.sub(r'```json\s*\{[\s\S]*?\}\s*```', '', response_text).strip()
                clean_response = re.sub(r'\{\s*"action"\s*:\s*"[^"]+"\s*,[\s\S]*?\}', '', clean_response).strip()
                return clean_response if clean_response else "I didn't catch that. Try again?"

        # No action JSON - just return conversational response
        return response_text

    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)
        return "I'm having trouble right now. Please try again in a moment."


# =====================================================
# VARIETY SELECTION HANDLER
# =====================================================

async def handle_variety_selection(user_phone: str, variety_id: str) -> str:
    """
    Handle when user selects a variety from the buttons.
    For check_price: returns prices for this variety.
    For report_price: continues to unit selection.
    """
    global partial_price_reports, user_action_context

    action = user_action_context.get(user_phone, "report_price")

    # CHECK PRICE FLOW - get and return prices
    if action == "check_price":
        commodity_display = clean_name(variety_id)
        prices = database.get_prices_by_commodity_all_markets(variety_id)

        if not prices:
            return f"No {commodity_display} prices yet. Be the first to share!\n\n__AFTER_ACTION__"

        # Format prices for display
        response = f"*{commodity_display}* prices in Enugu:\n\n"
        ogbete_price = None
        ogbete_unit = None

        for market_data in prices[:5]:  # Show top 5 markets
            market_name = clean_name(market_data.get("market", ""))
            min_price = format_price(market_data.get("min_price", 0))
            unit_name = clean_name(market_data.get("unit", "unit"))
            response += f"📍 {market_name}: {min_price}/{unit_name}\n"

            # Track Ogbete price for cart option
            if market_data.get("market") == "ogbete":
                ogbete_price = market_data.get("min_price")
                ogbete_unit = market_data.get("unit")

        # Clear action context
        if user_phone in user_action_context:
            del user_action_context[user_phone]

        # If Ogbete has price, include marker for add-to-cart button
        if ogbete_price:
            return f"{response}\n__ADD_TO_CART__:{variety_id}:{ogbete_price}:{ogbete_unit}"
        else:
            return response + "\n__AFTER_ACTION__"

    # REPORT PRICE FLOW - continue to unit selection
    partial = partial_price_reports.get(user_phone)
    if not partial:
        # Create new partial with variety
        partial_price_reports[user_phone] = {
            "commodity": variety_id,
            "awaiting": "unit"
        }
        return "__SELECT_UNIT__"

    # Update commodity with variety (e.g., "garri" -> "garri_yellow")
    partial_price_reports[user_phone]["commodity"] = variety_id

    # Check what's next - unit first (before price)
    if not partial.get("unit"):
        partial_price_reports[user_phone]["awaiting"] = "unit"
        return "__SELECT_UNIT__"
    elif not partial.get("price"):
        partial_price_reports[user_phone]["awaiting"] = "price"
        unit_display = clean_name(partial.get("unit", "unit"))
        return f"What's the price per {unit_display.lower()}?"
    elif not partial.get("market"):
        partial_price_reports[user_phone]["awaiting"] = "market"
        return "__SELECT_MARKET__"
    else:
        # All data present, proceed to confirmation
        data = PriceReportData(
            commodity=variety_id,
            commodity_raw=variety_id,
            price=partial["price"],
            unit=partial["unit"],
            unit_raw=partial["unit"],
            market=partial["market"],
            market_raw=partial["market"]
        )
        del partial_price_reports[user_phone]
        return await handle_report_price(data, user_phone)


# =====================================================
# UNIT SELECTION HANDLER
# =====================================================

async def handle_unit_selection(user_phone: str, unit_id: str) -> str:
    """
    Handle when user selects a unit from the interactive list.
    Updates partial data and asks for price (with unit in prompt).
    """
    global partial_price_reports

    partial = partial_price_reports.get(user_phone)
    if not partial:
        return "__MAIN_MENU__"

    # Update partial data with unit
    partial_price_reports[user_phone]["unit"] = unit_id
    partial_price_reports[user_phone]["awaiting"] = "price"

    # Format unit for display
    unit_display = unit_id.replace("_", " ").lower()

    # Return prompt with unit included
    return f"What's the price per {unit_display}?"


# =====================================================
# MARKET SELECTION HANDLER
# =====================================================

async def handle_market_selection(user_phone: str, market_slug: str) -> str:
    """
    Handle when user selects a market from the interactive list.
    Combines with stored partial price data.
    """
    global partial_price_reports

    partial = partial_price_reports.get(user_phone)
    if not partial:
        return "No pending price. Share a new price?"

    # Create full price report data
    data = PriceReportData(
        commodity=partial["commodity"],
        commodity_raw=partial["commodity"],
        price=partial["price"],
        unit=partial["unit"],
        unit_raw=partial["unit"],
        market=market_slug,
        market_raw=market_slug
    )

    # Clear partial data
    del partial_price_reports[user_phone]

    # Proceed to confirmation
    return await handle_report_price(data, user_phone)


# =====================================================
# ALERT SELECTION HANDLERS
# =====================================================

async def handle_alert_variety_selection(user_phone: str, variety_id: str) -> str:
    """
    Handle variety selection for alert flow.
    Stores variety and triggers unit selection.
    """
    global partial_alerts

    if user_phone not in partial_alerts:
        partial_alerts[user_phone] = {}

    partial_alerts[user_phone]["commodity"] = variety_id
    partial_alerts[user_phone]["awaiting"] = "unit"

    return "__SELECT_UNIT__"


async def handle_alert_unit_selection(user_phone: str, unit_id: str) -> str:
    """
    Handle unit selection for alert flow.
    Stores unit, fetches current prices, and triggers direction selection.
    """
    global partial_alerts

    if user_phone not in partial_alerts:
        return "__MAIN_MENU__"

    partial = partial_alerts[user_phone]
    partial["unit"] = unit_id
    partial["awaiting"] = "direction"

    commodity = partial.get("commodity", "")
    commodity_display = clean_name(commodity)
    unit_display = clean_name(unit_id)

    # Get current prices to show user
    prices = database.get_prices_by_commodity_all_markets(commodity)

    if not prices:
        prices_text = f"*{commodity_display}* ({unit_display})\n\nNo prices yet - you'll be alerted when prices are reported."
    else:
        # Format prices
        lines = [f"*{commodity_display}* current prices:"]
        for p in prices[:4]:
            market_name = clean_name(p['market'])
            price_display = format_price(p['min_price'])
            p_unit = clean_name(p.get('unit', 'unit'))
            lines.append(f"📍 {market_name}: {price_display}/{p_unit}")
        prices_text = "\n".join(lines)

    # Return marker with prices text for direction buttons
    return f"__DIRECTION__:{prices_text}"


async def handle_alert_direction_selection(user_phone: str, direction: str) -> str:
    """
    Handle direction selection (below/above) for alert flow.
    Stores direction and asks for threshold price.
    """
    global partial_alerts

    if user_phone not in partial_alerts:
        return "__MAIN_MENU__"

    partial_alerts[user_phone]["direction"] = direction
    partial_alerts[user_phone]["awaiting"] = "threshold"

    unit = partial_alerts[user_phone].get("unit", "unit")
    unit_display = clean_name(unit).lower()

    return f"What price per {unit_display}? (e.g., 5000 or 5k)"


async def handle_alert_threshold_input(user_phone: str, message_text: str) -> str:
    """
    Handle threshold price input for alert flow.
    Parses price, saves alert, and returns confirmation.
    """
    global partial_alerts, user_action_context

    if user_phone not in partial_alerts:
        return "__MAIN_MENU__"

    partial = partial_alerts[user_phone]

    # Parse the threshold price
    threshold = parse_price(message_text)
    if threshold <= 0:
        unit = partial.get("unit", "unit")
        unit_display = clean_name(unit).lower()
        return f"Please enter a valid price per {unit_display} (e.g., 5000 or 5k)"

    # Build alert data
    commodity = partial.get("commodity", "")
    unit = partial.get("unit", "")
    direction = partial.get("direction", "below")

    # Save alert
    try:
        database.get_or_create_user(user_phone)

        alert_data = {
            "whatsapp_number": user_phone,
            "commodity": commodity,
            "unit": unit,
            "threshold_price": threshold,
            "direction": direction
        }
        database.save_alert(alert_data)

        # Clear state
        del partial_alerts[user_phone]
        if user_phone in user_action_context:
            del user_action_context[user_phone]

        # Format confirmation
        commodity_display = clean_name(commodity)
        price_display = format_price(threshold)
        unit_display = clean_name(unit)
        direction_symbol = "📉" if direction == "below" else "📈"

        return f"🔔 Alert set! {commodity_display} {direction_symbol} {price_display}/{unit_display}\n\n__AFTER_ACTION__"

    except Exception as e:
        logger.error(f"Error saving alert: {e}")
        return "Couldn't set alert. Try again?\n\n__AFTER_ACTION__"


def parse_price(text: str) -> float:
    """Parse price from text like '6k', '6000', '6,000', etc."""
    text = text.lower().strip().replace(",", "").replace(" ", "")

    # Handle 'k' suffix (e.g., "6k" = 6000)
    if text.endswith("k"):
        try:
            return float(text[:-1]) * 1000
        except ValueError:
            return 0

    # Try to extract number
    try:
        return float(text)
    except ValueError:
        # Try to find a number in the text
        import re
        numbers = re.findall(r'[\d.]+', text)
        if numbers:
            return float(numbers[0])
        return 0


def is_menu_trigger(text: str) -> bool:
    """Check if message should trigger main menu (greetings, menu, help, etc.)"""
    text_lower = text.lower().strip()

    # Patterns that should trigger main menu
    menu_triggers = [
        'hello', 'hi', 'hey', 'good morning', 'good afternoon', 'good evening',
        'how far', 'how are you', 'what\'s up', 'sup', 'howdy',
        'help', 'menu', 'start', 'home', 'info', 'about',
        'thanks', 'thank you', 'bye', 'goodbye', 'later',
        'wetin dey', 'how you dey', 'e don tay', 'na wa'
    ]

    for pattern in menu_triggers:
        if pattern in text_lower:
            return True

    return False


async def handle_custom_input(message_text: str, user_phone: str) -> str:
    """
    Handle when user types custom input (price, unit, or market).
    Returns "__REPROCESS__" if message is unrelated and should go to Claude.
    """
    global partial_price_reports

    partial = partial_price_reports.get(user_phone)
    if not partial:
        return "No pending price. Share a new price?"

    awaiting = partial.get("awaiting", "market")
    input_text = message_text.strip()

    # Check if this looks like a menu trigger (greeting, help, etc.)
    if is_menu_trigger(input_text):
        # Clear stale data and show main menu
        del partial_price_reports[user_phone]
        return "__MAIN_MENU__"

    if awaiting == "price":
        # User typed a price - parse and go to market selection
        price = parse_price(input_text)
        if price <= 0:
            # Doesn't look like a price - ask again with unit context
            unit_display = partial.get("unit", "unit").replace("_", " ").lower()
            return f"Please enter a valid price per {unit_display} (e.g., 5000 or 5k)"

        # Store price and go to market selection
        partial_price_reports[user_phone]["price"] = price
        partial_price_reports[user_phone]["awaiting"] = "market"
        return "__SELECT_MARKET__"

    elif awaiting == "variety":
        # User should have tapped a button, not typed text
        # Clear and show main menu
        del partial_price_reports[user_phone]
        return "__MAIN_MENU__"

    elif awaiting == "unit":
        # User typed a custom unit - store and ask for price
        custom_unit = input_text.lower().replace(" ", "_")
        partial_price_reports[user_phone]["unit"] = custom_unit
        partial_price_reports[user_phone]["awaiting"] = "price"
        unit_display = input_text.lower()
        return f"What's the price per {unit_display}?"

    elif awaiting == "market":
        # Validate required fields exist before creating PriceReportData
        if not partial.get("unit") or not partial.get("price"):
            # Missing required data - clear and show menu
            del partial_price_reports[user_phone]
            return "__MAIN_MENU__"

        # User typed a custom market - complete the price report
        data = PriceReportData(
            commodity=partial["commodity"],
            commodity_raw=partial["commodity"],
            price=partial["price"],
            unit=partial["unit"],
            unit_raw=partial["unit"],
            market=input_text.lower().replace(" ", "_"),
            market_raw=input_text
        )

        # Clear partial data
        del partial_price_reports[user_phone]

        # Proceed to confirmation
        return await handle_report_price(data, user_phone)

    else:
        # Unknown awaiting state - clear and reprocess
        del partial_price_reports[user_phone]
        return "__REPROCESS__"


# Keep old name for backwards compatibility
async def handle_custom_market_input(message_text: str, user_phone: str) -> str:
    return await handle_custom_input(message_text, user_phone)


# =====================================================
# CONFIRMATION HANDLER
# =====================================================

async def handle_price_confirmation(message_text: str, user_phone: str) -> str:
    """Handle user confirmation of pending price report"""
    global pending_price_reports

    message_lower = message_text.lower().strip()
    pending = pending_price_reports.get(user_phone)

    if not pending:
        return "No pending report. Share a price?"

    # Check for confirmation
    yes_responses = ['yes', 'y', 'yeah', 'yep', 'sure', 'correct', 'confirm', '👍', 'ok', 'okay']
    no_responses = ['no', 'n', 'nope', 'wrong', 'cancel', '👎', 'nah']

    if any(r in message_lower for r in yes_responses):
        # Save the price report
        data = pending["data"]
        result = await save_confirmed_price(data, user_phone)
        del pending_price_reports[user_phone]
        return result

    elif any(r in message_lower for r in no_responses):
        # Cancel and clear
        del pending_price_reports[user_phone]
        return "Cancelled. Send the correct price?"

    else:
        # Unclear response - ask again
        data = pending["data"]
        price_display = format_price(data.price)
        commodity_name = clean_name(data.commodity_raw) or clean_name(data.commodity)
        market_name = clean_name(data.market_raw) or clean_name(data.market)
        unit_name = clean_name(data.unit)
        return f"{commodity_name} {price_display}/{unit_name} at {market_name} - correct? (yes/no)"


async def save_confirmed_price(data: PriceReportData, user_phone: str) -> str:
    """Save price report after confirmation"""
    try:
        # Get or create user
        database.get_or_create_user(user_phone)

        # Check if market exists
        market = database.find_market_by_name(data.market_raw)
        market_slug = market["slug"] if market else data.market

        # If market doesn't exist, create unverified
        if not market:
            try:
                database.create_unverified_market(data.market_raw, user_phone)
            except:
                pass

        # Save price report
        report_data = {
            "commodity": data.commodity,
            "commodity_raw": data.commodity_raw,
            "price": data.price,
            "unit": data.unit,
            "unit_raw": data.unit_raw,
            "market": market_slug,
            "city": "enugu",
            "reported_by": user_phone
        }
        database.save_price_report(report_data)
        database.increment_contribution_count(user_phone)

        # Format response with details
        commodity_display = clean_name(data.commodity)
        price_display = format_price(data.price)
        unit_display = clean_name(data.unit)
        market_display = clean_name(market_slug)

        return f"✅ Thanks! {commodity_display} at {price_display}/{unit_display} in {market_display} recorded.\n\n__AFTER_ACTION__"

    except Exception as e:
        logger.error(f"Error saving confirmed price: {e}")
        return "Couldn't save. Try again?"


# =====================================================
# INTENT HANDLERS
# =====================================================

async def handle_report_price(data: PriceReportData, user_phone: str) -> str:
    """Handle price report - store as pending and ask for confirmation"""
    global pending_price_reports

    try:
        # Store as pending
        pending_price_reports[user_phone] = {
            "data": data,
            "timestamp": asyncio.get_event_loop().time()
        }

        # Format confirmation message
        price_display = format_price(data.price)
        commodity_name = clean_name(data.commodity_raw) or clean_name(data.commodity)
        market_name = clean_name(data.market_raw) or clean_name(data.market)
        unit_name = clean_name(data.unit)

        return f"{commodity_name} {price_display}/{unit_name} at {market_name} - correct? (yes/no)"

    except Exception as e:
        logger.error(f"Error handling price report: {e}", exc_info=True)
        return "Something went wrong. Try again?"


async def handle_query_prices(data: PriceQueryData) -> str:
    """Handle price query"""
    try:
        if data.query_type == "by_commodity" and data.commodity:
            prices = database.get_prices_by_commodity_all_markets(data.commodity)
            commodity_display = clean_name(data.commodity)

            if not prices:
                return f"No {commodity_display} prices yet. Know the price? Share it!\n\n__AFTER_ACTION__"

            # Format response - brief
            lines = [f"*{commodity_display}*:"]
            ogbete_price = None
            ogbete_unit = None

            for p in prices[:4]:  # Top 4 markets
                market_name = clean_name(p['market'])
                unit_name = clean_name(p.get('unit', 'unit'))
                price_display = format_price(p['min_price'])
                lines.append(f"• {market_name}: {price_display}/{unit_name}")

                # Track Ogbete price for cart option
                if p.get('market') == 'ogbete':
                    ogbete_price = p['min_price']
                    ogbete_unit = p.get('unit', 'unit')

            response = "\n".join(lines)

            # If Ogbete has price, include marker for add-to-cart button
            if ogbete_price:
                return f"{response}\n\n__ADD_TO_CART__:{data.commodity}:{ogbete_price}:{ogbete_unit}"
            else:
                return response + "\n\n__AFTER_ACTION__"

        elif data.query_type == "by_market" and data.market:
            market = database.find_market_by_name(data.market)
            if not market:
                return "Which commodity?"
            return f"What commodity at {market.get('display_name')}?"

        elif data.query_type == "both" and data.commodity and data.market:
            prices = database.get_prices_by_commodity_single_market(data.commodity, data.market)
            commodity_display = clean_name(data.commodity)
            market_display = clean_name(data.market)

            if not prices:
                return f"No {commodity_display} prices at {market_display} yet.\n\n__AFTER_ACTION__"

            latest = prices[0]
            price_display = format_price(latest['price'])
            unit_name = clean_name(latest.get('unit', 'unit'))
            return f"*{commodity_display}* at {market_display}: {price_display}/{unit_name}\n\n__AFTER_ACTION__"

        return "What price do you want to check?"

    except Exception as e:
        logger.error(f"Error handling price query: {e}", exc_info=True)
        return "Couldn't fetch prices. Try again?"


async def handle_set_alert(data: AlertData, user_phone: str) -> str:
    """Handle alert creation (text-based flow through Claude)"""
    try:
        # Ensure user exists
        database.get_or_create_user(user_phone)

        alert_data = {
            "whatsapp_number": user_phone,
            "commodity": data.commodity,
            "threshold_price": data.threshold_price,
            "direction": data.direction,
            "market": data.market
        }
        # Add unit if available
        if data.unit:
            alert_data["unit"] = data.unit

        database.save_alert(alert_data)

        commodity_display = clean_name(data.commodity)
        price_display = format_price(data.threshold_price)
        direction_symbol = "📉" if data.direction == "below" else "📈"

        if data.unit:
            unit_display = clean_name(data.unit)
            return f"🔔 Alert set! {commodity_display} {direction_symbol} {price_display}/{unit_display}\n\n__AFTER_ACTION__"
        else:
            return f"🔔 Alert set! {commodity_display} {direction_symbol} {price_display}\n\n__AFTER_ACTION__"

    except Exception as e:
        logger.error(f"Error setting alert: {e}", exc_info=True)
        return "Couldn't set alert. Try again?"


def handle_greeting_help(intent_type: str, user_name: str = None) -> str:
    """Handle greetings and help requests"""
    if intent_type == "greeting":
        name = f" {user_name}" if user_name else ""
        return (
            f"Hey{name}! 👋 Welcome to *PriceDeck*\n\n"
            "Share prices, check prices, get alerts.\n\n"
            "Which market did you visit today?"
        )
    else:
        return _get_help_message()


# =====================================================
# CART & CHECKOUT HANDLERS
# =====================================================

async def handle_add_to_cart(user_phone: str, commodity: str) -> str:
    """
    Initialize add-to-cart flow for a commodity.
    Gets latest Ogbete price and asks for quantity.

    Args:
        user_phone: User's WhatsApp number
        commodity: Commodity to add (e.g., "rice_local")

    Returns:
        Response marker or message
    """
    global partial_cart

    # Get latest price from Ogbete
    price_data = database.get_latest_price_for_commodity(commodity, market="ogbete")

    if not price_data:
        return "Sorry, no price available for this item at Ogbete Market right now.\n\n__AFTER_ACTION__"

    # Store partial cart data
    partial_cart[user_phone] = {
        "awaiting": "quantity",
        "commodity": commodity,
        "unit": price_data.get("unit", "unit"),
        "unit_price": price_data.get("price", 0)
    }

    commodity_display = clean_name(commodity)
    unit_display = clean_name(price_data.get("unit", "unit"))
    price_display = format_price(price_data.get("price", 0))

    return f"*{commodity_display}* - {price_display}/{unit_display}\n\nHow many {unit_display.lower()}s do you want?"


async def handle_cart_quantity_input(user_phone: str, message_text: str) -> str:
    """
    Handle quantity input from user.

    Args:
        user_phone: User's WhatsApp number
        message_text: User's message

    Returns:
        Response marker or message
    """
    global partial_cart

    if user_phone not in partial_cart:
        return "Something went wrong. Please start again.\n\n__AFTER_ACTION__"

    # Parse quantity
    text = message_text.strip()
    try:
        quantity = int(text)
        if quantity <= 0:
            raise ValueError()
    except ValueError:
        return "Please enter a valid quantity (e.g., 1, 2, 3)"

    partial = partial_cart[user_phone]
    partial_cart[user_phone]["quantity"] = quantity
    partial_cart[user_phone]["awaiting"] = "confirm_item"

    # Calculate line total
    unit_price = partial.get("unit_price", 0)
    line_total = quantity * unit_price

    commodity_display = clean_name(partial.get("commodity", ""))
    unit_display = clean_name(partial.get("unit", "unit"))
    price_display = format_price(unit_price)
    total_display = format_price(line_total)

    return f"__CONFIRM_CART_ITEM__:{commodity_display}|{quantity}|{unit_display}|{price_display}|{total_display}"


async def handle_cart_item_confirmation(user_phone: str, confirmed: bool) -> str:
    """
    Handle cart item confirmation (yes/no button).

    Args:
        user_phone: User's WhatsApp number
        confirmed: Whether user confirmed

    Returns:
        Response message
    """
    global partial_cart

    if user_phone not in partial_cart:
        return "Something went wrong. Please start again.\n\n__AFTER_ACTION__"

    if not confirmed:
        del partial_cart[user_phone]
        return "Item not added.\n\n__AFTER_ACTION__"

    partial = partial_cart[user_phone]

    # Add to database cart
    try:
        cart = database.get_or_create_cart(user_phone)
        database.add_item_to_cart(
            cart_id=cart["id"],
            commodity=partial["commodity"],
            quantity=partial["quantity"],
            unit=partial["unit"],
            unit_price=partial["unit_price"]
        )

        commodity_display = clean_name(partial["commodity"])
        quantity = partial["quantity"]
        del partial_cart[user_phone]

        return f"Added {quantity}x {commodity_display} to cart!\n\n__VIEW_CART__"

    except Exception as e:
        logger.error(f"Error adding to cart: {e}")
        del partial_cart[user_phone]
        return "Couldn't add to cart. Try again?\n\n__AFTER_ACTION__"


async def handle_quantity_change_input(user_phone: str, message_text: str) -> str:
    """
    Handle new quantity input for editing cart item quantity.

    Args:
        user_phone: User's WhatsApp number
        message_text: User's message (new quantity)

    Returns:
        Response marker or message
    """
    global partial_cart

    if user_phone not in partial_cart:
        return "Something went wrong. Please start again.\n\n__AFTER_ACTION__"

    partial = partial_cart[user_phone]
    commodity = partial.get("editing_commodity")

    if not commodity:
        del partial_cart[user_phone]
        return "Something went wrong. Please start again.\n\n__AFTER_ACTION__"

    # Parse new quantity
    text = message_text.strip()
    try:
        new_quantity = int(text)
        if new_quantity < 1:
            return "Quantity must be at least 1. Enter a valid quantity:"
    except ValueError:
        return "Please enter a valid number (e.g., 1, 2, 3):"

    # Update quantity in database
    try:
        success = database.update_cart_item_quantity(user_phone, commodity, new_quantity)

        # Clear editing state but keep other partial data
        if "editing_commodity" in partial_cart[user_phone]:
            del partial_cart[user_phone]["editing_commodity"]
        if partial_cart[user_phone].get("awaiting") == "new_quantity":
            del partial_cart[user_phone]["awaiting"]

        if success:
            commodity_display = clean_name(commodity)
            return f"✅ Updated {commodity_display} to {new_quantity}.\n\n__VIEW_CART__"
        else:
            return "Couldn't update quantity. Try again?\n\n__AFTER_ACTION__"

    except Exception as e:
        logger.error(f"Error updating quantity: {e}")
        return "Couldn't update quantity. Try again?\n\n__AFTER_ACTION__"


async def handle_checkout_start(user_phone: str) -> str:
    """
    Start checkout flow - ask for delivery address.

    Args:
        user_phone: User's WhatsApp number

    Returns:
        Response message
    """
    global partial_cart, user_action_context

    # Check cart has items
    cart_items = database.get_cart_items(user_phone)
    if not cart_items:
        return "Your cart is empty! Check prices to add items.\n\n__AFTER_ACTION__"

    user_action_context[user_phone] = "checkout"
    partial_cart[user_phone] = {"awaiting": "delivery_address"}

    return "Where should we deliver?\n\nEnter your full address (area, street, landmark):"


async def handle_checkout_address_input(user_phone: str, message_text: str) -> str:
    """
    Handle delivery address input.

    Args:
        user_phone: User's WhatsApp number
        message_text: User's address

    Returns:
        Response marker or message
    """
    global partial_cart

    if user_phone not in partial_cart:
        partial_cart[user_phone] = {}

    address = message_text.strip()
    if len(address) < 10:
        return "Please enter a more detailed address for delivery (area, street, landmark):"

    partial_cart[user_phone]["delivery_address"] = address
    partial_cart[user_phone]["awaiting"] = "contact_phone_choice"

    return "__CHECKOUT_PHONE__"


async def handle_checkout_phone_selection(user_phone: str, phone: str) -> str:
    """
    Handle phone number selection.

    Args:
        user_phone: User's WhatsApp number
        phone: Selected phone number

    Returns:
        Response marker
    """
    global partial_cart

    if user_phone not in partial_cart:
        return "Something went wrong. Please start again.\n\n__AFTER_ACTION__"

    partial_cart[user_phone]["contact_phone"] = phone
    partial_cart[user_phone]["awaiting"] = "confirmation"

    return "__CHECKOUT_CONFIRM__"


async def handle_checkout_phone_input(user_phone: str, message_text: str) -> str:
    """
    Handle custom phone number input.

    Args:
        user_phone: User's WhatsApp number
        message_text: User's phone number

    Returns:
        Response marker or message
    """
    # Basic phone validation
    phone = message_text.strip().replace(" ", "").replace("-", "")
    if not phone.isdigit() or len(phone) < 10:
        return "Please enter a valid phone number (e.g., 08012345678):"

    return await handle_checkout_phone_selection(user_phone, phone)


async def handle_checkout_confirm(user_phone: str) -> str:
    """
    Finalize checkout: create order, initiate Paystack payment.

    Args:
        user_phone: User's WhatsApp number

    Returns:
        Payment link marker or error message
    """
    global partial_cart, user_action_context
    from app.config import DELIVERY_FEE

    if user_phone not in partial_cart:
        return "Something went wrong. Please start again.\n\n__AFTER_ACTION__"

    partial = partial_cart[user_phone]
    cart_items = database.get_cart_items(user_phone)

    if not cart_items:
        del partial_cart[user_phone]
        return "Your cart is empty!\n\n__AFTER_ACTION__"

    # Calculate totals
    subtotal = sum(item["quantity"] * item["unit_price"] for item in cart_items)
    delivery_fee = DELIVERY_FEE
    total = subtotal + delivery_fee

    # Get vendor for Ogbete
    vendor = database.get_vendor_for_market("ogbete")

    try:
        # Create order
        order = database.create_order(
            whatsapp_number=user_phone,
            vendor_id=vendor["id"] if vendor else None,
            items=cart_items,
            subtotal=subtotal,
            delivery_fee=delivery_fee,
            total=total,
            delivery_address=partial.get("delivery_address", ""),
            contact_phone=partial.get("contact_phone", user_phone)
        )

        # Initialize Paystack payment
        from app.paystack_service import initialize_payment
        payment_result = await initialize_payment(
            email=f"{user_phone}@pricedeck.ng",
            amount=int(total * 100),  # Paystack uses kobo
            reference=order["order_number"],
            metadata={
                "order_id": str(order["id"]),
                "user_phone": user_phone
            }
        )

        if payment_result.get("status"):
            # Update order with payment reference
            database.update_order_payment_ref(order["id"], payment_result["data"]["reference"])

            # Clear cart and partial state
            database.clear_cart(user_phone)
            del partial_cart[user_phone]
            if user_phone in user_action_context:
                del user_action_context[user_phone]

            return f"__PAYMENT_LINK__:{payment_result['data']['authorization_url']}:{order['order_number']}"
        else:
            logger.error(f"Paystack init failed: {payment_result}")
            return "Payment initialization failed. Please try again.\n\n__AFTER_ACTION__"

    except Exception as e:
        logger.error(f"Checkout error: {e}", exc_info=True)
        return "Something went wrong during checkout. Please try again.\n\n__AFTER_ACTION__"


def get_cart_summary_text(user_phone: str) -> str:
    """
    Generate cart summary text for display.

    Args:
        user_phone: User's WhatsApp number

    Returns:
        Formatted cart summary
    """
    from app.config import DELIVERY_FEE

    cart_items = database.get_cart_items(user_phone)

    if not cart_items:
        return "Your cart is empty."

    lines = ["*Your Cart:*\n"]
    subtotal = 0

    for item in cart_items:
        item_total = int(item["quantity"]) * item["unit_price"]
        subtotal += item_total
        commodity_display = clean_name(item["commodity"])
        quantity = int(item["quantity"])
        unit_display = clean_name(item["unit"]).lower()
        price_display = format_price(item["unit_price"])
        total_display = format_price(item_total)
        lines.append(f"• {commodity_display} x{quantity} ({price_display}/{unit_display}) = {total_display}")

    delivery_fee = DELIVERY_FEE
    total = subtotal + delivery_fee

    lines.append(f"\n*Subtotal:* {format_price(subtotal)}")
    lines.append(f"*Delivery:* {format_price(delivery_fee)}")
    lines.append(f"*Total:* {format_price(total)}")
    lines.append("\n_Shopping available for Ogbete Market only._")

    return "\n".join(lines)
