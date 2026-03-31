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
| `database.py` | Supabase CRUD operations (users, markets, prices, alerts) |
| `alert_service.py` | Background task for checking/triggering price alerts |
| `config.py` | Environment configuration validation |

## Core Features

1. **Price Checking** - Users ask "How much is rice?" to get current market prices
2. **Price Reporting** - Verified contributors submit commodity prices from specific markets
3. **Price Alerts** - Users set alerts for when prices go above/below thresholds
4. **Contributor System** - Users with 10+ verified submissions become verified contributors

## Supported Data

**Commodities**: Garri (white, yellow, ijebu), Rice (local, foreign, ofada), Beans (oloyin, brown, iron), tomatoes, pepper, palm oil, yam, plantain, beef, chicken, fish, eggs, onions, crayfish, cement

**Markets (Enugu)**: Ogbete Main Market, Abakpa Market, Mammy Market, Garriki Market, Obiagu Market, New Market, Mayor Market, Kenyetta Market, Orie Emene

**Units**: paint, half_paint, cup, bag, half_bag, mudu, kg, piece, etc.

## Database Tables

- `users` - WhatsApp profiles, contribution counts, verified status
- `markets` - Active markets
- `price_reports` - Price submissions with commodity, price, unit, market
- `alerts` - User price alerts with threshold and direction

## State Management

- `user_action_context` - Tracks current user action (check_price, report_price, set_alert)
- `partial_price_reports` - Incomplete price report data during multi-step flow
- `partial_alerts` - Incomplete alert data during setup
- Conversation history per user (last 10 messages)

## Response Markers

Used in `claude_tasks.py` to trigger UI flows in `main.py`:
- `__SELECT_UNIT__` - Show unit selection list
- `__SELECT_MARKET__` - Show market selection list
- `__DIRECTION__` - Show above/below buttons for alerts
- `__CONFIRM_PRICE__` - Show confirmation buttons

## Environment Variables

Required in `.env`:
- `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`
- `SUPABASE_URL`, `SUPABASE_KEY`
- `ANTHROPIC_API_KEY`
- `ADMIN_WHATSAPP_NUMBER`

## Running the App

```bash
uvicorn main:app --reload
```

Alert service runs as background task on startup.
