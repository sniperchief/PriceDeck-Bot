-- =====================================================
-- SHOPPING CART FEATURE TABLES
-- Run this in Supabase SQL Editor
-- =====================================================

-- TABLE: vendors
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

-- TABLE: carts
CREATE TABLE IF NOT EXISTS carts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    whatsapp_number TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_carts_whatsapp ON carts(whatsapp_number);
CREATE INDEX IF NOT EXISTS idx_carts_active ON carts(is_active);

-- TABLE: cart_items
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

-- TABLE: orders
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
-- ADD TEST VENDOR (replace phone number with real one)
-- =====================================================
-- INSERT INTO vendors (whatsapp_number, business_name, market, commodities, is_active)
-- VALUES ('2348012345678', 'Ogbete Foods', 'ogbete', ARRAY['rice', 'garri', 'beans'], true);
