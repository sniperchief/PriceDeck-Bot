"""
PriceDeck - Main FastAPI application
Handles WhatsApp webhook for commodity price intelligence
"""
import logging
import asyncio
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse
import httpx
from app.config import (
    VERIFY_TOKEN,
    WHATSAPP_TOKEN,
    WHATSAPP_API_URL,
    validate_config,
    ENVIRONMENT
)
from app.claude_tasks import (
    process_message,
    handle_market_selection,
    handle_unit_selection,
    handle_variety_selection,
    handle_add_to_cart,
    handle_cart_item_confirmation,
    handle_checkout_start,
    handle_checkout_phone_selection,
    handle_checkout_confirm,
    get_cart_summary_text,
    partial_price_reports,
    partial_cart,
    user_action_context
)
from app.database import (
    get_all_active_markets,
    is_user_contributor,
    get_cart_items,
    remove_cart_item,
    update_cart_item_quantity,
    is_vendor,
    get_order_by_id,
    update_order_status,
    get_vendor_for_market
)
from app.config import DELIVERY_FEE, ADMIN_WHATSAPP_NUMBER
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from app import alert_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="PriceDeck - Commodity Price Intelligence Bot",
    description="WhatsApp bot for Nigerian market price tracking",
    version="1.0.0"
)

# In-memory cache for commodities and markets (will be loaded from DB)
COMMODITIES_CACHE = []
MARKETS_CACHE = []

# Message deduplication - prevents processing the same message twice
# Key: message_id, Value: timestamp when processed
processed_message_ids = {}
MAX_PROCESSED_CACHE = 1000  # Keep last 1000 message IDs

# Background task reference
alert_checker_task = None


def is_message_processed(message_id: str) -> bool:
    """
    Check if a message was already processed.
    Also cleans up old entries if cache gets too large.
    """
    global processed_message_ids

    # Clean up if cache is too large (keep most recent half)
    if len(processed_message_ids) > MAX_PROCESSED_CACHE:
        sorted_items = sorted(processed_message_ids.items(), key=lambda x: x[1])
        processed_message_ids = dict(sorted_items[MAX_PROCESSED_CACHE // 2:])

    # Check if already processed
    if message_id in processed_message_ids:
        return True

    # Mark as processed
    processed_message_ids[message_id] = datetime.now().timestamp()
    return False


@app.on_event("startup")
async def startup_event():
    """Initialize app on startup"""
    global MARKETS_CACHE, alert_checker_task

    logger.info("Starting PriceDeck application...")

    try:
        # Validate configuration
        validate_config()
        logger.info("✅ Configuration validated successfully")

        # Load markets from database into cache
        MARKETS_CACHE = get_all_active_markets()
        logger.info(f"✅ Loaded {len(MARKETS_CACHE)} active markets into cache")

        # Set up alert service with message sender
        alert_service.set_message_sender(send_whatsapp_message)

        # Start background alert checker
        alert_checker_task = asyncio.create_task(alert_service.alert_checker_loop())
        logger.info("✅ Alert checker background task started")

        logger.info("✅ PriceDeck is ready to receive messages")

    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up on shutdown"""
    global alert_checker_task

    if alert_checker_task:
        alert_checker_task.cancel()
        try:
            await alert_checker_task
        except asyncio.CancelledError:
            pass
        logger.info("✅ Alert checker background task stopped")


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "PriceDeck",
        "version": "1.0.0",
        "environment": ENVIRONMENT
    }


@app.get("/webhook")
async def verify_webhook(request: Request):
    """
    Webhook verification endpoint for WhatsApp
    Meta sends a GET request to verify the webhook URL
    """
    # Get query parameters
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    logger.info(f"Webhook verification request received - mode: {mode}")

    # Check if mode and token are correct
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("✅ Webhook verified successfully")
        # Respond with challenge to confirm verification
        return PlainTextResponse(content=challenge, status_code=200)
    else:
        logger.warning("❌ Webhook verification failed - invalid token")
        raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/webhook")
async def receive_message(request: Request):
    """
    Webhook endpoint to receive messages from WhatsApp
    """
    try:
        # Get the request body
        body = await request.json()
        logger.info(f"Received webhook: {body}")

        # Extract message data
        if "entry" in body and len(body["entry"]) > 0:
            changes = body["entry"][0].get("changes", [])

            if len(changes) > 0 and "messages" in changes[0].get("value", {}):
                value = changes[0]["value"]
                messages = value["messages"]

                # Extract user's WhatsApp profile name
                user_name = None
                contacts = value.get("contacts", [])
                if contacts and len(contacts) > 0:
                    user_name = contacts[0].get("profile", {}).get("name")

                for message in messages:
                    # Extract message details
                    from_number = message.get("from")
                    message_id = message.get("id")
                    message_type = message.get("type")

                    # DEDUPLICATION CHECK: Skip if already processed
                    if is_message_processed(message_id):
                        logger.info(f"Skipping duplicate message: {message_id}")
                        continue

                    # Mark message as read immediately (shows blue ticks)
                    await mark_message_as_read(message_id)

                    # Get message content based on type
                    if message_type == "text":
                        message_body = message.get("text", {}).get("body", "")
                        logger.info(f"Message from {from_number} ({user_name}): {message_body}")

                        # Process message with Claude AI
                        response_text = await process_message(
                            message_text=message_body,
                            user_phone=from_number,
                            user_name=user_name,
                            available_markets=MARKETS_CACHE
                        )

                        # Check if we need to send interactive lists/buttons
                        if response_text == "__MAIN_MENU__":
                            await send_main_menu(from_number, welcome=True)
                        elif response_text == "__MENU__":
                            await send_main_menu(from_number, welcome=False)
                        elif "__AFTER_ACTION__" in response_text:
                            # Split message and show menu after
                            message_part = response_text.replace("__AFTER_ACTION__", "").strip()
                            await send_whatsapp_message(from_number, message_part)
                            await send_main_menu(from_number, welcome=False)
                        elif response_text.startswith("__SELECT_VARIETY__:"):
                            # Extract commodity from marker (e.g., "__SELECT_VARIETY__:garri")
                            commodity = response_text.split(":")[1]
                            await send_variety_buttons(from_number, commodity)
                        elif response_text == "__SELECT_UNIT__":
                            await send_unit_list(from_number)
                        elif response_text == "__SELECT_MARKET__":
                            await send_market_list(from_number, MARKETS_CACHE)
                        elif response_text.startswith("__ADD_TO_CART__:"):
                            # Format: __ADD_TO_CART__:commodity:price:unit
                            parts = response_text.split(":")
                            if len(parts) >= 4:
                                commodity = parts[1]
                                price = parts[2]
                                unit = parts[3]
                                await send_add_to_cart_buttons(from_number, commodity, price, unit)
                        elif response_text.startswith("__CONFIRM_CART_ITEM__:"):
                            # Format: __CONFIRM_CART_ITEM__:commodity|qty|unit|price|total
                            data = response_text.replace("__CONFIRM_CART_ITEM__:", "")
                            parts = data.split("|")
                            if len(parts) >= 5:
                                await send_cart_item_confirmation(from_number, parts[0], parts[1], parts[2], parts[3], parts[4])
                        elif "__VIEW_CART__" in response_text:
                            # Send any message before the marker, then show cart
                            message_part = response_text.replace("__VIEW_CART__", "").strip()
                            if message_part:
                                await send_whatsapp_message(from_number, message_part)
                            await send_cart_summary(from_number)
                        elif response_text == "__CHECKOUT_PHONE__":
                            await send_phone_selection_buttons(from_number)
                        elif response_text == "__CHECKOUT_CONFIRM__":
                            await send_checkout_confirmation(from_number)
                        elif response_text.startswith("__PAYMENT_LINK__:"):
                            # Format: __PAYMENT_LINK__:url:order_number
                            parts = response_text.split(":", 2)
                            if len(parts) >= 3:
                                url = parts[1]
                                order_number = parts[2]
                                await send_payment_link(from_number, url, order_number)
                        else:
                            await send_whatsapp_message(from_number, response_text)

                    elif message_type == "interactive":
                        # Handle interactive list/button responses
                        interactive = message.get("interactive", {})
                        interactive_type = interactive.get("type")

                        if interactive_type == "list_reply":
                            selected = interactive.get("list_reply", {})
                            selected_id = selected.get("id", "")
                            selected_title = selected.get("title", "")
                            logger.info(f"List selection by {from_number}: {selected_id} ({selected_title})")

                            # Check if it's a cart item removal
                            if selected_id.startswith("remove_"):
                                commodity = selected_id.replace("remove_", "")
                                removed = remove_cart_item(from_number, commodity)

                                if removed:
                                    # Check if cart is now empty
                                    remaining_items = get_cart_items(from_number)
                                    if not remaining_items:
                                        await send_whatsapp_message(from_number, "Item removed. Your cart is now empty.")
                                        await send_main_menu(from_number, welcome=False)
                                    else:
                                        commodity_display = commodity.replace("_", " ").title()
                                        await send_whatsapp_message(from_number, f"{commodity_display} removed from cart.")
                                        await send_cart_summary(from_number)
                                else:
                                    await send_whatsapp_message(from_number, "Couldn't remove item. Try again.")
                                    await send_cart_summary(from_number)

                            # Check if it's a quantity change request
                            elif selected_id.startswith("change_qty_"):
                                commodity = selected_id.replace("change_qty_", "")
                                # Store which item we're changing quantity for
                                partial_cart[from_number] = partial_cart.get(from_number, {})
                                partial_cart[from_number]["editing_commodity"] = commodity
                                partial_cart[from_number]["awaiting"] = "new_quantity"
                                commodity_display = commodity.replace("_", " ").title()
                                await send_whatsapp_message(from_number, f"Enter new quantity for {commodity_display} (minimum 1):")

                            # Check if it's a delivery area selection
                            elif selected_id == "area_not_listed":
                                await send_whatsapp_message(
                                    from_number,
                                    "Sorry, we currently only deliver to the listed areas.\n\n"
                                    "We're expanding soon! 🚀\n\n"
                                    "Your cart is saved - you can checkout when we reach your area."
                                )
                                await send_main_menu(from_number, welcome=False)

                            elif selected_id in ["new_haven", "ogui_road", "independence_layout", "trans_ekulu", "gra", "presidential_road", "golf", "okpara_avenue", "agbani_road"]:
                                # User selected a delivery area
                                area_names = {
                                    "new_haven": "New Haven",
                                    "ogui_road": "Ogui Road",
                                    "independence_layout": "Independence Layout",
                                    "trans_ekulu": "Trans Ekulu",
                                    "gra": "GRA",
                                    "presidential_road": "Presidential Road",
                                    "golf": "Golf",
                                    "okpara_avenue": "Okpara Avenue",
                                    "agbani_road": "Agbani Road"
                                }
                                partial_cart[from_number] = partial_cart.get(from_number, {})
                                partial_cart[from_number]["delivery_area"] = selected_id
                                partial_cart[from_number]["awaiting"] = "delivery_address"
                                area_name = area_names.get(selected_id, selected_id)
                                await send_whatsapp_message(from_number, f"Enter your delivery address in {area_name}\n\n(e.g., street name, house number, landmark):")

                            # Check if it's a unit selection
                            elif selected_id in ["paint", "half_paint", "cup", "bag", "half_bag", "mudu", "kg", "piece", "other_unit"]:
                                # Unit selection
                                if selected_id == "other_unit":
                                    await send_whatsapp_message(from_number, "Type the unit:")
                                else:
                                    # Report flow - ask for price
                                    response_text = await handle_unit_selection(from_number, selected_id)
                                    if response_text == "__MAIN_MENU__":
                                        await send_main_menu(from_number, welcome=True)
                                    elif response_text == "__SELECT_MARKET__":
                                        await send_market_list(from_number, MARKETS_CACHE)
                                    else:
                                        # Price prompt: "What's the price per paint?"
                                        await send_whatsapp_message(from_number, response_text)

                            elif selected_id == "other_market":
                                # User wants to type custom market
                                await send_whatsapp_message(from_number, "Type the market name:")
                            else:
                                # Market selection
                                response_text = await handle_market_selection(from_number, selected_id)
                                # Handle response markers
                                if "__AFTER_ACTION__" in response_text:
                                    message_part = response_text.replace("__AFTER_ACTION__", "").strip()
                                    await send_whatsapp_message(from_number, message_part)
                                    await send_main_menu(from_number, welcome=False)
                                else:
                                    await send_whatsapp_message(from_number, response_text)

                        elif interactive_type == "button_reply":
                            # Handle button responses
                            button = interactive.get("button_reply", {})
                            button_id = button.get("id", "")
                            button_title = button.get("title", "")
                            logger.info(f"Button clicked by {from_number}: {button_id} ({button_title})")

                            # Main menu buttons
                            if button_id == "menu_check_price":
                                user_action_context[from_number] = "check_price"
                                await send_commodity_buttons(from_number, "check")

                            elif button_id == "menu_report_price":
                                # Check contributor status
                                if is_user_contributor(from_number):
                                    user_action_context[from_number] = "report_price"
                                    await send_commodity_buttons(from_number, "report")
                                else:
                                    await send_contributor_onboarding(from_number)


                            # Commodity selection buttons (check flow)
                            elif button_id.startswith("check_"):
                                commodity = button_id.replace("check_", "")
                                user_action_context[from_number] = "check_price"
                                if commodity in ["garri", "rice", "beans"]:
                                    await send_variety_buttons(from_number, commodity)

                            # Commodity selection buttons (report flow)
                            elif button_id.startswith("report_"):
                                commodity = button_id.replace("report_", "")
                                user_action_context[from_number] = "report_price"
                                if commodity in ["garri", "rice", "beans"]:
                                    await send_variety_buttons(from_number, commodity)


                            # Cart buttons
                            elif button_id.startswith("add_to_cart_"):
                                commodity = button_id.replace("add_to_cart_", "")
                                response_text = await handle_add_to_cart(from_number, commodity)
                                if "__AFTER_ACTION__" in response_text:
                                    message_part = response_text.replace("__AFTER_ACTION__", "").strip()
                                    await send_whatsapp_message(from_number, message_part)
                                    await send_main_menu(from_number, welcome=False)
                                else:
                                    await send_whatsapp_message(from_number, response_text)

                            elif button_id == "view_cart":
                                await send_cart_summary(from_number)

                            elif button_id == "checkout":
                                # Check if market is open
                                if not is_market_open():
                                    await send_whatsapp_message(
                                        from_number,
                                        "*Vendors are closed*\n\n"
                                        "Ordering is available 8am - 4pm daily.\n\n"
                                        "Your cart is saved. Come back during market hours to complete your order!"
                                    )
                                    await send_main_menu(from_number, welcome=False)
                                else:
                                    # Check if cart has items
                                    cart_items = get_cart_items(from_number)
                                    if not cart_items:
                                        await send_whatsapp_message(from_number, "Your cart is empty! Check prices to add items.")
                                        await send_main_menu(from_number, welcome=False)
                                    else:
                                        # Check if we already have delivery info saved (for returning from edit)
                                        partial = partial_cart.get(from_number, {})
                                        if partial.get("delivery_address") and partial.get("contact_phone"):
                                            # Skip to confirmation
                                            partial_cart[from_number]["awaiting"] = "confirmation"
                                            await send_checkout_confirmation(from_number)
                                        else:
                                            # Start fresh - show delivery areas
                                            partial_cart[from_number] = partial_cart.get(from_number, {})
                                            await send_delivery_area_list(from_number)

                            elif button_id == "continue_shopping":
                                await send_main_menu(from_number, welcome=False)

                            elif button_id == "edit_cart":
                                await send_edit_cart_list(from_number)

                            elif button_id == "confirm_cart_item":
                                response_text = await handle_cart_item_confirmation(from_number, True)
                                if "__VIEW_CART__" in response_text:
                                    message_part = response_text.replace("__VIEW_CART__", "").strip()
                                    await send_whatsapp_message(from_number, message_part)
                                    await send_cart_summary(from_number)
                                elif "__AFTER_ACTION__" in response_text:
                                    message_part = response_text.replace("__AFTER_ACTION__", "").strip()
                                    await send_whatsapp_message(from_number, message_part)
                                    await send_main_menu(from_number, welcome=False)
                                else:
                                    await send_whatsapp_message(from_number, response_text)

                            elif button_id == "cancel_cart_item":
                                response_text = await handle_cart_item_confirmation(from_number, False)
                                if "__AFTER_ACTION__" in response_text:
                                    message_part = response_text.replace("__AFTER_ACTION__", "").strip()
                                    await send_whatsapp_message(from_number, message_part)
                                    await send_main_menu(from_number, welcome=False)
                                else:
                                    await send_whatsapp_message(from_number, response_text)

                            elif button_id == "use_whatsapp_number":
                                response_text = await handle_checkout_phone_selection(from_number, from_number)
                                if response_text == "__CHECKOUT_CONFIRM__":
                                    await send_checkout_confirmation(from_number)
                                else:
                                    await send_whatsapp_message(from_number, response_text)

                            elif button_id == "enter_different_phone":
                                partial_cart[from_number]["awaiting"] = "contact_phone"
                                await send_whatsapp_message(from_number, "Enter the phone number our rider should call:")

                            elif button_id == "confirm_checkout":
                                # Check if market is open
                                if not is_market_open():
                                    await send_whatsapp_message(
                                        from_number,
                                        "*Vendors are closed*\n\n"
                                        "Ordering is available 8am - 4pm daily.\n\n"
                                        "Your cart is saved. Come back during market hours to complete your order!"
                                    )
                                    await send_main_menu(from_number, welcome=False)
                                else:
                                    response_text = await handle_checkout_confirm(from_number)
                                    if response_text.startswith("__PAYMENT_LINK__:"):
                                        parts = response_text.split(":", 2)
                                        if len(parts) >= 3:
                                            url = parts[1]
                                            order_number = parts[2]
                                            await send_payment_link(from_number, url, order_number)
                                    elif "__AFTER_ACTION__" in response_text:
                                        message_part = response_text.replace("__AFTER_ACTION__", "").strip()
                                        await send_whatsapp_message(from_number, message_part)
                                        await send_main_menu(from_number, welcome=False)
                                    else:
                                        await send_whatsapp_message(from_number, response_text)

                            elif button_id == "cancel_checkout":
                                if from_number in partial_cart:
                                    del partial_cart[from_number]
                                await send_whatsapp_message(from_number, "Checkout cancelled. Your cart items are still saved.")
                                await send_main_menu(from_number, welcome=False)

                            elif button_id == "edit_cart_checkout":
                                # Go back to cart from order summary (keep address/phone)
                                await send_cart_summary(from_number)

                            # Vendor response buttons
                            elif button_id.startswith("vendor_confirm_"):
                                order_id = button_id.replace("vendor_confirm_", "")
                                await handle_vendor_order_response(from_number, order_id, "confirmed")

                            elif button_id.startswith("vendor_reject_"):
                                order_id = button_id.replace("vendor_reject_", "")
                                await handle_vendor_order_response(from_number, order_id, "rejected")

                            # Variety selection (garri_white, rice_local, etc.)
                            else:
                                variety_prefixes = ["garri_", "rice_", "beans_"]
                                if any(button_id.startswith(prefix) for prefix in variety_prefixes):
                                    # Check/Report flow
                                    response_text = await handle_variety_selection(from_number, button_id)
                                    # Check what's next based on response
                                    if response_text == "__SELECT_UNIT__":
                                        await send_unit_list(from_number)
                                    elif response_text == "__SELECT_MARKET__":
                                        await send_market_list(from_number, MARKETS_CACHE)
                                    elif "__ADD_TO_CART__:" in response_text:
                                        # Format: message\n\n__ADD_TO_CART__:commodity:price:unit
                                        parts = response_text.split("__ADD_TO_CART__:")
                                        message_part = parts[0].strip()
                                        cart_info = parts[1].split(":")
                                        await send_whatsapp_message(from_number, message_part)
                                        if len(cart_info) >= 3:
                                            await send_add_to_cart_buttons(from_number, cart_info[0], cart_info[1], cart_info[2])
                                    elif "__AFTER_ACTION__" in response_text:
                                        message_part = response_text.replace("__AFTER_ACTION__", "").strip()
                                        await send_whatsapp_message(from_number, message_part)
                                        await send_main_menu(from_number, welcome=False)
                                    else:
                                        await send_whatsapp_message(from_number, response_text)

                    else:
                        logger.info(f"Received non-text message type: {message_type}")
                        await send_whatsapp_message(
                            from_number,
                            "I can only process text messages. Please type your message."
                        )

        # Always return 200 OK to acknowledge receipt
        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        # Still return 200 to avoid Meta retrying
        return {"status": "error", "message": str(e)}


def is_market_open() -> bool:
    """
    Check if current time is within market hours (8am - 4pm Nigeria time).
    Nigeria timezone is Africa/Lagos (UTC+1).

    Returns:
        True if market is open, False otherwise
    """
    try:
        nigeria_tz = ZoneInfo('Africa/Lagos')
        now = datetime.now(nigeria_tz)
        current_time = now.time()

        open_time = dt_time(8, 0)   # 8:00 AM
        close_time = dt_time(16, 0)  # 4:00 PM

        return open_time <= current_time <= close_time
    except Exception as e:
        logger.error(f"Error checking market hours: {e}")
        # Default to open if there's an error
        return True


async def mark_message_as_read(message_id: str):
    """
    Mark a message as read (shows blue ticks to sender)

    Args:
        message_id: The WhatsApp message ID to mark as read
    """
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=5.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Message {message_id} marked as read")
                return True
            else:
                logger.debug(f"Could not mark message as read: {response.status_code}")
                return False

    except Exception as e:
        logger.debug(f"Error marking message as read: {e}")
        return False


async def send_whatsapp_message(to: str, message: str):
    """
    Send a WhatsApp message to a recipient

    Args:
        to: Recipient's WhatsApp number (format: 2348012345678)
        message: Message text to send
    """
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": message
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Message sent to {to}")
                return True
            else:
                logger.error(f"❌ Failed to send message: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending message: {e}")
        return False


async def send_market_list(to: str, markets: list):
    """
    Send an interactive list of markets to select from

    Args:
        to: Recipient's WhatsApp number
        markets: List of market dicts with 'slug' and 'display_name'
    """
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        # Build market rows (max 10 items in a list)
        rows = []
        for market in markets[:9]:  # Leave room for "Other"
            rows.append({
                "id": market.get("slug", "unknown"),
                "title": market.get("display_name", market.get("slug", "Unknown"))[:24],  # Max 24 chars
                "description": f"Select {market.get('display_name', '')}"[:72]  # Max 72 chars
            })

        # Add "Other" option
        rows.append({
            "id": "other_market",
            "title": "Other",
            "description": "Type a different market name"
        })

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {
                    "type": "text",
                    "text": "Select Market"
                },
                "body": {
                    "text": "Which market did you buy from?"
                },
                "action": {
                    "button": "Choose Market",
                    "sections": [
                        {
                            "title": "Markets",
                            "rows": rows
                        }
                    ]
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Market list sent to {to}")
                return True
            else:
                logger.error(f"❌ Failed to send market list: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending market list: {e}")
        return False


async def send_unit_list(to: str):
    """
    Send an interactive list of units/measurements to select from

    Args:
        to: Recipient's WhatsApp number
    """
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        # Common units in Nigerian markets
        units = [
            {"id": "paint", "title": "Paint", "description": "Standard paint bucket"},
            {"id": "half_paint", "title": "Half Paint", "description": "Half paint bucket"},
            {"id": "cup", "title": "Cup", "description": "Cup measure"},
            {"id": "bag", "title": "Bag", "description": "Full bag (50kg)"},
            {"id": "half_bag", "title": "Half Bag", "description": "Half bag (25kg)"},
            {"id": "mudu", "title": "Mudu", "description": "Traditional measure"},
            {"id": "kg", "title": "Kg", "description": "Per kilogram"},
            {"id": "piece", "title": "Piece", "description": "Per piece/unit"},
            {"id": "other_unit", "title": "Other", "description": "Type a different unit"}
        ]

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {
                    "type": "text",
                    "text": "Select Unit"
                },
                "body": {
                    "text": "What measurement/unit?"
                },
                "action": {
                    "button": "Choose Unit",
                    "sections": [
                        {
                            "title": "Units",
                            "rows": units
                        }
                    ]
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Unit list sent to {to}")
                return True
            else:
                logger.error(f"❌ Failed to send unit list: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending unit list: {e}")
        return False


# Variety definitions for commodities with sub-types
COMMODITY_VARIETIES = {
    "garri": [
        {"id": "garri_white", "title": "White"},
        {"id": "garri_yellow", "title": "Yellow"},
        {"id": "garri_ijebu", "title": "Ijebu"}
    ],
    "rice": [
        {"id": "rice_local", "title": "Local"},
        {"id": "rice_foreign", "title": "Foreign"},
        {"id": "rice_ofada", "title": "Ofada"}
    ],
    "beans": [
        {"id": "beans_oloyin", "title": "Oloyin"},
        {"id": "beans_brown", "title": "Brown"},
        {"id": "beans_iron", "title": "Iron"}
    ]
}


async def send_variety_buttons(to: str, commodity: str):
    """
    Send reply buttons for commodity variety selection

    Args:
        to: Recipient's WhatsApp number
        commodity: Base commodity (garri, rice, or beans)
    """
    try:
        varieties = COMMODITY_VARIETIES.get(commodity)
        if not varieties:
            logger.error(f"No varieties defined for commodity: {commodity}")
            return False

        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        # Build buttons (max 3)
        buttons = []
        for variety in varieties[:3]:
            buttons.append({
                "type": "reply",
                "reply": {
                    "id": variety["id"],
                    "title": variety["title"]
                }
            })

        commodity_display = commodity.title()
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {
                    "text": f"What type of {commodity_display}?"
                },
                "action": {
                    "buttons": buttons
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Variety buttons sent to {to} for {commodity}")
                return True
            else:
                logger.error(f"❌ Failed to send variety buttons: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending variety buttons: {e}")
        return False


async def send_main_menu(to: str, welcome: bool = True):
    """
    Send main menu with action buttons based on user type.
    Regular users: Check Price, View Cart
    Verified contributors: Check Price, Report Price

    Args:
        to: Recipient's WhatsApp number
        welcome: If True, include welcome text; if False, just "What next?"
    """
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        if welcome:
            body_text = (
                "Welcome to *PriceDeck*\n\n"
                "Your real-time guide to market prices in Enugu.\n\n"
                "What would you like to do?"
            )
        else:
            body_text = "What would you like to do next?"

        # Check if user is a verified contributor
        is_contributor = is_user_contributor(to)

        if is_contributor:
            # Verified contributors: Check Price + Report Price
            buttons = [
                {"type": "reply", "reply": {"id": "menu_check_price", "title": "Check Price"}},
                {"type": "reply", "reply": {"id": "menu_report_price", "title": "Report Price"}}
            ]
        else:
            # Regular users: Check Price + View Cart
            buttons = [
                {"type": "reply", "reply": {"id": "menu_check_price", "title": "Check Price"}},
                {"type": "reply", "reply": {"id": "view_cart", "title": "View Cart"}}
            ]

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {
                    "buttons": buttons
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Main menu sent to {to}")
                return True
            else:
                logger.error(f"❌ Failed to send main menu: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending main menu: {e}")
        return False


async def send_commodity_buttons(to: str, action: str):
    """
    Send commodity selection buttons

    Args:
        to: Recipient's WhatsApp number
        action: "check" or "report" - determines button ID prefix
    """
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": "Which commodity?"},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": f"{action}_garri", "title": "Garri"}},
                        {"type": "reply", "reply": {"id": f"{action}_rice", "title": "Rice"}},
                        {"type": "reply", "reply": {"id": f"{action}_beans", "title": "Beans"}}
                    ]
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Commodity buttons sent to {to} for {action}")
                return True
            else:
                logger.error(f"❌ Failed to send commodity buttons: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending commodity buttons: {e}")
        return False


async def send_contributor_onboarding(to: str):
    """Send message explaining how to become a contributor"""
    message = (
        "Price reporting is for verified contributors.\n\n"
        "Contributors are traders, vendors, and regular shoppers "
        "who help keep prices accurate.\n\n"
        "Want to join? Reply *JOIN* to apply."
    )
    await send_whatsapp_message(to, message)


# =====================================================
# CART & SHOPPING UI FUNCTIONS
# =====================================================

async def send_add_to_cart_buttons(to: str, commodity: str, price: str, unit: str):
    """
    Send Add to Cart and View Cart buttons after showing prices.
    Only shows Add to Cart during market hours (8am-4pm Nigeria time).

    Args:
        to: Recipient's WhatsApp number
        commodity: Commodity name
        price: Price display string
        unit: Unit name
    """
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        # Check if market is open
        if is_market_open():
            # Market open - show Add to Cart button
            body_text = (
                f"Want to buy from Ogbete Market?\n\n"
                f"_Shopping available for Ogbete Market only._"
            )

            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body_text},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": f"add_to_cart_{commodity}", "title": "Add to Cart"}},
                            {"type": "reply", "reply": {"id": "view_cart", "title": "View Cart"}}
                        ]
                    }
                }
            }
        else:
            # Market closed - show message with View Cart only
            body_text = (
                f"*Ordering available 8am - 4pm daily*\n\n"
                f"Vendors are currently closed. Check back during market hours!\n\n"
                f"_You can still view your cart._"
            )

            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body_text},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": "view_cart", "title": "View Cart"}}
                        ]
                    }
                }
            }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Add to cart buttons sent to {to}")
                return True
            else:
                logger.error(f"❌ Failed to send cart buttons: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending cart buttons: {e}")
        return False


async def send_cart_item_confirmation(to: str, commodity: str, quantity: str, unit: str, price: str, total: str):
    """
    Send confirmation buttons for adding item to cart

    Args:
        to: Recipient's WhatsApp number
        commodity: Commodity display name
        quantity: Number of units
        unit: Unit display name
        price: Price per unit
        total: Line total
    """
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        body_text = (
            f"*Add to cart?*\n\n"
            f"{commodity} x{quantity}\n"
            f"{price}/{unit}\n"
            f"*Total: {total}*"
        )

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": "confirm_cart_item", "title": "Yes, Add"}},
                        {"type": "reply", "reply": {"id": "cancel_cart_item", "title": "Cancel"}}
                    ]
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Cart item confirmation sent to {to}")
                return True
            else:
                logger.error(f"❌ Failed to send cart confirmation: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending cart confirmation: {e}")
        return False


async def send_cart_summary(to: str):
    """
    Send cart summary with checkout, edit, and add more buttons

    Args:
        to: Recipient's WhatsApp number
    """
    try:
        cart_items = get_cart_items(to)

        if not cart_items:
            await send_whatsapp_message(to, "Your cart is empty. Check prices to add items!")
            await send_main_menu(to, welcome=False)
            return

        # Build cart summary
        summary = get_cart_summary_text(to)

        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": summary},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": "checkout", "title": "Checkout"}},
                        {"type": "reply", "reply": {"id": "edit_cart", "title": "Edit Cart"}},
                        {"type": "reply", "reply": {"id": "continue_shopping", "title": "Add More"}}
                    ]
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Cart summary sent to {to}")
                return True
            else:
                logger.error(f"❌ Failed to send cart summary: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending cart summary: {e}")
        return False


async def send_edit_cart_list(to: str):
    """
    Send interactive list of cart items for editing (change quantity or remove)

    Args:
        to: Recipient's WhatsApp number
    """
    try:
        cart_items = get_cart_items(to)

        if not cart_items:
            await send_whatsapp_message(to, "Your cart is empty.")
            await send_main_menu(to, welcome=False)
            return

        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        # Build item rows - each item gets 2 options: change qty and remove
        rows = []
        for item in cart_items[:5]:  # Max 5 items (2 rows each = 10 rows max)
            commodity = item.get("commodity", "")
            commodity_display = commodity.replace("_", " ").title()
            quantity = int(item.get("quantity", 1))
            unit = item.get("unit", "unit").replace("_", " ").title()

            # Change quantity option
            rows.append({
                "id": f"change_qty_{commodity}",
                "title": f"Edit {commodity_display}"[:24],
                "description": f"Change quantity (currently {quantity}x)"[:72]
            })
            # Remove option
            rows.append({
                "id": f"remove_{commodity}",
                "title": f"Remove {commodity_display}"[:24],
                "description": f"Remove from cart"[:72]
            })

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {
                    "type": "text",
                    "text": "Edit Cart"
                },
                "body": {
                    "text": "Select an item to remove from your cart:"
                },
                "action": {
                    "button": "Select Item",
                    "sections": [
                        {
                            "title": "Cart Items",
                            "rows": rows
                        }
                    ]
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Edit cart list sent to {to}")
                return True
            else:
                logger.error(f"❌ Failed to send edit cart list: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending edit cart list: {e}")
        return False


# Delivery areas PriceDeck covers
DELIVERY_AREAS = [
    {"id": "new_haven", "title": "New Haven", "description": "New Haven area"},
    {"id": "ogui_road", "title": "Ogui Road", "description": "Ogui Road area"},
    {"id": "independence_layout", "title": "Independence Layout", "description": "Independence Layout"},
    {"id": "trans_ekulu", "title": "Trans Ekulu", "description": "Trans Ekulu area"},
    {"id": "gra", "title": "GRA", "description": "Government Reserved Area"},
    {"id": "presidential_road", "title": "Presidential Road", "description": "Presidential Road area"},
    {"id": "golf", "title": "Golf", "description": "Golf Estate area"},
    {"id": "okpara_avenue", "title": "Okpara Avenue", "description": "Okpara Avenue area"},
    {"id": "agbani_road", "title": "Agbani Road", "description": "Agbani Road area"},
    {"id": "area_not_listed", "title": "My area not listed", "description": "I'm outside these areas"}
]


async def send_delivery_area_list(to: str):
    """
    Send interactive list of delivery areas for checkout

    Args:
        to: Recipient's WhatsApp number
    """
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        rows = [{"id": area["id"], "title": area["title"], "description": area["description"]} for area in DELIVERY_AREAS]

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {
                    "type": "text",
                    "text": "Select Delivery Area"
                },
                "body": {
                    "text": "📍 Select your delivery area\n\nNote: We only deliver to the listed areas."
                },
                "action": {
                    "button": "Choose Area",
                    "sections": [
                        {
                            "title": "Delivery Areas",
                            "rows": rows
                        }
                    ]
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Delivery area list sent to {to}")
                return True
            else:
                logger.error(f"❌ Failed to send delivery area list: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending delivery area list: {e}")
        return False


async def send_phone_selection_buttons(to: str):
    """
    Send phone number selection buttons for checkout

    Args:
        to: Recipient's WhatsApp number
    """
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        body_text = "Which phone number should our rider call for delivery?"

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": "use_whatsapp_number", "title": "Use This Number"}},
                        {"type": "reply", "reply": {"id": "enter_different_phone", "title": "Different Number"}}
                    ]
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Phone selection buttons sent to {to}")
                return True
            else:
                logger.error(f"❌ Failed to send phone buttons: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending phone buttons: {e}")
        return False


def format_price_display(price: float) -> str:
    """Format price for display - use k for thousands"""
    if price >= 1000 and price % 1000 == 0:
        return f"{int(price // 1000)}k"
    else:
        return f"{int(price):,}"


async def send_checkout_confirmation(to: str):
    """
    Send order summary with Pay Now button

    Args:
        to: Recipient's WhatsApp number
    """
    try:
        cart_items = get_cart_items(to)
        partial = partial_cart.get(to, {})

        if not cart_items:
            await send_whatsapp_message(to, "Your cart is empty!")
            await send_main_menu(to, welcome=False)
            return

        # Calculate totals
        subtotal = sum(item["quantity"] * item["unit_price"] for item in cart_items)
        delivery_fee = DELIVERY_FEE
        total = subtotal + delivery_fee

        # Build summary with detailed item breakdown
        lines = ["*Order Summary*\n", "*Items:*"]
        for item in cart_items:
            commodity_display = item["commodity"].replace("_", " ").title()
            quantity = int(item["quantity"])
            unit_price = item["unit_price"]
            unit = item["unit"].replace("_", " ").lower()
            line_total = quantity * unit_price

            price_display = format_price_display(unit_price)
            total_display = format_price_display(line_total)

            lines.append(f"- {commodity_display} x{quantity} ({price_display}/{unit}) = {total_display}")

        lines.extend([
            f"\n*Subtotal:* {format_price_display(subtotal)}",
            f"*Delivery:* {format_price_display(delivery_fee)}",
            f"*Total:* {format_price_display(total)}",
            f"\n*Deliver to:* {partial.get('delivery_address', 'N/A')}",
            f"*Contact:* {partial.get('contact_phone', to)}",
            "\nProceed to payment?"
        ])

        summary = "\n".join(lines)

        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": summary},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": "confirm_checkout", "title": "Pay Now"}},
                        {"type": "reply", "reply": {"id": "edit_cart_checkout", "title": "Edit Cart"}},
                        {"type": "reply", "reply": {"id": "cancel_checkout", "title": "Cancel"}}
                    ]
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Checkout confirmation sent to {to}")
                return True
            else:
                logger.error(f"❌ Failed to send checkout confirmation: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending checkout confirmation: {e}")
        return False


async def send_payment_link(to: str, payment_url: str, order_number: str):
    """
    Send Paystack payment link to user

    Args:
        to: Recipient's WhatsApp number
        payment_url: Paystack payment URL
        order_number: Order number for reference
    """
    message = (
        f"*Order #{order_number}*\n\n"
        f"Click the link below to complete payment:\n\n"
        f"{payment_url}\n\n"
        f"Once paid, we'll notify the vendor and arrange delivery."
    )
    await send_whatsapp_message(to, message)


async def send_vendor_order_notification(vendor_phone: str, order: dict):
    """
    Notify vendor of new paid order with CONFIRM/REJECT buttons

    Args:
        vendor_phone: Vendor's WhatsApp number
        order: Order data
    """
    try:
        items = order.get("items", [])
        items_text = "\n".join([
            f"- {item['commodity'].replace('_', ' ').title()} x{item['quantity']}"
            for item in items
        ])

        total = order.get("total", 0)
        body_text = (
            f"*New Order #{order['order_number']}*\n\n"
            f"{items_text}\n\n"
            f"*Total:* {total:,.0f}\n"
            f"*Deliver to:* {order.get('delivery_address', 'N/A')}\n"
            f"*Contact:* {order.get('contact_phone', 'N/A')}\n\n"
            f"Can you fulfill this order?"
        )

        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": vendor_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": f"vendor_confirm_{order['id']}", "title": "CONFIRM"}},
                        {"type": "reply", "reply": {"id": f"vendor_reject_{order['id']}", "title": "REJECT"}}
                    ]
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Vendor notification sent to {vendor_phone} for order {order['order_number']}")
                return True
            else:
                logger.error(f"❌ Failed to send vendor notification: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending vendor notification: {e}")
        return False


async def handle_vendor_order_response(vendor_phone: str, order_id: str, response: str):
    """
    Handle vendor CONFIRM/REJECT response to order

    Args:
        vendor_phone: Vendor's WhatsApp number
        order_id: Order ID
        response: "confirmed" or "rejected"
    """
    # Verify this is actually a vendor
    if not is_vendor(vendor_phone):
        await send_whatsapp_message(vendor_phone, "You're not registered as a vendor.")
        return

    # Get order
    order = get_order_by_id(order_id)
    if not order:
        await send_whatsapp_message(vendor_phone, "Order not found.")
        return

    # Check order is in correct state
    if order["status"] != "paid_awaiting_vendor":
        await send_whatsapp_message(
            vendor_phone,
            f"Order #{order['order_number']} is no longer awaiting response."
        )
        return

    buyer_phone = order["whatsapp_number"]

    if response == "confirmed":
        # Update order
        update_order_status(order_id, "vendor_confirmed")

        # Notify vendor
        await send_whatsapp_message(
            vendor_phone,
            f"Order #{order['order_number']} confirmed!\n\n"
            f"*Deliver to:* {order['delivery_address']}\n"
            f"*Contact:* {order['contact_phone']}"
        )

        # Notify buyer
        await send_whatsapp_message(
            buyer_phone,
            f"Great news! Your order #{order['order_number']} is confirmed!\n\n"
            f"The vendor is preparing your items for delivery."
        )

    else:  # rejected
        # Update order
        update_order_status(order_id, "vendor_rejected")

        # Notify vendor
        await send_whatsapp_message(
            vendor_phone,
            f"Order #{order['order_number']} rejected."
        )

        # Notify buyer and mention refund
        await send_whatsapp_message(
            buyer_phone,
            f"Sorry, the vendor cannot fulfill order #{order['order_number']}.\n\n"
            f"Your payment will be refunded within 24 hours."
        )

        # Notify admin for manual refund
        await send_whatsapp_message(
            ADMIN_WHATSAPP_NUMBER,
            f"*REFUND NEEDED*\n\n"
            f"Order #{order['order_number']}\n"
            f"Amount: {order.get('total', 0):,.0f}\n"
            f"Reference: {order.get('payment_reference', 'N/A')}"
        )


# =====================================================
# PAYSTACK WEBHOOK
# =====================================================

@app.post("/paystack/webhook")
async def paystack_webhook(request: Request):
    """
    Paystack webhook handler for payment notifications
    """
    from app.database import get_order_by_reference, update_order_status, update_order

    try:
        # Get signature
        signature = request.headers.get("x-paystack-signature", "")

        # Get raw body
        body = await request.body()

        # Verify signature
        from app.paystack_service import verify_webhook_signature
        if not verify_webhook_signature(body, signature):
            logger.warning("Invalid Paystack webhook signature")
            return {"status": "error", "message": "Invalid signature"}

        # Parse payload
        payload = await request.json()
        event = payload.get("event")
        data = payload.get("data", {})

        logger.info(f"Paystack webhook: {event}")

        if event == "charge.success":
            reference = data.get("reference")

            # Get order
            order = get_order_by_reference(reference)

            if order:
                # Update order status
                update_order_status(
                    order["id"],
                    status="paid_awaiting_vendor",
                    payment_status="paid"
                )

                # Notify buyer
                await send_whatsapp_message(
                    order["whatsapp_number"],
                    f"Payment received for order #{order['order_number']}!\n\n"
                    f"We're notifying the vendor now."
                )

                # Notify vendor
                from datetime import datetime, timezone
                vendor = get_vendor_for_market("ogbete")
                if vendor:
                    update_order(
                        order["id"],
                        {"vendor_notified_at": datetime.now(timezone.utc).isoformat()}
                    )
                    await send_vendor_order_notification(
                        vendor["whatsapp_number"],
                        order
                    )
                else:
                    # No vendor - notify admin
                    await send_whatsapp_message(
                        ADMIN_WHATSAPP_NUMBER,
                        f"Order #{order['order_number']} paid but no vendor assigned!"
                    )

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Paystack webhook error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
