"""
PriceDeck - Main FastAPI application
Handles WhatsApp webhook for commodity price intelligence
"""
import logging
import asyncio
from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.responses import PlainTextResponse, HTMLResponse
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
    get_vendor_for_market,
    get_user_orders,
    get_logistics_for_market,
    get_pickup_agent_for_market,
    get_vendor_with_location,
    get_or_create_user,
    get_user
)
from app.config import DELIVERY_FEE, ADMIN_WHATSAPP_NUMBER
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

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

# Commodity image URLs (hosted on Supabase Storage)
COMMODITY_IMAGE_BASE_URL = "https://tctepjqsumnnofvpytqe.supabase.co/storage/v1/object/public/commodity-images"
COMMODITY_IMAGES = {
    "beans_brown": f"{COMMODITY_IMAGE_BASE_URL}/beans_brown.jpg",
    "beans_white": f"{COMMODITY_IMAGE_BASE_URL}/beans_white.jpg",
    "beans_iron": f"{COMMODITY_IMAGE_BASE_URL}/beans_brown.jpg",    # Use brown beans image
    "rice": f"{COMMODITY_IMAGE_BASE_URL}/rice.jpg",
    "rice_local": f"{COMMODITY_IMAGE_BASE_URL}/rice.jpg",
    "rice_foreign": f"{COMMODITY_IMAGE_BASE_URL}/rice.jpg",
    "garri_white": f"{COMMODITY_IMAGE_BASE_URL}/garri_white.jpg",
    "garri_yellow": f"{COMMODITY_IMAGE_BASE_URL}/garri_yellow.jpg",
    # Proteins
    "chicken": f"{COMMODITY_IMAGE_BASE_URL}/chicken.jpg",
    "egg": f"{COMMODITY_IMAGE_BASE_URL}/egg.jpg",
    "beef": f"{COMMODITY_IMAGE_BASE_URL}/beef.jpg",
    "goat_meat": f"{COMMODITY_IMAGE_BASE_URL}/goat_meat.jpg",
    # Seafood
    "frozen_fish": f"{COMMODITY_IMAGE_BASE_URL}/frozen_fish.jpg",
    "dry_fish": f"{COMMODITY_IMAGE_BASE_URL}/dry_fish.jpg",
    "stockfish": f"{COMMODITY_IMAGE_BASE_URL}/stockfish.jpg",
    "smoked_fish": f"{COMMODITY_IMAGE_BASE_URL}/smoked_fish.jpg",
    "crayfish": f"{COMMODITY_IMAGE_BASE_URL}/crayfish.jpg",

    # Vegetables
    "tomatoes": f"{COMMODITY_IMAGE_BASE_URL}/tomatoes.jpg",
    "pepper": f"{COMMODITY_IMAGE_BASE_URL}/pepper.jpg",
    "onions": f"{COMMODITY_IMAGE_BASE_URL}/onions.jpg",
    "ugu_leaf": f"{COMMODITY_IMAGE_BASE_URL}/ugu_leaf.jpg",
    "bitter_leaf": f"{COMMODITY_IMAGE_BASE_URL}/bitter_leaf.jpg",
    "oha_leaf": f"{COMMODITY_IMAGE_BASE_URL}/oha_leaf.jpg",
    # oils
    "palm_oil": f"{COMMODITY_IMAGE_BASE_URL}/palm_oil.jpg",
    "kings_oil": f"{COMMODITY_IMAGE_BASE_URL}/kings_oil.jpg",
    # Spices & Pasta
    "kitchen_glory": f"{COMMODITY_IMAGE_BASE_URL}/kitchen_glory.jpg",
    "gino_curry": f"{COMMODITY_IMAGE_BASE_URL}/gino_curry.jpg",
    "gino_tomato": f"{COMMODITY_IMAGE_BASE_URL}/gino_tomato.jpg",
    "maggi_knorr": f"{COMMODITY_IMAGE_BASE_URL}/maggi_knorr.jpg",
    "salt": f"{COMMODITY_IMAGE_BASE_URL}/salt.jpg",
    "indomie": f"{COMMODITY_IMAGE_BASE_URL}/indomie.jpg",
    "titus_sardine": f"{COMMODITY_IMAGE_BASE_URL}/titus_sardine.jpg",
    "spaghetti": f"{COMMODITY_IMAGE_BASE_URL}/spaghetti.jpg",
    
}

# =====================================================
# CATEGORY & ITEM DEFINITIONS
# =====================================================

# Categories for commodity organization
CATEGORIES = [
    {"id": "cat_grains", "title": "Grains", "description": "Rice, Garri, Beans"},
    {"id": "cat_proteins", "title": "Proteins", "description": "Meat, Chicken, Eggs"},
    {"id": "cat_seafood", "title": "Seafood", "description": "Fish, Crayfish"},
    {"id": "cat_vegetables", "title": "Vegetables", "description": "Fresh vegetables & leaves"},
    {"id": "cat_oils", "title": "Oils", "description": "Palm Oil, Vegetable Oil"},
    {"id": "cat_spices_pasta", "title": "Spices & Pasta", "description": "Seasonings, Pasta, Noodles"},
]

# Items within each category
CATEGORY_ITEMS = {
    "grains": [
        {"id": "rice", "title": "Rice", "description": "Local, Foreign"},
        {"id": "garri", "title": "Garri", "description": "White, Yellow"},
        {"id": "beans", "title": "Beans", "description": "Brown, Iron"},
    ],
    "proteins": [
        {"id": "beef", "title": "Beef", "description": "Cow meat (per kg)"},
        {"id": "goat_meat", "title": "Goat Meat", "description": "Per kg"},
        {"id": "chicken", "title": "Chicken", "description": "Per kg"},
        {"id": "egg", "title": "Egg", "description": "Crate, Half Crate"},
    ],
    "seafood": [
        {"id": "crayfish", "title": "Crayfish", "description": "Dried crayfish"},
        {"id": "stockfish", "title": "Stockfish", "description": "Per pack"},
        {"id": "dry_fish", "title": "Dry Fish", "description": "Per piece"},
        {"id": "frozen_fish", "title": "Frozen Fish", "description": "Per piece"},
        {"id": "smoked_fish", "title": "Smoked Fish", "description": "Per piece"},
    ],
    "vegetables": [
        {"id": "tomatoes", "title": "Tomatoes", "description": "Per portion"},
        {"id": "pepper", "title": "Pepper", "description": "Per portion"},
        {"id": "onions", "title": "Onions", "description": "Per portion"},
        {"id": "ugu_leaf", "title": "Ugu Leaf", "description": "Per bunch"},
        {"id": "bitter_leaf", "title": "Bitter Leaf", "description": "Per portion"},
        {"id": "oha_leaf", "title": "Oha Leaf", "description": "Per bunch"},
    ],
    "oils": [
        {"id": "palm_oil", "title": "Palm Oil", "description": "Per litre"},
        {"id": "kings_oil", "title": "Kings Oil", "description": "Vegetable oil (1L, 5L)"},
    ],
    "spices_pasta": [
        {"id": "maggi_knorr", "title": "Maggi Knorr", "description": "Per pack"},
        {"id": "salt", "title": "Salt", "description": "500g"},
        {"id": "spaghetti", "title": "Spaghetti", "description": "Per piece"},
        {"id": "indomie", "title": "Indomie", "description": "Per carton"},
        {"id": "kitchen_glory", "title": "Kitchen Glory", "description": "Per sachet"},
        {"id": "gino_curry", "title": "Gino Curry Powder", "description": "Per sachet"},
        {"id": "gino_tomato", "title": "Gino Tomato Paste", "description": "Sachet, Rolls"},
        {"id": "titus_sardine", "title": "Titus Sardine", "description": "Per piece"},
    ],
}

# Units for each commodity (for items with multiple units)
COMMODITY_UNITS = {
    # Grains
    "rice": ["paint", "bag_25kg", "bag_10kg"],
    "garri": ["paint", "half_paint"],
    "beans": ["paint", "half_paint"],
    # Proteins
    "beef": ["kg"],
    "goat_meat": ["kg"],
    "chicken": ["kg"],
    "egg": ["crate", "half_crate"],  # Direct price display (no variety)
    # Seafood
    "crayfish": ["paint", "half_paint", "portion"],
    "stockfish": ["pack"],
    "dry_fish": ["piece"],
    "frozen_fish": ["piece"],
    "smoked_fish": ["piece"],
    # Vegetables
    "tomatoes": ["portion"],
    "pepper": ["portion"],
    "onions": ["portion"],
    "ugu_leaf": ["bunch"],
    "bitter_leaf": ["portion"],
    "oha_leaf": ["bunch"],
    # Oils
    "palm_oil": ["litre"],
    "kings_oil": ["1_litre", "5_litre"],
    # Spices & Pasta
    "maggi_knorr": ["pack"],
    "salt": ["500g"],
    "spaghetti": ["piece"],
    "indomie": ["carton"],
    "kitchen_glory": ["sachet"],
    "gino_curry": ["sachet"],
    "gino_tomato": ["sachet", "rolls"],
    "titus_sardine": ["piece"],
}

# Items that have varieties (sub-types) - requires variety button selection
# Note: garri, rice, beans, egg now have direct price display (no variety selection step)
# This list is now empty - all items show prices directly
ITEMS_WITH_VARIETIES = []

# Commodity to category mapping for "Back to Category" buttons
COMMODITY_CATEGORY = {
    # Grains
    "rice": "grains", "garri": "grains", "beans": "grains",
    "rice_local": "grains", "rice_foreign": "grains",
    "garri_white": "grains", "garri_yellow": "grains",
    "beans_brown": "grains", "beans_iron": "grains",
    # Proteins
    "beef": "proteins", "goat_meat": "proteins", "chicken": "proteins", "egg": "proteins",
    # Seafood
    "crayfish": "seafood", "stockfish": "seafood", "dry_fish": "seafood",
    "frozen_fish": "seafood", "smoked_fish": "seafood",
    # Vegetables
    "tomatoes": "vegetables", "pepper": "vegetables", "onions": "vegetables",
    "ugu_leaf": "vegetables", "bitter_leaf": "vegetables", "oha_leaf": "vegetables",
    # Oils
    "palm_oil": "oils", "kings_oil": "oils",
    # Spices & Pasta
    "maggi_knorr": "spices_pasta", "salt": "spices_pasta", "spaghetti": "spices_pasta",
    "indomie": "spices_pasta", "kitchen_glory": "spices_pasta", "gino_curry": "spices_pasta",
    "gino_tomato": "spices_pasta", "titus_sardine": "spices_pasta",
}

# Category display names for buttons
CATEGORY_DISPLAY_NAMES = {
    "grains": "Grains",
    "proteins": "Proteins",
    "seafood": "Seafood",
    "vegetables": "Vegetables",
    "oils": "Oils",
    "spices_pasta": "Spices & Pasta",
}

def get_commodity_category(commodity: str) -> str:
    """Get the category for a commodity."""
    return COMMODITY_CATEGORY.get(commodity)

def get_category_display_name(category: str) -> str:
    """Get display name for a category."""
    return CATEGORY_DISPLAY_NAMES.get(category, category.replace("_", " ").title())

def get_commodity_image(commodity: str) -> str:
    """Get image URL for a commodity, returns None if no image available."""
    return COMMODITY_IMAGES.get(commodity)

# Message deduplication - prevents processing the same message twice
# Key: message_id, Value: timestamp when processed
processed_message_ids = {}
MAX_PROCESSED_CACHE = 1000  # Keep last 1000 message IDs

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
    global MARKETS_CACHE

    logger.info("Starting PriceDeck application...")

    try:
        # Validate configuration
        validate_config()
        logger.info("✅ Configuration validated successfully")

        # Load markets from database into cache (only Ogbete for now)
        all_markets = get_all_active_markets()
        MARKETS_CACHE = [m for m in all_markets if m.get("slug") == "ogbete_main"]
        logger.info(f"✅ Loaded {len(MARKETS_CACHE)} active markets into cache (Ogbete only)")

        logger.info("✅ PriceDeck is ready to receive messages")

    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")
        raise


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

                # Get sender's phone number from first message to save/update user
                if messages:
                    sender_phone = messages[0].get("from")
                    if sender_phone:
                        get_or_create_user(sender_phone, user_name)

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

                        # Check for vendor text reply (CONFIRM/REJECT order_number)
                        message_upper = message_body.strip().upper()
                        if message_upper.startswith("CONFIRM ") or message_upper.startswith("REJECT "):
                            # Check if sender is a vendor
                            if is_vendor(from_number):
                                parts = message_body.strip().split(maxsplit=1)
                                if len(parts) == 2:
                                    action = parts[0].upper()  # CONFIRM or REJECT
                                    order_number = parts[1].strip().upper()  # e.g., PD-ABC123

                                    # Get order by order number
                                    from app.database import get_order_by_number
                                    order = get_order_by_number(order_number)

                                    if order:
                                        if order["status"] == "paid_awaiting_vendor":
                                            # Process the vendor response
                                            response_type = "confirmed" if action == "CONFIRM" else "rejected"
                                            await handle_vendor_order_response(from_number, order["id"], response_type)
                                            continue  # Skip Claude processing
                                        else:
                                            await send_whatsapp_message(
                                                from_number,
                                                f"Order #{order_number} is no longer awaiting confirmation.\nCurrent status: {order['status']}"
                                            )
                                            continue
                                    else:
                                        await send_whatsapp_message(
                                            from_number,
                                            f"Order #{order_number} not found. Please check the order number and try again."
                                        )
                                        continue

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
                            # Get commodity from partial data for commodity-specific units
                            partial = partial_price_reports.get(from_number, {})
                            commodity = partial.get("commodity")
                            await send_unit_list(from_number, commodity)
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
                        elif response_text.startswith("__PAYMENT_LINK__|"):
                            # Format: __PAYMENT_LINK__|url|order_number
                            parts = response_text.split("|")
                            if len(parts) >= 3:
                                url = parts[1]
                                order_number = parts[2]
                                await send_payment_link(from_number, url, order_number)
                        elif response_text.startswith("__CHECK_PRICE_UNIT__:"):
                            # Format: __CHECK_PRICE_UNIT__:commodity|commodity_display
                            marker_data = response_text.split(":")[1]
                            parts = marker_data.split("|")
                            commodity = parts[0]
                            commodity_display = parts[1] if len(parts) > 1 else commodity
                            await send_check_price_unit_buttons(from_number, commodity_display, commodity)
                        elif response_text == "__MY_ORDERS__":
                            await send_my_orders(from_number)
                        elif response_text == "__HELP__":
                            help_text = (
                                "*PriceDeck Help*\n\n"
                                "*Commands:*\n"
                                "/orders - View your orders\n"
                                "/cart - View your cart\n"
                                "/online - Check in for pickups (agents)\n"
                                "/help - Show this help\n"
                                "/support - Contact support\n\n"
                                "*Tips:*\n"
                                "• Check prices for commodities at Ogbete Market\n"
                                "• Add items to cart and checkout\n"
                                "• Track your orders anytime"
                            )
                            await send_whatsapp_message(from_number, help_text)
                            await send_main_menu(from_number, welcome=False)
                        elif response_text == "__AGENT_ONLINE__":
                            # Check if user is a pickup agent
                            agent = get_pickup_agent_for_market("ogbete_main")
                            if agent and agent.get("whatsapp_number") == from_number:
                                await send_whatsapp_message(
                                    from_number,
                                    "✅ You're online for today!\n\n"
                                    "You'll receive pickup notifications for Ogbete Market orders."
                                )
                            elif is_user_contributor(from_number):
                                await send_whatsapp_message(
                                    from_number,
                                    "You're a verified contributor but not assigned as a pickup agent.\n\n"
                                    "Contact admin to be assigned."
                                )
                            else:
                                await send_whatsapp_message(
                                    from_number,
                                    "This command is for pickup agents only."
                                )
                            await send_main_menu(from_number, welcome=False)
                        elif response_text == "__SUPPORT__":
                            support_text = (
                                "*Need Help?*\n\n"
                                f"Contact our support team on WhatsApp:\n"
                                f"wa.me/{ADMIN_WHATSAPP_NUMBER}\n\n"
                                "We're here to help!"
                            )
                            await send_whatsapp_message(from_number, support_text)
                            await send_main_menu(from_number, welcome=False)
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

                            # Check if it's a category or commodity selection (from list)
                            elif selected_id.startswith("check_") or selected_id.startswith("report_"):
                                # Determine action
                                if selected_id.startswith("check_"):
                                    action = "check_price"
                                    selection = selected_id.replace("check_", "")
                                else:
                                    action = "report_price"
                                    selection = selected_id.replace("report_", "")

                                user_action_context[from_number] = action

                                # Check if it's a category selection (e.g., cat_grains, cat_proteins)
                                if selection.startswith("cat_"):
                                    category = selection.replace("cat_", "")
                                    await send_category_items(from_number, category, "check" if action == "check_price" else "report")

                                # Handle item selection
                                else:
                                    commodity = selection

                                    # Handle CHECK PRICE flow
                                    if action == "check_price":
                                        # Direct price display for grains (no variety selection)
                                        if commodity == "garri":
                                            await send_garri_prices(from_number, "paint")
                                        elif commodity == "beans":
                                            await send_beans_prices(from_number, "paint")
                                        elif commodity == "rice":
                                            await send_rice_prices(from_number, "paint")
                                        # Direct price display for egg (crate/half_crate)
                                        elif commodity == "egg":
                                            await send_egg_prices_direct(from_number)
                                        # Direct price display for crayfish (all units)
                                        elif commodity == "crayfish":
                                            await send_crayfish_prices(from_number)
                                        # Direct price display for kings_oil (all units)
                                        elif commodity == "kings_oil":
                                            await send_kings_oil_prices(from_number)
                                        # Direct price display for gino_tomato (all units)
                                        elif commodity == "gino_tomato":
                                            await send_gino_tomato_prices(from_number)
                                        # Items with varieties (none currently)
                                        elif commodity in ITEMS_WITH_VARIETIES:
                                            await send_variety_buttons(from_number, commodity)
                                        # Items with single unit - show price directly
                                        elif commodity in COMMODITY_UNITS and len(COMMODITY_UNITS.get(commodity, [])) == 1:
                                            await send_single_unit_price(from_number, commodity)
                                        else:
                                            # Default flow
                                            partial_price_reports[from_number] = {
                                                "commodity": commodity,
                                                "action": "check_price",
                                                "awaiting": "check_unit"
                                            }
                                            commodity_display = commodity.replace("_", " ").title()
                                            await send_check_price_unit_buttons(from_number, commodity_display, commodity)
                                    else:
                                        # REPORT PRICE flow
                                        if commodity == "garri":
                                            await send_variety_buttons(from_number, commodity)
                                        elif commodity == "beans":
                                            await send_variety_buttons(from_number, commodity)
                                        elif commodity == "rice":
                                            await send_variety_buttons(from_number, commodity)
                                        elif commodity in ITEMS_WITH_VARIETIES:
                                            await send_variety_buttons(from_number, commodity)
                                        else:
                                            partial_price_reports[from_number] = {
                                                "commodity": commodity,
                                                "awaiting": "unit"
                                            }
                                            await send_unit_list(from_number, commodity)

                            # Check if it's a unit selection
                            elif selected_id in ["paint", "half_paint", "bag", "half_bag", "kg", "crate", "half_crate", "litre", "portion", "other_unit"]:
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


                            # Cart add from interactive list (new flow)
                            elif selected_id.startswith("cart_add|"):
                                # Format: cart_add|{commodity}|{unit}|{price}
                                parts = selected_id.split("|")
                                if len(parts) >= 4:
                                    commodity = parts[1]
                                    unit = parts[2]
                                    price = float(parts[3])
                                    response_text = await handle_add_to_cart(from_number, commodity, unit, price)
                                    if "__AFTER_ACTION__" in response_text:
                                        message_part = response_text.replace("__AFTER_ACTION__", "").strip()
                                        await send_whatsapp_message(from_number, message_part)
                                        await send_main_menu(from_number, welcome=False)
                                    else:
                                        await send_whatsapp_message(from_number, response_text)

                            # Rice paint prices from list selection
                            elif selected_id == "rice_paint":
                                await send_rice_prices(from_number, "paint")

                            # View cart from list selection
                            elif selected_id == "view_cart":
                                await send_cart_summary(from_number)

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
                                await send_commodity_list(from_number, "check")

                            elif button_id == "menu_report_price":
                                # Check contributor status
                                if is_user_contributor(from_number):
                                    user_action_context[from_number] = "report_price"
                                    await send_commodity_list(from_number, "report")
                                else:
                                    await send_contributor_onboarding(from_number)

                            elif button_id == "my_orders":
                                await send_my_orders(from_number)

                            # Check price unit selection buttons (Paint, Bag, Half Bag)
                            elif button_id.startswith("check_unit_"):
                                unit = button_id.replace("check_unit_", "")

                                # Check if this is rice + bag - redirect to combined bag prices
                                partial = partial_price_reports.get(from_number, {})
                                commodity = partial.get("commodity", "")
                                if commodity.startswith("rice_") and unit == "bag":
                                    # Show all rice bag prices (both varieties)
                                    await send_rice_bag_prices(from_number)
                                else:
                                    # Normal flow
                                    from app.claude_tasks import handle_check_price_unit_selection
                                    response_text = await handle_check_price_unit_selection(from_number, unit)
                                    if "__ADD_TO_CART__:" in response_text:
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
                                    elif response_text == "__MAIN_MENU__":
                                        await send_main_menu(from_number, welcome=True)
                                    else:
                                        await send_whatsapp_message(from_number, response_text)

                            # Commodity selection buttons (check flow)
                            elif button_id.startswith("check_"):
                                commodity = button_id.replace("check_", "")
                                user_action_context[from_number] = "check_price"
                                # Direct price display for grains (no variety selection)
                                if commodity == "garri":
                                    await send_garri_prices(from_number, "paint")
                                elif commodity == "beans":
                                    await send_beans_prices(from_number, "paint")
                                elif commodity == "rice":
                                    await send_rice_prices(from_number, "paint")

                            # Commodity selection buttons (report flow)
                            elif button_id.startswith("report_"):
                                commodity = button_id.replace("report_", "")
                                user_action_context[from_number] = "report_price"
                                # Report flow still needs variety selection
                                if commodity in ["garri", "rice", "beans"]:
                                    await send_variety_buttons(from_number, commodity)



                            # Grain unit toggle (garri/beans - paint vs half_paint)
                            elif button_id.startswith("grain_unit|"):
                                # Format: grain_unit|{grain_type}|{unit}
                                parts = button_id.split("|")
                                if len(parts) >= 3:
                                    grain_type = parts[1]
                                    unit = parts[2]
                                    if grain_type == "garri":
                                        await send_garri_prices(from_number, unit)
                                    elif grain_type == "beans":
                                        await send_beans_prices(from_number, unit)

                            # Rice unit toggle (paint vs bag prices)
                            elif button_id == "rice_bags":
                                await send_rice_bag_prices(from_number)

                            elif button_id == "rice_paint":
                                await send_rice_prices(from_number, "paint")

                            # Back to category buttons
                            elif button_id.startswith("back_cat_"):
                                category = button_id.replace("back_cat_", "")
                                user_action_context[from_number] = "check_price"
                                await send_category_items(from_number, category, "check")

                            # Cart add from buttons (new grain/rice flow)
                            elif button_id.startswith("cart_add|"):
                                # Format: cart_add|{commodity}|{unit}|{price}
                                parts = button_id.split("|")
                                if len(parts) >= 4:
                                    commodity = parts[1]
                                    unit = parts[2]
                                    price = float(parts[3])
                                    response_text = await handle_add_to_cart(from_number, commodity, unit, price)
                                    if "__AFTER_ACTION__" in response_text:
                                        message_part = response_text.replace("__AFTER_ACTION__", "").strip()
                                        await send_whatsapp_message(from_number, message_part)
                                        await send_main_menu(from_number, welcome=False)
                                    else:
                                        await send_whatsapp_message(from_number, response_text)

                            # Cart buttons
                            elif button_id.startswith("add_to_cart|"):
                                # Format: add_to_cart|{commodity}|{unit}|{price}
                                parts = button_id.split("|")
                                if len(parts) >= 4:
                                    commodity = parts[1]
                                    unit = parts[2]
                                    price = parts[3]
                                    response_text = await handle_add_to_cart(from_number, commodity, unit, float(price))
                                else:
                                    # Fallback
                                    response_text = "Something went wrong. Please try again.\n\n__AFTER_ACTION__"
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
                                # Show commodity list directly (skip main menu)
                                user_action_context[from_number] = "check_price"
                                await send_commodity_list(from_number, "check")

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
                                    if response_text.startswith("__PAYMENT_LINK__|"):
                                        parts = response_text.split("|")
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

                            # Agent/Contributor buttons
                            elif button_id.startswith("agent_collected_"):
                                order_id = button_id.replace("agent_collected_", "")
                                await handle_agent_collected(from_number, order_id)

                            elif button_id.startswith("agent_handedover_"):
                                order_id = button_id.replace("agent_handedover_", "")
                                await handle_agent_handedover(from_number, order_id)

                            # Logistics buttons
                            elif button_id.startswith("logistics_pickedup_"):
                                order_id = button_id.replace("logistics_pickedup_", "")
                                await handle_logistics_pickedup(from_number, order_id)

                            elif button_id.startswith("logistics_delivered_"):
                                order_id = button_id.replace("logistics_delivered_", "")
                                await handle_logistics_delivered(from_number, order_id)

                            # Variety selection (garri_white, rice_local, etc.) - used for report price flow
                            else:
                                variety_prefixes = ["garri_", "rice_", "beans_"]
                                if any(button_id.startswith(prefix) for prefix in variety_prefixes):
                                    action = user_action_context.get(from_number, "report_price")

                                    # CHECK PRICE flow - show all unit prices at once
                                    # Note: In the new flow, users don't select varieties for check_price
                                    # This handles edge cases from the old flow
                                    if action == "check_price":
                                        if button_id.startswith("garri_") or button_id.startswith("beans_"):
                                            # Show all unit prices at once
                                            await send_variety_all_prices(from_number, button_id)
                                        elif button_id.startswith("rice_"):
                                            # Rice - show paint and bag options
                                            partial_price_reports[from_number] = {
                                                "commodity": button_id,
                                                "action": "check_price",
                                                "awaiting": "check_unit"
                                            }
                                            commodity_display = button_id.replace("_", " ").title()
                                            await send_check_price_unit_buttons(from_number, commodity_display, button_id)
                                    else:
                                        # REPORT PRICE flow - unchanged
                                        response_text = await handle_variety_selection(from_number, button_id)
                                        if response_text == "__SELECT_UNIT__":
                                            partial = partial_price_reports.get(from_number, {})
                                            commodity = partial.get("commodity")
                                            await send_unit_list(from_number, commodity)
                                        elif response_text == "__SELECT_MARKET__":
                                            await send_market_list(from_number, MARKETS_CACHE)
                                        elif "__ADD_TO_CART__:" in response_text:
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
    # TODO: Remove this line after testing - allows ordering anytime
    return True

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


async def send_image_message(to: str, image_url: str, caption: str = None):
    """
    Send a WhatsApp image message with optional caption.

    Args:
        to: Recipient's WhatsApp number
        image_url: Public URL of the image
        caption: Optional caption text
    """
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        image_payload = {"link": image_url}
        if caption:
            image_payload["caption"] = caption

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "image",
            "image": image_payload
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Image sent to {to}")
                return True
            else:
                logger.error(f"❌ Failed to send image: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending image: {e}")
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


async def send_unit_list(to: str, commodity: str = None):
    """
    Send unit selection - uses buttons for ≤3 units, list for more.
    Units are commodity-specific when commodity is provided.

    Args:
        to: Recipient's WhatsApp number
        commodity: Optional commodity slug for commodity-specific units
    """
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        # Determine base commodity for unit selection
        base_commodity = commodity.split("_")[0] if commodity else ""

        # Commodity-specific units - use buttons for ≤3 units
        if base_commodity == "palm" or commodity == "palm_oil":
            # Only 1 real unit - use button
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": "What unit for Red Oil?"},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": "litre", "title": "Litre"}}
                        ]
                    }
                }
            }
        elif commodity in ["beef", "goat_meat"]:
            # Only 1 real unit - use button
            commodity_display = commodity.replace("_", " ").title()
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": f"What unit for {commodity_display}?"},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": "kg", "title": "Kg"}}
                        ]
                    }
                }
            }
        elif commodity == "egg":
            # 2 units - use buttons
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": "What unit for Egg?"},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": "crate", "title": "Crate"}},
                            {"type": "reply", "reply": {"id": "half_crate", "title": "Half Crate"}}
                        ]
                    }
                }
            }
        elif base_commodity == "crayfish" or commodity == "crayfish":
            # 3 units - use buttons
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": "What unit for Crayfish?"},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": "paint", "title": "Paint"}},
                            {"type": "reply", "reply": {"id": "half_paint", "title": "Half Paint"}},
                            {"type": "reply", "reply": {"id": "portion", "title": "Portion"}}
                        ]
                    }
                }
            }
        else:
            # Default units for garri, rice, beans - use list (6 items)
            units = [
                {"id": "paint", "title": "Paint", "description": "Standard paint bucket"},
                {"id": "half_paint", "title": "Half Paint", "description": "Half paint bucket"},
                {"id": "bag", "title": "Bag", "description": "Full bag (50kg)"},
                {"id": "half_bag", "title": "Half Bag", "description": "Half bag (25kg)"},
                {"id": "kg", "title": "Kg", "description": "Per kilogram"},
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
# Note: garri, rice, beans, egg now have direct price display (no variety selection)
# This is kept for report price flow where variety selection is still needed
COMMODITY_VARIETIES = {
    "garri": [
        {"id": "garri_white", "title": "White"},
        {"id": "garri_yellow", "title": "Yellow"}
    ],
    "rice": [
        {"id": "rice_local", "title": "Local"},
        {"id": "rice_foreign", "title": "Foreign"}
    ],
    "beans": [
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
    Regular users: Check Price, View Cart, My Orders
    Verified contributors: Check Price, Report Price, My Orders

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
            # Get user's name for personalized greeting
            user = get_user(to)
            user_name = user.get("name") if user else None
            greeting = f"Hi {user_name}! " if user_name else ""

            body_text = (
                f"{greeting}Welcome to *PriceDeck*\n\n"
                "Your real-time guide to food item market prices in Enugu.\n\n"
                "What would you like to do?"
            )
        else:
            body_text = "What would you like to do next?"

        # Check if user is a verified contributor
        is_contributor = is_user_contributor(to)

        if is_contributor:
            # Verified contributors: Check Price + Report Price + My Orders
            buttons = [
                {"type": "reply", "reply": {"id": "menu_check_price", "title": "Check Price"}},
                {"type": "reply", "reply": {"id": "menu_report_price", "title": "Report Price"}},
                {"type": "reply", "reply": {"id": "my_orders", "title": "My Orders"}}
            ]
        else:
            # Regular users: Check Price + View Cart + My Orders
            buttons = [
                {"type": "reply", "reply": {"id": "menu_check_price", "title": "Check Price"}},
                {"type": "reply", "reply": {"id": "view_cart", "title": "View Cart"}},
                {"type": "reply", "reply": {"id": "my_orders", "title": "My Orders"}}
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


async def send_commodity_list(to: str, action: str):
    """
    Send category selection as interactive list (renamed from commodity list).
    Shows categories first, then items within selected category.

    Args:
        to: Recipient's WhatsApp number
        action: "check" or "report" - determines list item ID prefix
    """
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        # Build category rows with action prefix
        categories = []
        for cat in CATEGORIES:
            categories.append({
                "id": f"{action}_{cat['id']}",
                "title": cat["title"],
                "description": cat["description"]
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
                    "text": "Select Category"
                },
                "body": {
                    "text": "What would you like to check?"
                },
                "action": {
                    "button": "Choose Category",
                    "sections": [
                        {
                            "title": "Categories",
                            "rows": categories
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
                logger.info(f"✅ Category list sent to {to} for {action}")
                return True
            else:
                logger.error(f"❌ Failed to send category list: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending category list: {e}")
        return False


async def send_category_items(to: str, category: str, action: str):
    """
    Send items within a selected category as interactive list.

    Args:
        to: Recipient's WhatsApp number
        category: Category key (e.g., "grains", "proteins")
        action: "check" or "report" - determines list item ID prefix
    """
    try:
        items = CATEGORY_ITEMS.get(category, [])
        if not items:
            logger.error(f"No items found for category: {category}")
            await send_whatsapp_message(to, "Category not found. Please try again.")
            return False

        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        # Build item rows with action prefix
        rows = []
        for item in items:
            rows.append({
                "id": f"{action}_{item['id']}",
                "title": item["title"],
                "description": item["description"]
            })

        category_display = category.replace("_", " ").title()

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {
                    "type": "text",
                    "text": f"{category_display}"
                },
                "body": {
                    "text": f"Select an item from {category_display}:"
                },
                "action": {
                    "button": "Choose Item",
                    "sections": [
                        {
                            "title": category_display,
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
                logger.info(f"✅ Category items sent to {to} for {category}")
                return True
            else:
                logger.error(f"❌ Failed to send category items: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending category items: {e}")
        return False


async def send_single_unit_price(to: str, commodity: str):
    """
    Show price for items with only one unit type.
    Directly displays price with image and add to cart button.

    Args:
        to: Recipient's WhatsApp number
        commodity: Commodity slug (e.g., "tomatoes", "beef")
    """
    from app.database import get_prices_by_commodity_and_unit
    from app.claude_tasks import format_price

    try:
        units = COMMODITY_UNITS.get(commodity, [])
        if not units:
            await send_whatsapp_message(to, "Item not found. Please try again.")
            await send_main_menu(to, welcome=False)
            return

        unit = units[0]  # Single unit
        prices = get_prices_by_commodity_and_unit(commodity, unit)

        commodity_display = commodity.replace("_", " ").title()
        unit_display = unit.replace("_", " ").title()

        if not prices:
            await send_whatsapp_message(to, f"No {commodity_display} prices available yet.\n\n")
            await send_main_menu(to, welcome=False)
            return

        # Get Ogbete price (or first available)
        ogbete_price = None
        for p in prices:
            if p.get("market") == "ogbete":
                ogbete_price = p.get("price")
                break
        if not ogbete_price and prices:
            ogbete_price = prices[0].get("price")

        price_display = format_price(ogbete_price)

        # Get emoji based on category
        category = get_commodity_category(commodity)
        category_emojis = {
            "vegetables": "🥬",
            "proteins": "🍖",
            "seafood": "🐟",
            "oils": "🫒",
            "spices_pasta": "🧂",
            "grains": "🌾"
        }
        emoji = category_emojis.get(category, "📦")

        # Build formatted message
        message = f"{emoji} *{commodity_display} Price*\n\n₦{price_display} per {unit_display}\n\n_Prices from Ogbete Market_"

        # Send image with prices if available
        image_url = get_commodity_image(commodity)
        if image_url:
            await send_image_message(to, image_url, message)
        else:
            await send_whatsapp_message(to, message)

        await asyncio.sleep(0.5)

        # Send add to cart button if market is open
        if ogbete_price and is_market_open():
            await send_add_to_cart_buttons(to, commodity, str(ogbete_price), unit)
        else:
            await send_whatsapp_message(to, "_Shopping available 8am - 4pm daily._")
            await send_main_menu(to, welcome=False)

    except Exception as e:
        logger.error(f"Error sending single unit price for {commodity}: {e}")
        await send_whatsapp_message(to, "Error fetching prices. Please try again.")
        await send_main_menu(to, welcome=False)


async def send_contributor_onboarding(to: str):
    """Send message explaining how to become a contributor"""
    message = (
        "Price reporting is for verified contributors.\n\n"
        "Contributors are traders, vendors, and regular shoppers "
        "who help keep prices accurate.\n\n"
        "Want to join? Reply *JOIN* to apply."
    )
    await send_whatsapp_message(to, message)


async def send_check_price_unit_buttons(to: str, commodity_display: str, commodity: str = None):
    """
    Send unit selection buttons for price checking.
    Units are commodity-specific.

    Args:
        to: Recipient's WhatsApp number
        commodity_display: Display name of commodity (e.g., "White Garri")
        commodity: Commodity slug (e.g., "garri_white", "palm_oil")
    """
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        # Determine base commodity for unit selection
        base_commodity = commodity.split("_")[0] if commodity else ""

        # Commodity-specific units
        if base_commodity == "palm" or commodity == "palm_oil":
            buttons = [
                {"type": "reply", "reply": {"id": "check_unit_litre", "title": "Litre"}}
            ]
        elif base_commodity == "crayfish" or commodity == "crayfish":
            buttons = [
                {"type": "reply", "reply": {"id": "check_unit_paint", "title": "Paint"}},
                {"type": "reply", "reply": {"id": "check_unit_half_paint", "title": "Half Paint"}},
                {"type": "reply", "reply": {"id": "check_unit_portion", "title": "Portion"}}
            ]
        elif commodity == "egg":
            buttons = [
                {"type": "reply", "reply": {"id": "check_unit_crate", "title": "Crate"}},
                {"type": "reply", "reply": {"id": "check_unit_half_crate", "title": "Half Crate"}}
            ]
        else:
            # Default units
            buttons = [
                {"type": "reply", "reply": {"id": "check_unit_paint", "title": "Paint"}},
                {"type": "reply", "reply": {"id": "check_unit_bag", "title": "Bag"}},
                {"type": "reply", "reply": {"id": "check_unit_half_bag", "title": "Half Bag"}}
            ]

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": f"What unit for {commodity_display}?"},
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
                logger.info(f"✅ Check price unit buttons sent to {to}")
                return True
            else:
                logger.error(f"❌ Failed to send check price unit buttons: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending check price unit buttons: {e}")
        return False


# =====================================================
# OPTIMIZED COMMODITY PRICE FLOWS
# =====================================================

async def send_egg_prices_direct(to: str):
    """
    Show egg prices for both crate and half_crate directly (no variety selection).
    """
    from app.database import get_prices_for_commodity_all_units
    from app.claude_tasks import format_price

    try:
        units = ["crate", "half_crate"]
        prices = get_prices_for_commodity_all_units("egg", units, market="ogbete")

        if not prices:
            await send_whatsapp_message(to, "No Egg prices available yet. Be the first to share!")
            await send_main_menu(to, welcome=False)
            return

        # Build price message
        message = "🥚 *Egg Prices*\n\n"
        for unit in units:
            if unit in prices:
                unit_display = "Full Crate" if unit == "crate" else "Half Crate"
                price_display = format_price(prices[unit]["price"])
                message += f"{unit_display}: ₦{price_display}\n"

        message += "\n_Prices from Ogbete Market_"

        # Send image with prices
        image_url = get_commodity_image("egg")
        if image_url:
            await send_image_message(to, image_url, message)
        else:
            await send_whatsapp_message(to, message)

        await asyncio.sleep(0.5)

        # Send buttons for cart
        if is_market_open():
            await send_egg_cart_buttons_direct(to, prices)
        else:
            await send_whatsapp_message(to, "_Shopping available 8am - 4pm daily._")
            await send_main_menu(to, welcome=False)

    except Exception as e:
        logger.error(f"Error sending egg prices: {e}")
        await send_whatsapp_message(to, "Error fetching prices. Please try again.")
        await send_main_menu(to, welcome=False)


async def send_egg_cart_buttons_direct(to: str, prices: dict):
    """Send cart buttons for eggs: Add Full Crate, Add Half Crate, More Proteins."""
    from app.claude_tasks import format_price

    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        buttons = []

        # Add Full Crate button
        if "crate" in prices:
            price = int(prices["crate"]["price"])
            buttons.append({
                "type": "reply",
                "reply": {"id": f"cart_add|egg|crate|{price}", "title": "Add Full Crate"}
            })

        # Add Half Crate button
        if "half_crate" in prices:
            price = int(prices["half_crate"]["price"])
            buttons.append({
                "type": "reply",
                "reply": {"id": f"cart_add|egg|half_crate|{price}", "title": "Add Half Crate"}
            })

        # Add More Proteins button
        buttons.append({
            "type": "reply",
            "reply": {"id": "back_cat_proteins", "title": "More Proteins"}
        })

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": "Add to cart or browse more:"},
                "action": {"buttons": buttons}
            }
        }

        async with httpx.AsyncClient() as client:
            await client.post(WHATSAPP_API_URL, headers=headers, json=payload, timeout=10.0)

    except Exception as e:
        logger.error(f"Error sending egg cart buttons: {e}")


async def send_crayfish_prices(to: str):
    """
    Show crayfish prices for all units (paint, half_paint, portion) directly.
    """
    from app.database import get_prices_for_commodity_all_units
    from app.claude_tasks import format_price

    try:
        units = ["paint", "half_paint", "portion"]
        prices = get_prices_for_commodity_all_units("crayfish", units, market="ogbete")

        if not prices:
            await send_whatsapp_message(to, "No Crayfish prices available yet. Be the first to share!")
            await send_main_menu(to, welcome=False)
            return

        # Build price message
        message = "🦐 *Crayfish Prices*\n\n"
        unit_labels = {"paint": "Paint", "half_paint": "Half Paint", "portion": "Portion"}
        for unit in units:
            if unit in prices:
                price_display = format_price(prices[unit]["price"])
                message += f"{unit_labels[unit]}: ₦{price_display}\n"

        message += "\n_Prices from Ogbete Market_"

        # Send image with prices
        image_url = get_commodity_image("crayfish")
        if image_url:
            await send_image_message(to, image_url, message)
        else:
            await send_whatsapp_message(to, message)

        await asyncio.sleep(0.5)

        # Send cart list (3 units + More Seafood = 4 options, needs list)
        if is_market_open():
            await send_crayfish_cart_list(to, prices)
        else:
            await send_whatsapp_message(to, "_Shopping available 8am - 4pm daily._")
            await send_main_menu(to, welcome=False)

    except Exception as e:
        logger.error(f"Error sending crayfish prices: {e}")
        await send_whatsapp_message(to, "Error fetching prices. Please try again.")
        await send_main_menu(to, welcome=False)


async def send_crayfish_cart_list(to: str, prices: dict):
    """Send interactive list for crayfish cart selection."""
    from app.claude_tasks import format_price

    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        rows = []
        unit_labels = {"paint": "Paint", "half_paint": "Half Paint", "portion": "Portion"}

        for unit in ["paint", "half_paint", "portion"]:
            if unit in prices:
                price = int(prices[unit]["price"])
                price_display = format_price(price)
                rows.append({
                    "id": f"cart_add|crayfish|{unit}|{price}",
                    "title": f"{unit_labels[unit]} - ₦{price_display}",
                    "description": "Add to cart"
                })

        # Add More Seafood option
        rows.append({
            "id": "back_cat_seafood",
            "title": "More Seafood",
            "description": "Browse other seafood"
        })

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": "Select to add to cart:"},
                "action": {
                    "button": "Choose Option",
                    "sections": [{"title": "Crayfish", "rows": rows}]
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(WHATSAPP_API_URL, headers=headers, json=payload, timeout=10.0)
            if response.status_code != 200:
                logger.error(f"Failed to send crayfish cart list: {response.status_code} - {response.text}")

    except Exception as e:
        logger.error(f"Error sending crayfish cart list: {e}")


async def send_kings_oil_prices(to: str):
    """
    Show Kings Oil prices for all units (1_litre, 5_litre) directly.
    """
    from app.database import get_prices_for_commodity_all_units
    from app.claude_tasks import format_price

    try:
        units = ["1_litre", "5_litre"]
        prices = get_prices_for_commodity_all_units("kings_oil", units, market="ogbete")

        if not prices:
            await send_whatsapp_message(to, "No Kings Oil prices available yet. Be the first to share!")
            await send_main_menu(to, welcome=False)
            return

        # Build price message
        message = "🫒 *Kings Oil Prices*\n\n"
        unit_labels = {"1_litre": "1 Litre", "5_litre": "5 Litres"}
        for unit in units:
            if unit in prices:
                price_display = format_price(prices[unit]["price"])
                message += f"{unit_labels[unit]}: ₦{price_display}\n"

        message += "\n_Prices from Ogbete Market_"

        # Send image with prices
        image_url = get_commodity_image("kings_oil")
        if image_url:
            await send_image_message(to, image_url, message)
        else:
            await send_whatsapp_message(to, message)

        await asyncio.sleep(0.5)

        # Send cart buttons (2 units + More Oils = 3 buttons)
        if is_market_open():
            await send_kings_oil_cart_buttons(to, prices)
        else:
            await send_whatsapp_message(to, "_Shopping available 8am - 4pm daily._")
            await send_main_menu(to, welcome=False)

    except Exception as e:
        logger.error(f"Error sending kings oil prices: {e}")
        await send_whatsapp_message(to, "Error fetching prices. Please try again.")
        await send_main_menu(to, welcome=False)


async def send_kings_oil_cart_buttons(to: str, prices: dict):
    """Send cart buttons for Kings Oil: Add 1 Litre, Add 5 Litres, More Oils."""
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        buttons = []

        if "1_litre" in prices:
            price = int(prices["1_litre"]["price"])
            buttons.append({
                "type": "reply",
                "reply": {"id": f"cart_add|kings_oil|1_litre|{price}", "title": "Add 1 Litre"}
            })

        if "5_litre" in prices:
            price = int(prices["5_litre"]["price"])
            buttons.append({
                "type": "reply",
                "reply": {"id": f"cart_add|kings_oil|5_litre|{price}", "title": "Add 5 Litres"}
            })

        buttons.append({
            "type": "reply",
            "reply": {"id": "back_cat_oils", "title": "More Oils"}
        })

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": "Add to cart or browse more:"},
                "action": {"buttons": buttons}
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(WHATSAPP_API_URL, headers=headers, json=payload, timeout=10.0)
            if response.status_code != 200:
                logger.error(f"Failed to send kings oil cart buttons: {response.status_code} - {response.text}")

    except Exception as e:
        logger.error(f"Error sending kings oil cart buttons: {e}")


async def send_gino_tomato_prices(to: str):
    """
    Show Gino Tomato prices for all units (sachet, rolls) directly.
    """
    from app.database import get_prices_for_commodity_all_units
    from app.claude_tasks import format_price

    try:
        units = ["sachet", "rolls"]
        prices = get_prices_for_commodity_all_units("gino_tomato", units, market="ogbete")

        if not prices:
            await send_whatsapp_message(to, "No Gino Tomato prices available yet. Be the first to share!")
            await send_main_menu(to, welcome=False)
            return

        # Build price message
        message = "🍅 *Gino Tomato Prices*\n\n"
        unit_labels = {"sachet": "Sachet", "rolls": "Rolls"}
        for unit in units:
            if unit in prices:
                price_display = format_price(prices[unit]["price"])
                message += f"{unit_labels[unit]}: ₦{price_display}\n"

        message += "\n_Prices from Ogbete Market_"

        # Send message (no image for gino_tomato)
        await send_whatsapp_message(to, message)

        await asyncio.sleep(0.5)

        # Send cart buttons (2 units + More Spices = 3 buttons)
        if is_market_open():
            await send_gino_tomato_cart_buttons(to, prices)
        else:
            await send_whatsapp_message(to, "_Shopping available 8am - 4pm daily._")
            await send_main_menu(to, welcome=False)

    except Exception as e:
        logger.error(f"Error sending gino tomato prices: {e}")
        await send_whatsapp_message(to, "Error fetching prices. Please try again.")
        await send_main_menu(to, welcome=False)


async def send_gino_tomato_cart_buttons(to: str, prices: dict):
    """Send cart buttons for Gino Tomato: Add Sachet, Add Rolls, More Spices."""
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        buttons = []

        if "sachet" in prices:
            price = int(prices["sachet"]["price"])
            buttons.append({
                "type": "reply",
                "reply": {"id": f"cart_add|gino_tomato|sachet|{price}", "title": "Add Sachet"}
            })

        if "rolls" in prices:
            price = int(prices["rolls"]["price"])
            buttons.append({
                "type": "reply",
                "reply": {"id": f"cart_add|gino_tomato|rolls|{price}", "title": "Add Rolls"}
            })

        buttons.append({
            "type": "reply",
            "reply": {"id": "back_cat_spices_pasta", "title": "More Spices"}
        })

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": "Add to cart or browse more:"},
                "action": {"buttons": buttons}
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(WHATSAPP_API_URL, headers=headers, json=payload, timeout=10.0)
            if response.status_code != 200:
                logger.error(f"Failed to send gino tomato cart buttons: {response.status_code} - {response.text}")

    except Exception as e:
        logger.error(f"Error sending gino tomato cart buttons: {e}")


async def send_variety_all_prices(to: str, variety: str):
    """
    Show all unit prices for a variety (garri or beans) at once with image.
    Used for garri_white, garri_yellow, beans_brown, beans_iron, etc.
    Note: This function is now primarily used by the report price flow after variety selection.
    """
    from app.database import get_prices_for_commodity_all_units
    from app.claude_tasks import format_price

    try:
        # Determine which units to show based on commodity type
        base = variety.split("_")[0]
        if base == "garri":
            units = ["paint", "half_paint"]
        elif base == "beans":
            units = ["paint", "half_paint"]
        else:
            units = ["paint", "bag"]

        prices = get_prices_for_commodity_all_units(variety, units)

        variety_display = variety.replace("_", " ").title()

        if not prices:
            await send_whatsapp_message(to, f"No {variety_display} prices available yet. Be the first to share!\n\n")
            await send_main_menu(to, welcome=False)
            return

        # Build price message
        emoji = "🌾" if base == "garri" else "🫘"
        message = f"*{variety_display}* at Ogbete:\n\n"
        for unit in units:
            if unit in prices:
                price_display = format_price(prices[unit]["price"])
                unit_display = unit.replace("_", " ").title()
                message += f"{emoji} {unit_display}: {price_display}\n"

        # Send image with prices (WhatsApp lists don't support image headers)
        image_url = get_commodity_image(variety)
        if image_url:
            await send_image_message(to, image_url, message)
        else:
            await send_whatsapp_message(to, message)

        # Small delay to ensure WhatsApp delivers messages in order
        await asyncio.sleep(0.5)

        # Send interactive list for cart selection
        if is_market_open():
            await send_variety_cart_list(to, variety, prices)
        else:
            await send_whatsapp_message(to, "_Shopping available 8am - 4pm daily at Ogbete Market._")
            await send_main_menu(to, welcome=False)

    except Exception as e:
        logger.error(f"Error sending variety prices: {e}")
        await send_whatsapp_message(to, "Error fetching prices. Please try again.")
        await send_main_menu(to, welcome=False)


async def send_garri_prices(to: str, unit: str = "paint"):
    """
    Show White and Yellow garri prices at once.
    No variety selection - displays both varieties immediately.
    """
    from app.database import get_prices_for_varieties_with_unit
    from app.claude_tasks import format_price

    try:
        varieties = ["garri_white", "garri_yellow"]
        prices = get_prices_for_varieties_with_unit(varieties, unit, market="ogbete")

        unit_display = unit.replace("_", " ").title()

        if not prices:
            await send_whatsapp_message(to, f"No Garri prices available yet. Be the first to share!")
            await send_main_menu(to, welcome=False)
            return

        # Build price message
        message = f"🌾 *Garri Prices ({unit_display})*\n\n"
        for variety in varieties:
            if variety in prices:
                variety_name = "White Garri" if variety == "garri_white" else "Yellow Garri"
                price_display = format_price(prices[variety]["price"])
                message += f"{variety_name}: ₦{price_display}\n"

        message += "\n_Prices from Ogbete Market_"

        # Send image with prices
        image_url = get_commodity_image("garri_white")
        if image_url:
            await send_image_message(to, image_url, message)
        else:
            await send_whatsapp_message(to, message)

        await asyncio.sleep(0.5)

        # Send buttons for cart and alternate unit
        if is_market_open():
            alternate_unit = "half_paint" if unit == "paint" else "paint"
            alternate_label = "Half Paint Prices" if unit == "paint" else "See Paint Prices"
            await send_grain_cart_buttons(to, "garri", prices, unit, alternate_unit, alternate_label)
        else:
            await send_whatsapp_message(to, "_Shopping available 8am - 4pm daily._")
            await send_main_menu(to, welcome=False)

    except Exception as e:
        logger.error(f"Error sending garri prices: {e}")
        await send_whatsapp_message(to, "Error fetching prices. Please try again.")
        await send_main_menu(to, welcome=False)


async def send_beans_prices(to: str, unit: str = "paint"):
    """
    Show Brown and Iron beans prices at once.
    No variety selection - displays both varieties immediately.
    """
    from app.database import get_prices_for_varieties_with_unit
    from app.claude_tasks import format_price

    try:
        varieties = ["beans_brown", "beans_iron"]
        prices = get_prices_for_varieties_with_unit(varieties, unit, market="ogbete")

        unit_display = unit.replace("_", " ").title()

        if not prices:
            await send_whatsapp_message(to, f"No Beans prices available yet. Be the first to share!")
            await send_main_menu(to, welcome=False)
            return

        # Build price message
        message = f"🫘 *Beans Prices ({unit_display})*\n\n"
        for variety in varieties:
            if variety in prices:
                variety_name = "Brown Beans" if variety == "beans_brown" else "Iron Beans"
                price_display = format_price(prices[variety]["price"])
                message += f"{variety_name}: ₦{price_display}\n"

        message += "\n_Prices from Ogbete Market_"

        # Send image with prices
        image_url = get_commodity_image("beans_brown")
        if image_url:
            await send_image_message(to, image_url, message)
        else:
            await send_whatsapp_message(to, message)

        await asyncio.sleep(0.5)

        # Send buttons for cart and alternate unit
        if is_market_open():
            alternate_unit = "half_paint" if unit == "paint" else "paint"
            alternate_label = "Half Paint Prices" if unit == "paint" else "See Paint Prices"
            await send_grain_cart_buttons(to, "beans", prices, unit, alternate_unit, alternate_label)
        else:
            await send_whatsapp_message(to, "_Shopping available 8am - 4pm daily._")
            await send_main_menu(to, welcome=False)

    except Exception as e:
        logger.error(f"Error sending beans prices: {e}")
        await send_whatsapp_message(to, "Error fetching prices. Please try again.")
        await send_main_menu(to, welcome=False)


async def send_rice_prices(to: str, unit: str = "paint"):
    """
    Show Local and Foreign rice prices at once.
    No variety selection - displays both varieties immediately.
    """
    from app.database import get_prices_for_varieties_with_unit
    from app.claude_tasks import format_price

    try:
        varieties = ["rice_local", "rice_foreign"]
        prices = get_prices_for_varieties_with_unit(varieties, unit, market="ogbete")

        unit_display = unit.replace("_", " ").title()

        if not prices:
            await send_whatsapp_message(to, f"No Rice prices available yet. Be the first to share!")
            await send_main_menu(to, welcome=False)
            return

        # Build price message
        message = f"🍚 *Rice Prices ({unit_display})*\n\n"
        for variety in varieties:
            if variety in prices:
                variety_name = "Local Rice" if variety == "rice_local" else "Foreign Rice"
                price_display = format_price(prices[variety]["price"])
                message += f"{variety_name}: ₦{price_display}\n"

        message += "\n_Prices from Ogbete Market_"

        # Send image with prices
        image_url = get_commodity_image("rice_local")
        if image_url:
            await send_image_message(to, image_url, message)
        else:
            await send_whatsapp_message(to, message)

        await asyncio.sleep(0.5)

        # Send buttons for cart and alternate unit
        if is_market_open():
            await send_rice_cart_buttons(to, prices, unit)
        else:
            await send_whatsapp_message(to, "_Shopping available 8am - 4pm daily._")
            await send_main_menu(to, welcome=False)

    except Exception as e:
        logger.error(f"Error sending rice prices: {e}")
        await send_whatsapp_message(to, "Error fetching prices. Please try again.")
        await send_main_menu(to, welcome=False)


async def send_grain_cart_buttons(to: str, grain_type: str, prices: dict, current_unit: str, alternate_unit: str, alternate_label: str):
    """
    Send reply buttons for garri/beans: Add variety1, Add variety2, See alternate prices.
    """
    from app.claude_tasks import format_price

    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        # Determine varieties based on grain type
        if grain_type == "garri":
            variety1, variety2 = "garri_white", "garri_yellow"
            label1, label2 = "Add White", "Add Yellow"
        else:  # beans
            variety1, variety2 = "beans_brown", "beans_iron"
            label1, label2 = "Add Brown", "Add Iron"

        buttons = []

        # Add variety 1 to cart button
        if variety1 in prices:
            price1 = int(prices[variety1]["price"])
            buttons.append({
                "type": "reply",
                "reply": {
                    "id": f"cart_add|{variety1}|{current_unit}|{price1}",
                    "title": label1
                }
            })

        # Add variety 2 to cart button
        if variety2 in prices:
            price2 = int(prices[variety2]["price"])
            buttons.append({
                "type": "reply",
                "reply": {
                    "id": f"cart_add|{variety2}|{current_unit}|{price2}",
                    "title": label2
                }
            })

        # See alternate unit prices button
        buttons.append({
            "type": "reply",
            "reply": {
                "id": f"grain_unit|{grain_type}|{alternate_unit}",
                "title": alternate_label
            }
        })

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": "Add to cart or see other prices:"},
                "action": {"buttons": buttons}
            }
        }

        async with httpx.AsyncClient() as client:
            await client.post(WHATSAPP_API_URL, headers=headers, json=payload, timeout=10.0)

    except Exception as e:
        logger.error(f"Error sending grain cart buttons: {e}")


async def send_rice_cart_buttons(to: str, prices: dict, current_unit: str):
    """
    Send reply buttons for rice: Add Local, Add Foreign, See Bag/Paint Prices.
    """
    from app.claude_tasks import format_price

    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        buttons = []

        # Add Local rice to cart button
        if "rice_local" in prices:
            price_local = int(prices["rice_local"]["price"])
            buttons.append({
                "type": "reply",
                "reply": {
                    "id": f"cart_add|rice_local|{current_unit}|{price_local}",
                    "title": "Add Local"
                }
            })

        # Add Foreign rice to cart button
        if "rice_foreign" in prices:
            price_foreign = int(prices["rice_foreign"]["price"])
            buttons.append({
                "type": "reply",
                "reply": {
                    "id": f"cart_add|rice_foreign|{current_unit}|{price_foreign}",
                    "title": "Add Foreign"
                }
            })

        # Toggle between paint and bag prices
        if current_unit == "paint":
            buttons.append({
                "type": "reply",
                "reply": {
                    "id": "rice_bags",
                    "title": "See Bag Prices"
                }
            })
        else:
            buttons.append({
                "type": "reply",
                "reply": {
                    "id": "rice_paint",
                    "title": "See Paint Prices"
                }
            })

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": "Add to cart or see other prices:"},
                "action": {"buttons": buttons}
            }
        }

        async with httpx.AsyncClient() as client:
            await client.post(WHATSAPP_API_URL, headers=headers, json=payload, timeout=10.0)

    except Exception as e:
        logger.error(f"Error sending rice cart buttons: {e}")


async def send_rice_bag_prices(to: str):
    """
    Show Local and Foreign rice bag prices (25kg, 10kg) at once.
    """
    from app.database import get_prices_for_commodity_all_units
    from app.claude_tasks import format_price

    try:
        bag_units = ["bag_25kg", "bag_10kg"]

        # Get prices for both varieties
        local_prices = get_prices_for_commodity_all_units("rice_local", bag_units, market="ogbete")
        foreign_prices = get_prices_for_commodity_all_units("rice_foreign", bag_units, market="ogbete")

        if not local_prices and not foreign_prices:
            await send_whatsapp_message(to, "No Rice bag prices available yet. Be the first to share!")
            await send_main_menu(to, welcome=False)
            return

        # Build price message
        message = "🍚 *Rice Bag Prices*\n\n"

        if local_prices:
            message += "*Local Rice:*\n"
            for unit in bag_units:
                if unit in local_prices:
                    size = unit.replace("bag_", "").replace("kg", " kg")
                    price_display = format_price(local_prices[unit]["price"])
                    message += f"  {size}: ₦{price_display}\n"
            message += "\n"

        if foreign_prices:
            message += "*Foreign Rice:*\n"
            for unit in bag_units:
                if unit in foreign_prices:
                    size = unit.replace("bag_", "").replace("kg", " kg")
                    price_display = format_price(foreign_prices[unit]["price"])
                    message += f"  {size}: ₦{price_display}\n"

        message += "\n_Prices from Ogbete Market_"

        await send_whatsapp_message(to, message)

        await asyncio.sleep(0.5)

        # Send cart list for bag selection
        if is_market_open():
            await send_rice_bag_cart_list_combined(to, local_prices, foreign_prices)
        else:
            await send_whatsapp_message(to, "_Shopping available 8am - 4pm daily._")
            await send_main_menu(to, welcome=False)

    except Exception as e:
        logger.error(f"Error sending rice bag prices: {e}")
        await send_whatsapp_message(to, "Error fetching prices. Please try again.")
        await send_main_menu(to, welcome=False)


async def send_rice_bag_cart_list_combined(to: str, local_prices: dict, foreign_prices: dict):
    """Send interactive list for rice bag cart selection - both varieties."""
    from app.claude_tasks import format_price

    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        rows = []
        bag_units = ["bag_25kg", "bag_10kg"]

        # Add local rice options
        for unit in bag_units:
            if unit in local_prices:
                price = local_prices[unit]["price"]
                price_display = format_price(price)
                size = unit.replace("bag_", "").replace("kg", " kg")
                rows.append({
                    "id": f"cart_add|rice_local|{unit}|{int(price)}",
                    "title": f"Local {size} - ₦{price_display}",
                    "description": "Add to cart"
                })

        # Add foreign rice options
        for unit in bag_units:
            if unit in foreign_prices:
                price = foreign_prices[unit]["price"]
                price_display = format_price(price)
                size = unit.replace("bag_", "").replace("kg", " kg")
                rows.append({
                    "id": f"cart_add|rice_foreign|{unit}|{int(price)}",
                    "title": f"Foreign {size} - ₦{price_display}",
                    "description": "Add to cart"
                })

        # Add back to paint prices option
        rows.append({
            "id": "rice_paint",
            "title": "See Paint Prices",
            "description": "View paint prices instead"
        })

        # Add view cart option
        rows.append({
            "id": "view_cart",
            "title": "View Cart",
            "description": "See items in your cart"
        })

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": "Select to add to cart:"},
                "action": {
                    "button": "Choose Option",
                    "sections": [{"title": "Rice Bags", "rows": rows}]
                }
            }
        }

        async with httpx.AsyncClient() as client:
            await client.post(WHATSAPP_API_URL, headers=headers, json=payload, timeout=10.0)

    except Exception as e:
        logger.error(f"Error sending rice bag cart list: {e}")


async def send_variety_cart_list(to: str, variety: str, prices: dict):
    """Send interactive list for variety cart selection (garri/beans)."""
    from app.claude_tasks import format_price

    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        rows = []
        for unit, data in prices.items():
            price = data["price"]
            price_display = format_price(price)
            unit_display = unit.replace("_", " ").title()
            rows.append({
                "id": f"cart_add|{variety}|{unit}|{price}",
                "title": f"{unit_display} - {price_display}",
                "description": f"Add {unit_display} to cart"
            })

        rows.append({
            "id": "view_cart",
            "title": "View Cart",
            "description": "See items in your cart"
        })

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": "Select to add to cart:"},
                "action": {
                    "button": "Choose Option",
                    "sections": [{"title": "Add to Cart", "rows": rows}]
                }
            }
        }

        async with httpx.AsyncClient() as client:
            await client.post(WHATSAPP_API_URL, headers=headers, json=payload, timeout=10.0)

    except Exception as e:
        logger.error(f"Error sending variety cart list: {e}")


# =====================================================
# CART & SHOPPING UI FUNCTIONS
# =====================================================

async def send_add_to_cart_buttons(to: str, commodity: str, price: str, unit: str, category: str = None):
    """
    Send Add to Cart, Back to Category, and View Cart buttons after showing prices.
    Only shows Add to Cart during market hours (8am-4pm Nigeria time).

    Args:
        to: Recipient's WhatsApp number
        commodity: Commodity name
        price: Price display string
        unit: Unit name
        category: Optional category for "Back to X" button
    """
    try:
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        # Get category if not provided
        if not category:
            category = get_commodity_category(commodity)

        # Check if market is open
        if is_market_open():
            # Market open - show Add to Cart button
            body_text = (
                f"Want to buy from Ogbete Market?\n\n"
                f"_Shopping available for Ogbete Market only._"
            )

            buttons = [
                {"type": "reply", "reply": {"id": f"add_to_cart|{commodity}|{unit}|{price}", "title": "Add to Cart"}},
            ]

            # Add "Back to Category" button if we have a category
            if category:
                category_display = get_category_display_name(category)
                buttons.append({"type": "reply", "reply": {"id": f"back_cat_{category}", "title": f"More {category_display}"}})

            buttons.append({"type": "reply", "reply": {"id": "view_cart", "title": "View Cart"}})

            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body_text},
                    "action": {"buttons": buttons}
                }
            }
        else:
            # Market closed - show message with Back to Category and View Cart
            body_text = (
                f"*Ordering available 8am - 4pm daily*\n\n"
                f"Vendors are currently closed. Check back during market hours!"
            )

            buttons = []

            # Add "Back to Category" button if we have a category
            if category:
                category_display = get_category_display_name(category)
                buttons.append({"type": "reply", "reply": {"id": f"back_cat_{category}", "title": f"More {category_display}"}})

            buttons.append({"type": "reply", "reply": {"id": "view_cart", "title": "View Cart"}})

            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body_text},
                    "action": {"buttons": buttons}
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
    from app.config import SERVICE_CHARGE_PERCENT, SERVICE_CHARGE_CAP

    try:
        cart_items = get_cart_items(to)
        partial = partial_cart.get(to, {})

        if not cart_items:
            await send_whatsapp_message(to, "Your cart is empty!")
            await send_main_menu(to, welcome=False)
            return

        # Calculate totals
        subtotal = sum(item["quantity"] * item["unit_price"] for item in cart_items)

        # Calculate service charge (10% capped at 3k, rounded down)
        service_charge = int(subtotal * SERVICE_CHARGE_PERCENT)
        service_charge = min(service_charge, SERVICE_CHARGE_CAP)

        delivery_fee = DELIVERY_FEE
        total = subtotal + service_charge + delivery_fee

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
            f"*Service Charge:* {format_price_display(service_charge)}",
            f"*Delivery:* {format_price_display(delivery_fee)}",
            f"━━━━━━━━━━━━━━━",
            f"*Total:* {format_price_display(total)}",
            f"\n⏱️ *Prep time:* 20-30 minutes",
            f"📍 *Deliver to:* {partial.get('delivery_address', 'N/A')}",
            f"📞 *Contact:* {partial.get('contact_phone', to)}",
            "\n_Shopping from Ogbete Market_"
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


async def send_my_orders(to: str):
    """
    Send user's orders grouped by ongoing and completed status

    Args:
        to: Recipient's WhatsApp number
    """
    try:
        orders = get_user_orders(to, limit=10)

        if not orders:
            await send_whatsapp_message(
                to,
                "You don't have any orders yet.\n\n"
                "Check prices and add items to your cart to place an order!"
            )
            await send_main_menu(to, welcome=False)
            return

        # Define status categories
        ongoing_statuses = ["pending_payment", "paid_awaiting_vendor", "vendor_confirmed", "preparing", "out_for_delivery"]
        completed_statuses = ["delivered", "vendor_rejected", "cancelled"]

        # Status display names with icons
        status_display = {
            "pending_payment": "Awaiting payment 💳",
            "paid_awaiting_vendor": "Paid, awaiting vendor ⏳",
            "vendor_confirmed": "Vendor confirmed ✅",
            "preparing": "Preparing 👨‍🍳",
            "out_for_delivery": "Out for delivery 🚚",
            "delivered": "Delivered ✅",
            "vendor_rejected": "Rejected (refund pending) ❌",
            "cancelled": "Cancelled ❌"
        }

        # Separate orders
        ongoing = [o for o in orders if o.get("status") in ongoing_statuses]
        completed = [o for o in orders if o.get("status") in completed_statuses]

        lines = ["*My Orders*\n"]

        # Ongoing orders
        if ongoing:
            lines.append("📦 *Ongoing*")
            for order in ongoing:
                order_num = order.get("order_number", "N/A")
                status = status_display.get(order.get("status"), order.get("status", "Unknown"))
                total = order.get("total", 0)
                items = order.get("items", [])
                item_summary = ", ".join([f"{item['commodity'].replace('_', ' ').title()}" for item in items[:2]])
                if len(items) > 2:
                    item_summary += f" +{len(items) - 2} more"
                lines.append(f"• *#{order_num}*")
                lines.append(f"  {item_summary}")
                lines.append(f"  {status}")
                lines.append(f"  Total: {format_price_display(total)}")
                lines.append("")
        else:
            lines.append("📦 *Ongoing*")
            lines.append("No ongoing orders\n")

        # Completed orders
        if completed:
            lines.append("✅ *Completed*")
            for order in completed[:5]:  # Show max 5 completed
                order_num = order.get("order_number", "N/A")
                status = status_display.get(order.get("status"), order.get("status", "Unknown"))
                total = order.get("total", 0)
                lines.append(f"• #{order_num} - {status} - {format_price_display(total)}")
        else:
            lines.append("✅ *Completed*")
            lines.append("No completed orders yet")

        message = "\n".join(lines)
        await send_whatsapp_message(to, message)
        await send_main_menu(to, welcome=False)

    except Exception as e:
        logger.error(f"❌ Error sending my orders: {e}")
        await send_whatsapp_message(to, "Couldn't load your orders. Please try again.")
        await send_main_menu(to, welcome=False)


async def send_vendor_order_notification(vendor_phone: str, order: dict):
    """
    Notify vendor of new paid order with CONFIRM/REJECT buttons.
    Falls back to plain text if interactive message fails (e.g., outside 24hr window).

    Args:
        vendor_phone: Vendor's WhatsApp number
        order: Order data

    Returns:
        True if notification sent (either interactive or text), False if both failed
    """
    try:
        items = order.get("items", [])
        items_text = "\n".join([
            f"- {item['commodity'].replace('_', ' ').title()} ({item['unit'].replace('_', ' ')}) x{item['quantity']}"
            for item in items
        ])

        total = order.get("total", 0)
        order_number = order['order_number']

        # Interactive button message body
        button_body_text = (
            f"*New Order #{order_number}*\n\n"
            f"{items_text}\n\n"
            f"*Total:* ₦{total:,.0f}\n\n"
            f"Can you fulfill this order?"
        )

        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        # Step 1: Try interactive button message first
        interactive_payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": vendor_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": button_body_text},
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
                json=interactive_payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Vendor notification (interactive) sent to {vendor_phone} for order {order_number}")
                return True
            else:
                logger.warning(f"⚠️ Interactive message failed: {response.status_code} - {response.text}")
                logger.info(f"Trying text fallback for vendor {vendor_phone}...")

        # Step 2: Fallback to plain text message
        text_body = (
            f"*New Order #{order_number}*\n\n"
            f"{items_text}\n\n"
            f"*Total:* ₦{total:,.0f}\n\n"
            f"Reply with:\n"
            f"*CONFIRM {order_number}*\n"
            f"or\n"
            f"*REJECT {order_number}*"
        )

        text_payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": vendor_phone,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": text_body
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                WHATSAPP_API_URL,
                headers=headers,
                json=text_payload,
                timeout=10.0
            )

            if response.status_code == 200:
                logger.info(f"✅ Vendor notification (text fallback) sent to {vendor_phone} for order {order_number}")
                return True
            else:
                logger.error(f"❌ Text fallback also failed: {response.status_code} - {response.text}")
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

        # Notify vendor with packing instructions
        await send_whatsapp_message(
            vendor_phone,
            f"Order #{order['order_number']} confirmed!\n\n"
            f"Please pack the items and write *{order['order_number']}* on the package.\n\n"
            f"Our agent will come to collect shortly."
        )

        # Notify buyer
        await send_whatsapp_message(
            buyer_phone,
            f"🎉 *Great News!*\n\n"
            f"Order #{order['order_number']} accepted!\n\n"
            f"The vendor is preparing your items.\n\n"
            f"⏱️ *Prep time:* 20-30 minutes\n\n"
            f"You'll be notified as soon as it's ready for delivery."
        )

        # Notify contributor/agent to pick up
        agent = get_pickup_agent_for_market("ogbete_main")
        if agent:
            await send_contributor_pickup_notification(agent["whatsapp_number"], order, order.get("vendor_id"))
        else:
            # Notify admin if no agent available
            await send_whatsapp_message(
                ADMIN_WHATSAPP_NUMBER,
                f"*NO PICKUP AGENT*\n\n"
                f"Order #{order['order_number']} confirmed but no agent available.\n"
                f"Please assign someone to pick up."
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


async def handle_agent_collected(agent_phone: str, order_id: str):
    """
    Handle agent clicking COLLECTED button

    Args:
        agent_phone: Agent's WhatsApp number
        order_id: Order ID
    """
    order = get_order_by_id(order_id)
    if not order:
        await send_whatsapp_message(agent_phone, "Order not found.")
        return

    if order["status"] != "vendor_confirmed":
        await send_whatsapp_message(
            agent_phone,
            f"Order #{order['order_number']} is not ready for collection."
        )
        return

    # Update order status
    update_order_status(order_id, "agent_collecting")

    # Notify agent with handover prompt
    await send_contributor_handover_prompt(agent_phone, order)

    # Notify customer
    await send_whatsapp_message(
        order["whatsapp_number"],
        f"📦 Your order #{order['order_number']} has been picked up from the vendor!"
    )


async def handle_agent_handedover(agent_phone: str, order_id: str):
    """
    Handle agent clicking HANDED OVER button

    Args:
        agent_phone: Agent's WhatsApp number
        order_id: Order ID
    """
    order = get_order_by_id(order_id)
    if not order:
        await send_whatsapp_message(agent_phone, "Order not found.")
        return

    if order["status"] != "agent_collecting":
        await send_whatsapp_message(
            agent_phone,
            f"Order #{order['order_number']} status has changed."
        )
        return

    # Update order status
    update_order_status(order_id, "handed_to_logistics")

    # Confirm to agent
    await send_whatsapp_message(
        agent_phone,
        f"✅ Order #{order['order_number']} handed to logistics."
    )

    # Notify logistics
    logistics = get_logistics_for_market("ogbete_main")
    if logistics:
        await send_logistics_delivery_notification(logistics["whatsapp_number"], order)
    else:
        # Notify admin if no logistics available
        await send_whatsapp_message(
            ADMIN_WHATSAPP_NUMBER,
            f"*NO LOGISTICS PARTNER*\n\n"
            f"Order #{order['order_number']} handed over but no logistics assigned."
        )


async def handle_logistics_pickedup(logistics_phone: str, order_id: str):
    """
    Handle logistics clicking PICKED UP button

    Args:
        logistics_phone: Logistics partner's WhatsApp number
        order_id: Order ID
    """
    order = get_order_by_id(order_id)
    if not order:
        await send_whatsapp_message(logistics_phone, "Order not found.")
        return

    if order["status"] != "handed_to_logistics":
        await send_whatsapp_message(
            logistics_phone,
            f"Order #{order['order_number']} status has changed."
        )
        return

    # Update order status
    update_order_status(order_id, "out_for_delivery")

    # Send delivered prompt to logistics
    await send_logistics_delivered_prompt(logistics_phone, order)

    # Notify customer
    await send_whatsapp_message(
        order["whatsapp_number"],
        f"🚚 Your order #{order['order_number']} is on the way!\n\n"
        f"Our delivery partner will arrive soon."
    )


async def handle_logistics_delivered(logistics_phone: str, order_id: str):
    """
    Handle logistics clicking DELIVERED button

    Args:
        logistics_phone: Logistics partner's WhatsApp number
        order_id: Order ID
    """
    order = get_order_by_id(order_id)
    if not order:
        await send_whatsapp_message(logistics_phone, "Order not found.")
        return

    if order["status"] != "out_for_delivery":
        await send_whatsapp_message(
            logistics_phone,
            f"Order #{order['order_number']} status has changed."
        )
        return

    # Update order status
    update_order_status(order_id, "delivered")

    # Confirm to logistics
    await send_whatsapp_message(
        logistics_phone,
        f"✅ Order #{order['order_number']} marked as delivered. Thank you!"
    )

    # Notify customer
    await send_whatsapp_message(
        order["whatsapp_number"],
        f"✅ Your order #{order['order_number']} has been delivered!\n\n"
        f"Thank you for shopping with PriceDeck."
    )
    await send_main_menu(order["whatsapp_number"], welcome=False)


# =====================================================
# CONTRIBUTOR/AGENT NOTIFICATIONS
# =====================================================

async def send_contributor_pickup_notification(agent_phone: str, order: dict, vendor_id: str = None):
    """
    Notify contributor to pick up order from vendor

    Args:
        agent_phone: Contributor's WhatsApp number
        order: Order data
        vendor_id: Vendor ID for location details
    """
    try:
        items = order.get("items", [])
        items_text = "\n".join([
            f"- {item['commodity'].replace('_', ' ').title()} ({item['unit'].replace('_', ' ')}) x{item['quantity']}"
            for item in items
        ])

        # Get vendor location details
        vendor_info = ""
        if vendor_id:
            vendor = get_vendor_with_location(vendor_id)
            if vendor:
                vendor_info = f"*Vendor:* {vendor.get('business_name', 'N/A')}\n"
                vendor_info += f"*Phone:* {vendor.get('whatsapp_number', 'N/A')}\n"
                if vendor.get('section'):
                    vendor_info += f"*Section:* {vendor.get('section')}\n"
                if vendor.get('shop_location'):
                    vendor_info += f"*Location:* {vendor.get('shop_location')}\n"
                if vendor.get('landmark'):
                    vendor_info += f"*Landmark:* {vendor.get('landmark')}\n"

        body_text = (
            f"📦 *Pickup #{order['order_number']}*\n\n"
            f"{vendor_info}\n"
            f"*Items:*\n{items_text}\n\n"
            f"⚠️ Verify package has order number written on it\n\n"
            f"Collect and hand to logistics."
        )

        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": agent_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": f"agent_collected_{order['id']}", "title": "COLLECTED"}}
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
                logger.info(f"✅ Pickup notification sent to agent {agent_phone} for order {order['order_number']}")
                return True
            else:
                logger.error(f"❌ Failed to send pickup notification: {response.status_code} - {response.text}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending pickup notification: {e}")
        return False


async def send_contributor_handover_prompt(agent_phone: str, order: dict):
    """
    Prompt contributor to hand over to logistics

    Args:
        agent_phone: Contributor's WhatsApp number
        order: Order data
    """
    try:
        body_text = (
            f"🤝 *Handover #{order['order_number']}*\n\n"
            f"*Deliver to:* {order.get('delivery_address', 'N/A')}\n"
            f"*Customer:* {order.get('contact_phone', 'N/A')}\n\n"
            f"Hand to logistics and say: *\"{order['order_number']}\"*"
        )

        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": agent_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": f"agent_handedover_{order['id']}", "title": "HANDED OVER"}}
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
                logger.info(f"✅ Handover prompt sent to agent {agent_phone}")
                return True
            else:
                logger.error(f"❌ Failed to send handover prompt: {response.status_code}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending handover prompt: {e}")
        return False


async def send_logistics_delivery_notification(logistics_phone: str, order: dict):
    """
    Notify logistics to deliver order (NO PRICE shown)

    Args:
        logistics_phone: Logistics partner's WhatsApp number
        order: Order data
    """
    try:
        items = order.get("items", [])
        items_text = ", ".join([
            f"{item['commodity'].replace('_', ' ').title()} ({item['unit'].replace('_', ' ')}) x{item['quantity']}"
            for item in items
        ])

        # NO PRICE - only items and delivery info
        body_text = (
            f"🚚 *Delivery #{order['order_number']}*\n\n"
            f"*Items:* {items_text}\n\n"
            f"*Deliver to:* {order.get('delivery_address', 'N/A')}\n"
            f"*Customer:* {order.get('contact_phone', 'N/A')}"
        )

        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": logistics_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": f"logistics_pickedup_{order['id']}", "title": "PICKED UP"}}
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
                logger.info(f"✅ Delivery notification sent to logistics {logistics_phone}")
                return True
            else:
                logger.error(f"❌ Failed to send logistics notification: {response.status_code}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending logistics notification: {e}")
        return False


async def send_logistics_delivered_prompt(logistics_phone: str, order: dict):
    """
    Prompt logistics to confirm delivery

    Args:
        logistics_phone: Logistics partner's WhatsApp number
        order: Order data
    """
    try:
        body_text = (
            f"🚚 *In Transit #{order['order_number']}*\n\n"
            f"*Deliver to:* {order.get('delivery_address', 'N/A')}\n"
            f"*Customer:* {order.get('contact_phone', 'N/A')}\n\n"
            f"Click when delivered."
        )

        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": logistics_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": f"logistics_delivered_{order['id']}", "title": "DELIVERED"}}
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
                logger.info(f"✅ Delivered prompt sent to logistics {logistics_phone}")
                return True
            else:
                logger.error(f"❌ Failed to send delivered prompt: {response.status_code}")
                return False

    except Exception as e:
        logger.error(f"❌ Error sending delivered prompt: {e}")
        return False


# =====================================================
# PAYMENT SUCCESS PAGE
# =====================================================

@app.get("/payment/success", response_class=HTMLResponse)
async def payment_success(
    reference: str = Query(None),
    trxref: str = Query(None)
):
    """
    Payment success page - shown after Paystack payment
    """
    order_number = reference or trxref or ""

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Payment Successful - PriceDeck</title>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }}
            .container {{
                background: white;
                border-radius: 20px;
                padding: 40px 30px;
                text-align: center;
                box-shadow: 0 10px 40px rgba(0,0,0,0.1);
                max-width: 400px;
                width: 100%;
            }}
            .checkmark {{
                width: 80px;
                height: 80px;
                background: #22c55e;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 24px;
            }}
            .checkmark svg {{
                width: 40px;
                height: 40px;
                fill: white;
            }}
            h1 {{
                color: #1f2937;
                font-size: 24px;
                margin-bottom: 12px;
            }}
            .order-number {{
                background: #f3f4f6;
                padding: 10px 20px;
                border-radius: 8px;
                font-family: monospace;
                font-size: 16px;
                color: #4b5563;
                margin-bottom: 20px;
                display: inline-block;
            }}
            .message {{
                color: #6b7280;
                font-size: 16px;
                line-height: 1.6;
                margin-bottom: 30px;
            }}
            .whatsapp-btn {{
                display: inline-flex;
                align-items: center;
                gap: 10px;
                background: #25D366;
                color: white;
                text-decoration: none;
                padding: 14px 28px;
                border-radius: 50px;
                font-size: 16px;
                font-weight: 600;
                transition: transform 0.2s, box-shadow 0.2s;
            }}
            .whatsapp-btn:hover {{
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(37, 211, 102, 0.4);
            }}
            .whatsapp-btn svg {{
                width: 24px;
                height: 24px;
                fill: white;
            }}
            .footer {{
                margin-top: 30px;
                color: #9ca3af;
                font-size: 14px;
            }}
            .brand {{
                font-weight: 600;
                color: #6b7280;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="checkmark">
                <svg viewBox="0 0 24 24">
                    <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z"/>
                </svg>
            </div>

            <h1>Payment Successful!</h1>

            {"<div class='order-number'>Order " + order_number + "</div>" if order_number else ""}

            <p class="message">
                Your order is being processed.<br>
                Check WhatsApp for confirmation and delivery updates.
            </p>

            <a href="https://wa.me/15551661013" class="whatsapp-btn">
                <svg viewBox="0 0 24 24">
                    <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/>
                </svg>
                Open WhatsApp
            </a>

            <div class="footer">
                <span class="brand">PriceDeck</span> - Market Prices Made Easy
            </div>
        </div>
    </body>
    </html>
    """

    return HTMLResponse(content=html)


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
                    f"✅ *Payment Confirmed!*\n\n"
                    f"Order #{order['order_number']}\n\n"
                    f"We'll notify you as soon as a vendor accepts your order."
                )

                # Notify vendor with retry logic
                from datetime import datetime, timezone
                vendor = get_vendor_for_market("ogbete")
                if vendor:
                    # Retry up to 3 times
                    notification_sent = False
                    for attempt in range(3):
                        success = await send_vendor_order_notification(
                            vendor["whatsapp_number"],
                            order
                        )
                        if success:
                            notification_sent = True
                            update_order(
                                order["id"],
                                {"vendor_notified_at": datetime.now(timezone.utc).isoformat()}
                            )
                            break  # Success - stop retrying
                        else:
                            logger.warning(f"Vendor notification attempt {attempt + 1}/3 failed for order {order['order_number']}")
                            if attempt < 2:  # Don't wait after last attempt
                                await asyncio.sleep(2)  # Wait 2 seconds before retry

                    # If all retries failed, notify admin
                    if not notification_sent:
                        logger.error(f"All 3 vendor notification attempts failed for order {order['order_number']}")
                        await send_whatsapp_message(
                            ADMIN_WHATSAPP_NUMBER,
                            f"⚠️ URGENT: Order #{order['order_number']} paid but vendor notification failed after 3 attempts. Please notify vendor manually!"
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
