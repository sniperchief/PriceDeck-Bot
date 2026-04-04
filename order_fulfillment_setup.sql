-- =====================================================
-- PriceDeck Order Fulfillment System - Database Setup
-- Run these queries in your Supabase SQL Editor
-- =====================================================

-- 1. Create logistics partners table
CREATE TABLE logistics_partners (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100),
    whatsapp_number VARCHAR(20) UNIQUE NOT NULL,
    market VARCHAR(50) DEFAULT 'ogbete_main',
    pickup_location VARCHAR(200),
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 2. Add pickup agent fields to users table
ALTER TABLE users ADD COLUMN is_pickup_agent BOOLEAN DEFAULT false;
ALTER TABLE users ADD COLUMN agent_market VARCHAR(50);

-- 3. Add vendor location fields
ALTER TABLE vendors ADD COLUMN section VARCHAR(100);
ALTER TABLE vendors ADD COLUMN shop_location VARCHAR(200);
ALTER TABLE vendors ADD COLUMN landmark VARCHAR(200);


-- =====================================================
-- SETUP DATA (Update with your actual values)
-- =====================================================

-- Insert your logistics partner (replace with actual phone number)
-- INSERT INTO logistics_partners (name, whatsapp_number, market, pickup_location)
-- VALUES ('Your Logistics Company', '2348012345678', 'ogbete_main', 'Main gate area');

-- Set a contributor as pickup agent (replace with actual phone number)
-- UPDATE users
-- SET is_pickup_agent = true, agent_market = 'ogbete_main'
-- WHERE whatsapp_number = '2348012345678';

-- Update vendor with location details (replace with actual vendor id)
-- UPDATE vendors
-- SET section = 'Rice Line',
--     shop_location = 'Row 3, Shop 12',
--     landmark = 'Opposite yellow building'
-- WHERE id = 'your-vendor-uuid-here';
