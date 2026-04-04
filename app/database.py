"""
Database helper functions for PriceDeck
Handles all Supabase database operations
"""
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime, timezone
from supabase import create_client, Client
from app.config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# =====================================================
# USER MANAGEMENT
# =====================================================

def get_or_create_user(whatsapp_number: str) -> Dict[str, Any]:
    """
    Get existing user or create new user

    Args:
        whatsapp_number: WhatsApp number in format "2348012345678"

    Returns:
        User record as dictionary
    """
    try:
        # Try to get existing user
        response = supabase.table("users").select("*").eq("whatsapp_number", whatsapp_number).execute()

        if response.data and len(response.data) > 0:
            logger.info(f"Existing user found: {whatsapp_number}")
            return response.data[0]

        # Create new user
        new_user = {
            "whatsapp_number": whatsapp_number,
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "is_verified_contributor": False,
            "contribution_count": 0,
            "subscription_tier": "free"
        }

        response = supabase.table("users").insert(new_user).execute()
        logger.info(f"New user created: {whatsapp_number}")
        return response.data[0]

    except Exception as e:
        logger.error(f"Error in get_or_create_user: {e}")
        raise


def increment_contribution_count(whatsapp_number: str) -> bool:
    """
    Increment user's contribution count after successful price submission

    Args:
        whatsapp_number: WhatsApp number

    Returns:
        True if successful, False otherwise
    """
    try:
        # Get current count
        response = supabase.table("users").select("contribution_count").eq("whatsapp_number", whatsapp_number).execute()

        if response.data and len(response.data) > 0:
            current_count = response.data[0].get("contribution_count", 0)
            new_count = current_count + 1

            # Update count
            supabase.table("users").update({"contribution_count": new_count}).eq("whatsapp_number", whatsapp_number).execute()

            # Check if user should be verified contributor (10+ accurate reports)
            if new_count >= 10:
                supabase.table("users").update({"is_verified_contributor": True}).eq("whatsapp_number", whatsapp_number).execute()
                logger.info(f"User {whatsapp_number} is now a verified contributor!")

            return True

        return False

    except Exception as e:
        logger.error(f"Error incrementing contribution count: {e}")
        return False


# =====================================================
# MARKET MANAGEMENT
# =====================================================

def get_all_active_markets() -> List[Dict[str, Any]]:
    """
    Get all active and verified markets

    Returns:
        List of market records
    """
    try:
        response = supabase.table("markets").select("*").eq("is_active", True).eq("is_verified", True).execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error getting active markets: {e}")
        return []


def find_market_by_name(name: str) -> Optional[Dict[str, Any]]:
    """
    Find market by slug or display_name (case-insensitive)

    Args:
        name: Market name or slug to search for

    Returns:
        Market record if found, None otherwise
    """
    try:
        name_lower = name.lower().strip()

        # Try exact slug match first
        response = supabase.table("markets").select("*").eq("slug", name_lower).eq("is_active", True).execute()

        if response.data and len(response.data) > 0:
            return response.data[0]

        # Try case-insensitive display_name match
        response = supabase.table("markets").select("*").ilike("display_name", f"%{name}%").eq("is_active", True).execute()

        if response.data and len(response.data) > 0:
            return response.data[0]

        return None

    except Exception as e:
        logger.error(f"Error finding market: {e}")
        return None


def create_unverified_market(display_name: str, submitted_by: str) -> Dict[str, Any]:
    """
    Create a new unverified market from user submission

    Args:
        display_name: Market name as submitted by user
        submitted_by: WhatsApp number of submitter

    Returns:
        New market record
    """
    try:
        # Generate slug: lowercase, spaces to underscores, remove special chars
        slug = display_name.lower().strip()
        slug = "".join(c if c.isalnum() or c == " " else "" for c in slug)
        slug = slug.replace(" ", "_")

        # Capitalize each word for display_name
        clean_display_name = " ".join(word.capitalize() for word in display_name.split())

        new_market = {
            "slug": slug,
            "display_name": clean_display_name,
            "city": "enugu",
            "state": "enugu",
            "is_active": False,
            "is_verified": False,
            "submitted_by": submitted_by,
            "submitted_at": datetime.now(timezone.utc).isoformat()
        }

        response = supabase.table("markets").insert(new_market).execute()
        logger.info(f"New unverified market created: {clean_display_name} by {submitted_by}")
        return response.data[0]

    except Exception as e:
        logger.error(f"Error creating unverified market: {e}")
        raise


def get_pending_markets() -> List[Dict[str, Any]]:
    """
    Get all unverified markets pending admin review

    Returns:
        List of unverified market records
    """
    try:
        response = supabase.table("markets").select("*").eq("is_verified", False).order("submitted_at", desc=True).execute()
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"Error getting pending markets: {e}")
        return []


def verify_market(slug: str) -> bool:
    """
    Admin function: Verify and activate a market

    Args:
        slug: Market slug to verify

    Returns:
        True if successful, False otherwise
    """
    try:
        update_data = {
            "is_active": True,
            "is_verified": True,
            "verified_at": datetime.now(timezone.utc).isoformat()
        }

        response = supabase.table("markets").update(update_data).eq("slug", slug).execute()

        if response.data and len(response.data) > 0:
            logger.info(f"Market verified: {slug}")
            return True

        return False

    except Exception as e:
        logger.error(f"Error verifying market: {e}")
        return False


def reject_market(slug: str) -> bool:
    """
    Admin function: Reject and delete a market
    Also updates orphaned price_reports to market='unknown'

    Args:
        slug: Market slug to reject

    Returns:
        True if successful, False otherwise
    """
    try:
        # Update orphaned price reports
        supabase.table("price_reports").update({"market": "unknown"}).eq("market", slug).execute()

        # Delete the market
        response = supabase.table("markets").delete().eq("slug", slug).execute()

        logger.info(f"Market rejected and deleted: {slug}")
        return True

    except Exception as e:
        logger.error(f"Error rejecting market: {e}")
        return False


# =====================================================
# PRICE REPORTS
# =====================================================

def save_price_report(report_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Save a new price report to database

    Args:
        report_data: Dictionary with keys: commodity, commodity_raw, price,
                     unit, unit_raw, market, city, reported_by

    Returns:
        Saved price report record
    """
    try:
        # Add timestamp
        report_data["reported_at"] = datetime.now(timezone.utc).isoformat()
        report_data["is_flagged"] = False
        report_data["is_verified"] = False

        response = supabase.table("price_reports").insert(report_data).execute()
        logger.info(f"Price report saved: {report_data['commodity']} at {report_data['market']}")
        return response.data[0]

    except Exception as e:
        logger.error(f"Error saving price report: {e}")
        raise


def get_prices_by_commodity_all_markets(commodity: str, city: str = "enugu") -> List[Dict[str, Any]]:
    """
    Get latest price for a commodity across all active markets.
    Shows only the most recent price per market (no time expiration).
    Historical data is preserved in database for analytics.

    Args:
        commodity: Standardized commodity name (e.g., "garri_white", "beef")
        city: City to search in (default: "enugu")

    Returns:
        List of latest price data by market (one per market)
    """
    try:
        # Get all prices for this commodity, ordered by most recent first
        response = supabase.table("price_reports")\
            .select("*")\
            .eq("commodity", commodity)\
            .eq("city", city)\
            .eq("is_flagged", False)\
            .order("reported_at", desc=True)\
            .execute()

        if not response.data:
            return []

        # Get active markets only
        active_markets = get_all_active_markets()
        active_market_slugs = {m["slug"] for m in active_markets}

        # Get only the latest price per market
        # Since data is ordered by reported_at DESC, first occurrence per market is the latest
        market_data = {}
        for report in response.data:
            market = report["market"]

            # Skip inactive markets
            if market not in active_market_slugs:
                continue

            # Only keep the first (latest) record per market
            if market not in market_data:
                market_data[market] = {
                    "market": market,
                    "min_price": report["price"],
                    "max_price": report["price"],
                    "last_reported": report["reported_at"],
                    "unit": report.get("unit", "unit"),
                    "report_count": 1
                }

        # Convert to list and sort by price (cheapest first)
        result = list(market_data.values())
        result.sort(key=lambda x: x["min_price"])

        return result

    except Exception as e:
        logger.error(f"Error getting prices by commodity: {e}")
        return []


def get_prices_by_commodity_single_market(commodity: str, market: str, city: str = "enugu") -> List[Dict[str, Any]]:
    """
    Get latest price for a commodity at a specific market.
    Returns only the most recent price (no time expiration).

    Args:
        commodity: Standardized commodity name
        market: Market slug
        city: City to search in (default: "enugu")

    Returns:
        List with the most recent price report
    """
    try:
        response = supabase.table("price_reports")\
            .select("*")\
            .eq("commodity", commodity)\
            .eq("market", market)\
            .eq("city", city)\
            .eq("is_flagged", False)\
            .order("reported_at", desc=True)\
            .limit(1)\
            .execute()

        return response.data if response.data else []

    except Exception as e:
        logger.error(f"Error getting prices for single market: {e}")
        return []


def get_prices_by_commodity_and_unit(commodity: str, unit: str, city: str = "enugu") -> List[Dict[str, Any]]:
    """
    Get latest prices for a commodity filtered by unit across all markets.
    Returns one price per market (most recent).

    Args:
        commodity: Standardized commodity name (e.g., "garri_white")
        unit: Unit to filter by (e.g., "paint", "bag")
        city: City to search in (default: "enugu")

    Returns:
        List of price data by market, filtered by unit
    """
    try:
        # Get all prices for this commodity and unit, ordered by most recent first
        response = supabase.table("price_reports")\
            .select("*")\
            .eq("commodity", commodity)\
            .eq("unit", unit)\
            .eq("city", city)\
            .eq("is_flagged", False)\
            .order("reported_at", desc=True)\
            .execute()

        if not response.data:
            return []

        # Get active markets only
        active_markets = get_all_active_markets()
        active_market_slugs = {m["slug"] for m in active_markets}

        # Get only the latest price per market
        market_data = {}
        for report in response.data:
            market = report["market"]

            # Skip inactive markets
            if market not in active_market_slugs:
                continue

            # Only keep the first (latest) record per market
            if market not in market_data:
                market_data[market] = {
                    "market": market,
                    "price": report["price"],
                    "unit": report["unit"],
                    "last_reported": report["reported_at"]
                }

        # Convert to list and sort by price (cheapest first)
        result = list(market_data.values())
        result.sort(key=lambda x: x["price"])

        return result

    except Exception as e:
        logger.error(f"Error getting prices by commodity and unit: {e}")
        return []


def get_recent_prices_for_anomaly_check(commodity: str, city: str = "enugu", limit: int = 5) -> List[Dict[str, Any]]:
    """
    Get recent prices for a commodity to check for anomalies

    Args:
        commodity: Standardized commodity name
        city: City to search in
        limit: Number of recent prices to return

    Returns:
        List of recent price reports
    """
    try:
        response = supabase.table("price_reports")\
            .select("price, market, reported_at")\
            .eq("commodity", commodity)\
            .eq("city", city)\
            .eq("is_flagged", False)\
            .order("reported_at", desc=True)\
            .limit(limit)\
            .execute()

        return response.data if response.data else []

    except Exception as e:
        logger.error(f"Error getting recent prices: {e}")
        return []


def get_latest_price_for_commodity(commodity: str, market: str = None) -> Optional[Dict[str, Any]]:
    """
    Get the most recent price report for a commodity.

    Args:
        commodity: Standardized commodity name (e.g., "garri_white")
        market: Optional market slug to filter by

    Returns:
        Most recent price report or None if not found
    """
    try:
        query = supabase.table("price_reports")\
            .select("*")\
            .eq("commodity", commodity)\
            .eq("is_flagged", False)\
            .order("reported_at", desc=True)\
            .limit(1)

        # Add market filter if specified
        if market:
            query = query.eq("market", market)

        response = query.execute()

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        logger.error(f"Error getting latest price for {commodity}: {e}")
        return None


def is_user_contributor(whatsapp_number: str) -> bool:
    """
    Check if user is a verified contributor

    Args:
        whatsapp_number: User's WhatsApp number

    Returns:
        True if verified contributor, False otherwise
    """
    try:
        response = supabase.table("users")\
            .select("is_verified_contributor")\
            .eq("whatsapp_number", whatsapp_number)\
            .execute()

        if response.data and len(response.data) > 0:
            return response.data[0].get("is_verified_contributor", False)
        return False
    except Exception as e:
        logger.error(f"Error checking contributor status: {e}")
        return False


# =====================================================
# CART MANAGEMENT
# =====================================================

def get_or_create_cart(whatsapp_number: str) -> Dict[str, Any]:
    """
    Get active cart for user or create new one

    Args:
        whatsapp_number: User's WhatsApp number

    Returns:
        Cart record
    """
    try:
        # Check for existing active cart
        response = supabase.table("carts")\
            .select("*")\
            .eq("whatsapp_number", whatsapp_number)\
            .eq("is_active", True)\
            .execute()

        if response.data and len(response.data) > 0:
            return response.data[0]

        # Create new cart
        new_cart = {
            "whatsapp_number": whatsapp_number,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "is_active": True
        }

        response = supabase.table("carts").insert(new_cart).execute()
        logger.info(f"New cart created for {whatsapp_number}")
        return response.data[0]

    except Exception as e:
        logger.error(f"Error in get_or_create_cart: {e}")
        raise


def add_item_to_cart(
    cart_id: str,
    commodity: str,
    quantity: int,
    unit: str,
    unit_price: float
) -> Dict[str, Any]:
    """
    Add item to cart (or update quantity if exists)

    Args:
        cart_id: Cart ID
        commodity: Commodity name
        quantity: Number of units
        unit: Unit type
        unit_price: Price per unit

    Returns:
        Cart item record
    """
    try:
        # Check if item already in cart
        response = supabase.table("cart_items")\
            .select("*")\
            .eq("cart_id", cart_id)\
            .eq("commodity", commodity)\
            .eq("unit", unit)\
            .execute()

        if response.data and len(response.data) > 0:
            # Update quantity
            existing = response.data[0]
            new_quantity = existing["quantity"] + quantity

            response = supabase.table("cart_items")\
                .update({"quantity": new_quantity})\
                .eq("id", existing["id"])\
                .execute()
            return response.data[0]

        # Add new item
        new_item = {
            "cart_id": cart_id,
            "commodity": commodity,
            "quantity": quantity,
            "unit": unit,
            "unit_price": unit_price,
            "added_at": datetime.now(timezone.utc).isoformat()
        }

        response = supabase.table("cart_items").insert(new_item).execute()

        # Update cart timestamp
        supabase.table("carts")\
            .update({"updated_at": datetime.now(timezone.utc).isoformat()})\
            .eq("id", cart_id)\
            .execute()

        logger.info(f"Added {quantity}x {commodity} to cart {cart_id}")
        return response.data[0]

    except Exception as e:
        logger.error(f"Error adding item to cart: {e}")
        raise


def get_cart_items(whatsapp_number: str) -> List[Dict[str, Any]]:
    """
    Get all items in user's active cart

    Args:
        whatsapp_number: User's WhatsApp number

    Returns:
        List of cart items
    """
    try:
        # Get active cart
        cart_response = supabase.table("carts")\
            .select("id")\
            .eq("whatsapp_number", whatsapp_number)\
            .eq("is_active", True)\
            .execute()

        if not cart_response.data:
            return []

        cart_id = cart_response.data[0]["id"]

        # Get items
        items_response = supabase.table("cart_items")\
            .select("*")\
            .eq("cart_id", cart_id)\
            .order("added_at", desc=False)\
            .execute()

        return items_response.data if items_response.data else []

    except Exception as e:
        logger.error(f"Error getting cart items: {e}")
        return []


def clear_cart(whatsapp_number: str) -> bool:
    """
    Clear user's cart after checkout

    Args:
        whatsapp_number: User's WhatsApp number

    Returns:
        True if successful
    """
    try:
        # Get cart
        cart_response = supabase.table("carts")\
            .select("id")\
            .eq("whatsapp_number", whatsapp_number)\
            .eq("is_active", True)\
            .execute()

        if not cart_response.data:
            return True

        cart_id = cart_response.data[0]["id"]

        # Delete items
        supabase.table("cart_items").delete().eq("cart_id", cart_id).execute()

        # Deactivate cart
        supabase.table("carts")\
            .update({"is_active": False})\
            .eq("id", cart_id)\
            .execute()

        logger.info(f"Cart cleared for {whatsapp_number}")
        return True

    except Exception as e:
        logger.error(f"Error clearing cart: {e}")
        return False


def update_cart_item_quantity(whatsapp_number: str, commodity: str, new_quantity: int) -> bool:
    """
    Update quantity of a specific item in user's cart

    Args:
        whatsapp_number: User's WhatsApp number
        commodity: Commodity to update
        new_quantity: New quantity (must be >= 1)

    Returns:
        True if successful
    """
    try:
        if new_quantity < 1:
            return False

        # Get active cart
        cart_response = supabase.table("carts")\
            .select("id")\
            .eq("whatsapp_number", whatsapp_number)\
            .eq("is_active", True)\
            .execute()

        if not cart_response.data:
            return False

        cart_id = cart_response.data[0]["id"]

        # Update item quantity
        supabase.table("cart_items")\
            .update({"quantity": new_quantity})\
            .eq("cart_id", cart_id)\
            .eq("commodity", commodity)\
            .execute()

        # Update cart timestamp
        supabase.table("carts")\
            .update({"updated_at": datetime.now(timezone.utc).isoformat()})\
            .eq("id", cart_id)\
            .execute()

        logger.info(f"Updated {commodity} quantity to {new_quantity} for {whatsapp_number}")
        return True

    except Exception as e:
        logger.error(f"Error updating cart item quantity: {e}")
        return False


def remove_cart_item(whatsapp_number: str, commodity: str) -> bool:
    """
    Remove a specific item from user's cart

    Args:
        whatsapp_number: User's WhatsApp number
        commodity: Commodity to remove

    Returns:
        True if successful
    """
    try:
        # Get active cart
        cart_response = supabase.table("carts")\
            .select("id")\
            .eq("whatsapp_number", whatsapp_number)\
            .eq("is_active", True)\
            .execute()

        if not cart_response.data:
            return False

        cart_id = cart_response.data[0]["id"]

        # Delete item
        supabase.table("cart_items")\
            .delete()\
            .eq("cart_id", cart_id)\
            .eq("commodity", commodity)\
            .execute()

        logger.info(f"Removed {commodity} from cart for {whatsapp_number}")
        return True

    except Exception as e:
        logger.error(f"Error removing cart item: {e}")
        return False


# =====================================================
# VENDOR MANAGEMENT
# =====================================================

def get_vendor_for_market(market_slug: str) -> Optional[Dict[str, Any]]:
    """
    Get active vendor for a market

    Args:
        market_slug: Market slug (e.g., 'ogbete')

    Returns:
        Vendor record or None
    """
    try:
        response = supabase.table("vendors")\
            .select("*")\
            .eq("market", market_slug)\
            .eq("is_active", True)\
            .limit(1)\
            .execute()

        return response.data[0] if response.data else None

    except Exception as e:
        logger.error(f"Error getting vendor for market: {e}")
        return None


def is_vendor(whatsapp_number: str) -> bool:
    """
    Check if phone number belongs to a vendor

    Args:
        whatsapp_number: Phone number to check

    Returns:
        True if vendor
    """
    try:
        response = supabase.table("vendors")\
            .select("id")\
            .eq("whatsapp_number", whatsapp_number)\
            .eq("is_active", True)\
            .execute()

        return bool(response.data)

    except Exception as e:
        logger.error(f"Error checking vendor status: {e}")
        return False


def create_vendor(
    whatsapp_number: str,
    business_name: str,
    market: str = "ogbete",
    commodities: List[str] = None,
    created_by: str = None
) -> Dict[str, Any]:
    """
    Admin function: Create new vendor

    Args:
        whatsapp_number: Vendor's WhatsApp number
        business_name: Business name
        market: Market slug
        commodities: List of commodities sold
        created_by: Admin who created

    Returns:
        Vendor record
    """
    try:
        vendor_data = {
            "whatsapp_number": whatsapp_number,
            "business_name": business_name,
            "market": market,
            "commodities": commodities or [],
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": created_by
        }

        response = supabase.table("vendors").insert(vendor_data).execute()
        logger.info(f"Vendor created: {business_name} at {market}")
        return response.data[0]

    except Exception as e:
        logger.error(f"Error creating vendor: {e}")
        raise


def get_vendor_by_phone(whatsapp_number: str) -> Optional[Dict[str, Any]]:
    """
    Get vendor by phone number

    Args:
        whatsapp_number: Vendor's phone number

    Returns:
        Vendor record or None
    """
    try:
        response = supabase.table("vendors")\
            .select("*")\
            .eq("whatsapp_number", whatsapp_number)\
            .eq("is_active", True)\
            .execute()

        return response.data[0] if response.data else None

    except Exception as e:
        logger.error(f"Error getting vendor by phone: {e}")
        return None


# =====================================================
# ORDER MANAGEMENT
# =====================================================

def generate_order_number() -> str:
    """
    Generate unique order number: PD-YYYYMMDD-XXX

    Returns:
        Order number string
    """
    from datetime import date
    today = date.today().strftime("%Y%m%d")

    # Get count of orders today
    response = supabase.table("orders")\
        .select("id")\
        .like("order_number", f"PD-{today}-%")\
        .execute()

    count = len(response.data) if response.data else 0
    return f"PD-{today}-{count + 1:03d}"


def create_order(
    whatsapp_number: str,
    vendor_id: str,
    items: List[Dict],
    subtotal: float,
    service_charge: float,
    delivery_fee: float,
    total: float,
    delivery_address: str,
    contact_phone: str
) -> Dict[str, Any]:
    """
    Create new order

    Args:
        whatsapp_number: Buyer's phone
        vendor_id: Vendor ID
        items: List of cart items
        subtotal: Items total
        service_charge: Service charge (10% capped at 3k)
        delivery_fee: Delivery charge
        total: Grand total
        delivery_address: Delivery address
        contact_phone: Contact phone

    Returns:
        Order record
    """
    try:
        order_data = {
            "order_number": generate_order_number(),
            "whatsapp_number": whatsapp_number,
            "vendor_id": vendor_id,
            "items": items,
            "subtotal": subtotal,
            "service_charge": service_charge,
            "delivery_fee": delivery_fee,
            "total": total,
            "delivery_address": delivery_address,
            "contact_phone": contact_phone,
            "payment_status": "pending",
            "status": "pending_payment",
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        response = supabase.table("orders").insert(order_data).execute()
        logger.info(f"Order created: {order_data['order_number']}")
        return response.data[0]

    except Exception as e:
        logger.error(f"Error creating order: {e}")
        raise


def update_order_payment_ref(order_id: str, reference: str) -> bool:
    """
    Update order with payment reference

    Args:
        order_id: Order ID
        reference: Payment reference

    Returns:
        True if successful
    """
    try:
        supabase.table("orders")\
            .update({"payment_reference": reference})\
            .eq("id", order_id)\
            .execute()
        return True
    except Exception as e:
        logger.error(f"Error updating order payment ref: {e}")
        return False


def update_order_status(
    order_id: str,
    status: str,
    payment_status: str = None
) -> bool:
    """
    Update order status

    Args:
        order_id: Order ID
        status: New status
        payment_status: New payment status (optional)

    Returns:
        True if successful
    """
    try:
        update_data = {"status": status}

        if payment_status:
            update_data["payment_status"] = payment_status
            if payment_status == "paid":
                update_data["paid_at"] = datetime.now(timezone.utc).isoformat()

        if status == "vendor_confirmed":
            update_data["vendor_responded_at"] = datetime.now(timezone.utc).isoformat()
        elif status == "vendor_rejected":
            update_data["vendor_responded_at"] = datetime.now(timezone.utc).isoformat()
        elif status == "delivered":
            update_data["delivered_at"] = datetime.now(timezone.utc).isoformat()

        supabase.table("orders").update(update_data).eq("id", order_id).execute()
        logger.info(f"Order {order_id} status updated to {status}")
        return True

    except Exception as e:
        logger.error(f"Error updating order status: {e}")
        return False


def update_order(order_id: str, update_data: Dict[str, Any]) -> bool:
    """
    General order update function

    Args:
        order_id: Order ID
        update_data: Fields to update

    Returns:
        True if successful
    """
    try:
        supabase.table("orders").update(update_data).eq("id", order_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error updating order: {e}")
        return False


def get_order_by_reference(reference: str) -> Optional[Dict[str, Any]]:
    """
    Get order by payment reference

    Args:
        reference: Payment reference

    Returns:
        Order record or None
    """
    try:
        response = supabase.table("orders")\
            .select("*")\
            .eq("payment_reference", reference)\
            .execute()

        return response.data[0] if response.data else None

    except Exception as e:
        logger.error(f"Error getting order by reference: {e}")
        return None


def get_order_by_id(order_id: str) -> Optional[Dict[str, Any]]:
    """
    Get order by ID

    Args:
        order_id: Order ID

    Returns:
        Order record or None
    """
    try:
        response = supabase.table("orders")\
            .select("*, vendors(*)")\
            .eq("id", order_id)\
            .execute()

        return response.data[0] if response.data else None

    except Exception as e:
        logger.error(f"Error getting order by ID: {e}")
        return None


def get_order_by_number(order_number: str) -> Optional[Dict[str, Any]]:
    """
    Get order by order number

    Args:
        order_number: Order number (e.g., PD-20260329-001)

    Returns:
        Order record or None
    """
    try:
        response = supabase.table("orders")\
            .select("*, vendors(*)")\
            .eq("order_number", order_number)\
            .execute()

        return response.data[0] if response.data else None

    except Exception as e:
        logger.error(f"Error getting order by number: {e}")
        return None


def get_user_orders(whatsapp_number: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Get user's recent orders

    Args:
        whatsapp_number: User's phone
        limit: Max orders to return

    Returns:
        List of orders
    """
    try:
        response = supabase.table("orders")\
            .select("*")\
            .eq("whatsapp_number", whatsapp_number)\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()

        return response.data if response.data else []

    except Exception as e:
        logger.error(f"Error getting user orders: {e}")
        return []


# =====================================================
# LOGISTICS PARTNERS
# =====================================================

def get_logistics_for_market(market: str = "ogbete_main") -> Optional[Dict[str, Any]]:
    """
    Get active logistics partner for a market

    Args:
        market: Market slug

    Returns:
        Logistics partner record or None
    """
    try:
        response = supabase.table("logistics_partners")\
            .select("*")\
            .eq("market", market)\
            .eq("is_active", True)\
            .limit(1)\
            .execute()

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        logger.error(f"Error getting logistics partner: {e}")
        return None


# =====================================================
# PICKUP AGENTS (Contributors)
# =====================================================

def get_pickup_agent_for_market(market: str = "ogbete_main") -> Optional[Dict[str, Any]]:
    """
    Get active pickup agent (contributor) for a market

    Args:
        market: Market slug

    Returns:
        User record of pickup agent or None
    """
    try:
        response = supabase.table("users")\
            .select("*")\
            .eq("is_pickup_agent", True)\
            .eq("agent_market", market)\
            .limit(1)\
            .execute()

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        logger.error(f"Error getting pickup agent: {e}")
        return None


def set_user_as_pickup_agent(whatsapp_number: str, market: str = "ogbete_main") -> bool:
    """
    Set a user as pickup agent for a market

    Args:
        whatsapp_number: User's phone
        market: Market slug

    Returns:
        True if successful
    """
    try:
        response = supabase.table("users")\
            .update({
                "is_pickup_agent": True,
                "agent_market": market
            })\
            .eq("whatsapp_number", whatsapp_number)\
            .execute()

        return True

    except Exception as e:
        logger.error(f"Error setting pickup agent: {e}")
        return False


def get_vendor_with_location(vendor_id: str) -> Optional[Dict[str, Any]]:
    """
    Get vendor with location details

    Args:
        vendor_id: Vendor ID

    Returns:
        Vendor record with location fields
    """
    try:
        response = supabase.table("vendors")\
            .select("*")\
            .eq("id", vendor_id)\
            .limit(1)\
            .execute()

        if response.data and len(response.data) > 0:
            return response.data[0]
        return None

    except Exception as e:
        logger.error(f"Error getting vendor: {e}")
        return None
