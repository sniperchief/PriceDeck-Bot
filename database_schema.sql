-- =====================================================
-- MARKIT DATABASE SCHEMA
-- Supabase PostgreSQL Schema for Nigerian Market Price Intelligence
-- =====================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =====================================================
-- TABLE: users
-- Stores WhatsApp user information and contribution stats
-- =====================================================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    whatsapp_number TEXT UNIQUE NOT NULL,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    is_verified_contributor BOOLEAN DEFAULT FALSE,
    contribution_count INTEGER DEFAULT 0,
    preferred_market TEXT,
    subscription_tier TEXT DEFAULT 'free'
);

-- Create index on whatsapp_number for fast lookups
CREATE INDEX IF NOT EXISTS idx_users_whatsapp ON users(whatsapp_number);

-- =====================================================
-- TABLE: markets
-- Stores market information including user-submitted markets
-- =====================================================
CREATE TABLE IF NOT EXISTS markets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    slug TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    city TEXT DEFAULT 'enugu',
    state TEXT DEFAULT 'enugu',
    specialty TEXT,
    is_active BOOLEAN DEFAULT FALSE,
    is_verified BOOLEAN DEFAULT FALSE,
    submitted_by TEXT,
    submitted_at TIMESTAMPTZ,
    verified_at TIMESTAMPTZ
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_markets_slug ON markets(slug);
CREATE INDEX IF NOT EXISTS idx_markets_active ON markets(is_active);
CREATE INDEX IF NOT EXISTS idx_markets_verified ON markets(is_verified);

-- =====================================================
-- TABLE: price_reports
-- Stores all price submissions from users
-- =====================================================
CREATE TABLE IF NOT EXISTS price_reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    commodity TEXT NOT NULL,
    commodity_raw TEXT NOT NULL,
    price NUMERIC NOT NULL,
    unit TEXT NOT NULL,
    unit_raw TEXT NOT NULL,
    market TEXT NOT NULL,
    city TEXT DEFAULT 'enugu',
    reported_by TEXT NOT NULL,
    reported_at TIMESTAMPTZ DEFAULT NOW(),
    is_flagged BOOLEAN DEFAULT FALSE,
    is_verified BOOLEAN DEFAULT FALSE
);

-- Create indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_price_reports_commodity ON price_reports(commodity);
CREATE INDEX IF NOT EXISTS idx_price_reports_market ON price_reports(market);
CREATE INDEX IF NOT EXISTS idx_price_reports_city ON price_reports(city);
CREATE INDEX IF NOT EXISTS idx_price_reports_reported_at ON price_reports(reported_at);
CREATE INDEX IF NOT EXISTS idx_price_reports_lookup ON price_reports(commodity, market, city, reported_at);

-- =====================================================
-- TABLE: alerts
-- Stores user price alert preferences
-- =====================================================
CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    whatsapp_number TEXT NOT NULL,
    commodity TEXT NOT NULL,
    market TEXT,
    threshold_price NUMERIC NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('above', 'below')),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_alerts_whatsapp ON alerts(whatsapp_number);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(is_active);

-- =====================================================
-- SEED DATA: The 8 Enugu Markets
-- All verified and active from the start
-- =====================================================

INSERT INTO markets (slug, display_name, city, state, specialty, is_active, is_verified, submitted_by, submitted_at, verified_at) VALUES
('ogbete', 'Ogbete Main Market', 'enugu', 'enugu', 'Wholesale foodstuffs, electronics, clothing, general goods. The largest market in Enugu (5th largest in Nigeria). Best for bulk buying and wholesale prices.', TRUE, TRUE, NULL, NULL, NOW()),

('abakpa', 'Abakpa Market (Afia Akpa)', 'enugu', 'enugu', 'Fresh farm produce from Nike, Ugwogo, and neighbouring communities. Best for tomatoes, vegetables, pepper, yam, garden eggs.', TRUE, TRUE, NULL, NULL, NOW()),

('mammy_market', 'Mammy Market', 'enugu', 'enugu', 'Major meat market selling processed, ready-to-buy beef, goat meat, pork, and chicken. Convenient for household buyers, prices slightly higher than source markets.', TRUE, TRUE, NULL, NULL, NOW()),

('garriki', 'Garriki Market (Afor Garriki)', 'enugu', 'enugu', 'Live animal market and source for bulk meat buying. Cheapest meat prices but requires larger quantity purchases. Also sells local produce from Nkanu Land and Aninri communities.', TRUE, TRUE, NULL, NULL, NOW()),

('obiagu', 'Obiagu Market', 'enugu', 'enugu', 'Neighbourhood market serving Obiagu residential area. General food items and household goods with convenience premium pricing.', TRUE, TRUE, NULL, NULL, NOW()),

('new_market', 'New Market (Aria Market)', 'enugu', 'enugu', 'Traditional goods and produce from Ngwo, Udi, and Ezeagu communities. Best for local and traditional foods.', TRUE, TRUE, NULL, NULL, NOW()),

('mayor_market', 'Mayor Market', 'enugu', 'enugu', 'Small commodity market for fruits, vegetables, and household items. Very busy in evenings. Serves Agbani road corridor residents.', TRUE, TRUE, NULL, NULL, NOW()),

('kenyetta', 'Kenyetta Market', 'enugu', 'enugu', 'The most popular building materials market in Enugu State. Primary destination for cement, roofing sheets, iron rods, furniture, and interior decoration materials.', TRUE, TRUE, NULL, NULL, NOW()),

('orie_emene', 'Orie Emene', 'enugu', 'enugu', 'Market in Emene industrial area serving Emene town residents and workers. Local produce and general goods.', TRUE, TRUE, NULL, NULL, NOW())

ON CONFLICT (slug) DO NOTHING;

-- =====================================================
-- TABLE: vendors
-- Stores registered vendors for the shopping feature
-- =====================================================
CREATE TABLE IF NOT EXISTS vendors (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    whatsapp_number TEXT UNIQUE NOT NULL,
    business_name TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'ogbete',
    commodities TEXT[],
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_vendors_whatsapp ON vendors(whatsapp_number);
CREATE INDEX IF NOT EXISTS idx_vendors_market ON vendors(market);
CREATE INDEX IF NOT EXISTS idx_vendors_active ON vendors(is_active);

-- =====================================================
-- TABLE: carts
-- Stores active shopping carts for users
-- =====================================================
CREATE TABLE IF NOT EXISTS carts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    whatsapp_number TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_carts_whatsapp ON carts(whatsapp_number);
CREATE INDEX IF NOT EXISTS idx_carts_active ON carts(is_active);

-- =====================================================
-- TABLE: cart_items
-- Stores items in shopping carts
-- =====================================================
CREATE TABLE IF NOT EXISTS cart_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    cart_id UUID NOT NULL REFERENCES carts(id) ON DELETE CASCADE,
    commodity TEXT NOT NULL,
    quantity NUMERIC NOT NULL DEFAULT 1,
    unit TEXT NOT NULL,
    unit_price NUMERIC NOT NULL,
    added_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cart_items_cart ON cart_items(cart_id);

-- =====================================================
-- TABLE: orders
-- Stores completed orders from checkout
-- =====================================================
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_number TEXT UNIQUE NOT NULL,
    whatsapp_number TEXT NOT NULL,
    vendor_id UUID REFERENCES vendors(id),
    items JSONB NOT NULL,
    subtotal NUMERIC NOT NULL,
    delivery_fee NUMERIC NOT NULL DEFAULT 500,
    total NUMERIC NOT NULL,
    delivery_address TEXT NOT NULL,
    contact_phone TEXT NOT NULL,
    payment_reference TEXT,
    payment_status TEXT DEFAULT 'pending' CHECK (payment_status IN ('pending', 'paid', 'failed', 'refunded')),
    paid_at TIMESTAMPTZ,
    status TEXT DEFAULT 'pending_payment' CHECK (status IN (
        'pending_payment',
        'paid_awaiting_vendor',
        'vendor_confirmed',
        'vendor_rejected',
        'preparing',
        'out_for_delivery',
        'delivered',
        'cancelled'
    )),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    vendor_notified_at TIMESTAMPTZ,
    vendor_responded_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    rejection_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_whatsapp ON orders(whatsapp_number);
CREATE INDEX IF NOT EXISTS idx_orders_vendor ON orders(vendor_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_payment_ref ON orders(payment_reference);
CREATE INDEX IF NOT EXISTS idx_orders_order_number ON orders(order_number);

-- =====================================================
-- VERIFICATION
-- Run this to verify tables were created successfully
-- =====================================================

-- Check all tables exist
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
AND table_name IN ('users', 'markets', 'price_reports', 'alerts', 'vendors', 'carts', 'cart_items', 'orders');

-- Check seeded markets
SELECT slug, display_name, is_active, is_verified FROM markets ORDER BY display_name;

-- Check vendors
SELECT whatsapp_number, business_name, market, is_active FROM vendors;
