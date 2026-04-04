"""
Configuration and environment variables for PriceDeck
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# WhatsApp API Configuration
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

# Admin Configuration
ADMIN_WHATSAPP_NUMBER = os.getenv("ADMIN_WHATSAPP_NUMBER")

# Supabase Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Claude API Configuration
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Paystack Configuration
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY")

# Shopping Configuration
DELIVERY_FEE = int(os.getenv("DELIVERY_FEE", "500"))
SERVICE_CHARGE_PERCENT = float(os.getenv("SERVICE_CHARGE_PERCENT", "0.10"))  # 10%
SERVICE_CHARGE_CAP = int(os.getenv("SERVICE_CHARGE_CAP", "3000"))  # Max ₦3,000

# Environment
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# WhatsApp API Base URL
WHATSAPP_API_URL = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"

# Validate required environment variables
def validate_config():
    """Validate that all required environment variables are set"""
    required_vars = {
        "WHATSAPP_TOKEN": WHATSAPP_TOKEN,
        "PHONE_NUMBER_ID": PHONE_NUMBER_ID,
        "VERIFY_TOKEN": VERIFY_TOKEN,
        "ADMIN_WHATSAPP_NUMBER": ADMIN_WHATSAPP_NUMBER,
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_KEY": SUPABASE_KEY,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    }

    missing = [key for key, value in required_vars.items() if not value]

    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    return True
