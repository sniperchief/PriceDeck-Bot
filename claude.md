# PriceDeck (MARKIT)

WhatsApp-based commodity price intelligence bot for Nigerian markets, starting with Enugu State.

## Tech Stack

- **Backend**: FastAPI + Uvicorn
- **AI/NLU**: Claude 3 Haiku (Anthropic API)
- **Database**: Supabase (PostgreSQL)
- **Messaging**: WhatsApp Cloud API (Meta)
- **Async**: Python asyncio, httpx

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app, webhook handler, WhatsApp message sender, interactive UI builders |
| `claude_tasks.py` | Claude NLU integration, conversation history, action handlers |
| `database.py` | Supabase CRUD operations (users, markets, prices, carts, orders) |
| `paystack_service.py` | Paystack payment integration |
| `config.py` | Environment configuration validation |

## Core Features

1. **Price Checking** - Users ask "How much is rice?" to get current market prices
2. **Price Reporting** - Verified contributors submit commodity prices (Ogbete auto-selected)
3. **Shopping Cart** - Users can add items to cart and checkout with Paystack payment (Ogbete Market only)
4. **My Orders** - Users can view their order history (ongoing and completed)
5. **Contributor System** - Verified contributors are assigned by admin (not auto-promoted)
6. **Order Fulfillment** - Vendor → Pickup Agent (verified contributor) → Logistics flow

## Pricing

- **Delivery Fee**: ₦500 (configurable via `DELIVERY_FEE`)
- **Service Charge**: 10% of subtotal, capped at ₦3,000 (configurable via `SERVICE_CHARGE_PERCENT` and `SERVICE_CHARGE_CAP`)

## Order Fulfillment Flow

1. Customer pays → Order status: `paid_awaiting_vendor`
2. Vendor confirms → Status: `vendor_confirmed`, pickup agent notified
3. Agent collects from vendor → Status: `agent_collecting`
4. Agent hands to logistics → Status: `handed_to_logistics`, logistics notified
5. Logistics delivers → Status: `out_for_delivery`
6. Customer receives → Status: `delivered`

**Roles:**
- **Vendor**: Receives order notification, confirms availability, packages items
- **Pickup Agent**: Verified contributor who collects from vendor and hands to logistics
- **Logistics Partner**: Delivers to customer (does NOT see order prices)

## Supported Data

**Categories & Items**:
- **Grains**: Rice (local, foreign), Garri (white, yellow), Beans (brown, iron)
- **Proteins**: Beef (kg), Goat Meat (kg), Chicken (kg), Egg (crate, half_crate)
- **Seafood**: Crayfish, Stockfish, Dry Fish, Frozen Fish, Smoked Fish
- **Vegetables**: Tomatoes, Pepper, Onions, Ugu Leaf, Bitter Leaf, Oha Leaf
- **Oils**: Palm Oil, Kings Oil (vegetable oil)
- **Spices & Pasta**: Maggi Knorr, Salt, Spaghetti, Indomie, Kitchen Glory, Gino Curry Powder, Gino Tomato Paste, Titus Sardine

**Markets (Enugu)**: Ogbete Main Market (only market currently active)

**Units**: paint, half_paint, bag_25kg, bag_10kg, kg, crate, half_crate, litre, portion, bunch, pack, piece, sachet, rolls, carton, 500g, 1_litre, 5_litre

**UX Flow (Simplified - 1 tap to see prices)**:
- Garri/Beans: Tap → See both varieties' paint prices → Add to cart or see half paint prices
- Rice: Tap → See Local & Foreign paint prices → Add to cart or see bag prices (25kg, 10kg)
- Egg: Tap → See crate & half_crate prices → Add to cart or browse more proteins
- Beef/Goat Meat/Chicken: Tap → See kg price → Add to cart or browse more proteins
- All items include "More [Category]" button for easy navigation

## Database Tables

- `users` - WhatsApp profiles, contribution counts, verified status, `is_pickup_agent`, `agent_market`
- `markets` - Active markets
- `price_reports` - Price submissions with commodity, price, unit, market
- `vendors` - Registered vendors with `section`, `shop_location`, `landmark`
- `carts` / `cart_items` - Shopping cart data
- `orders` - Checkout orders with payment status and `service_charge`
- `logistics_partners` - Delivery partners per market

## State Management

- `user_action_context` - Tracks current user action (check_price, report_price)
- `partial_price_reports` - Incomplete price report data during multi-step flow
- `partial_cart` - Incomplete cart/checkout data during shopping flow
- Conversation history per user (last 10 messages)

## Response Markers

Used in `claude_tasks.py` to trigger UI flows in `main.py`:
- `__SELECT_UNIT__` - Show unit selection list
- `__CONFIRM_PRICE__` - Show confirmation buttons
- `__ADD_TO_CART__` - Show add to cart button
- `__VIEW_CART__` - Show cart with checkout options
- `__CHECKOUT_PHONE__` - Phone selection for checkout
- `__PAYMENT_LINK__` - Send Paystack payment link

## Environment Variables

Required in `.env`:
- `WHATSAPP_TOKEN`, `PHONE_NUMBER_ID`, `VERIFY_TOKEN`
- `SUPABASE_URL`, `SUPABASE_KEY`
- `ANTHROPIC_API_KEY`
- `ADMIN_WHATSAPP_NUMBER`
- `PAYSTACK_SECRET_KEY`, `PAYSTACK_PUBLIC_KEY`

Optional (with defaults):
- `DELIVERY_FEE` (default: 500)
- `SERVICE_CHARGE_PERCENT` (default: 0.10)
- `SERVICE_CHARGE_CAP` (default: 3000)

## API Endpoints

- `GET /` - Health check
- `GET/POST /webhook` - WhatsApp webhook
- `POST /paystack/webhook` - Paystack payment webhook
- `GET /payment/success` - Payment success page (redirect after Paystack payment)

## Running the App

```bash
uvicorn main:app --reload
```
