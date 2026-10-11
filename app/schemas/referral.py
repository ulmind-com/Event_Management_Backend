from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

# --- Admin Configuration Schemas ---

class RewardSettingSchema(BaseModel):
    enabled: bool = True
    coins: int = Field(default=100, ge=0)
    activation_delay_hours: int = Field(default=0, ge=0)
    require_admin_approval: bool = False

class CoinValuationSchema(BaseModel):
    currency: str = 'INR'
    value_per_coin: float = Field(default=0.50, gt=0)

class CoinExpirySchema(BaseModel):
    enabled: bool = True
    duration_value: int = Field(default=90, gt=0)
    duration_unit: str = Field(default='DAYS', pattern='^(DAYS|WEEKS|MONTHS)$')
    warning_days: int = Field(default=7, ge=0)
    notify_user: bool = True

class RedemptionRulesSchema(BaseModel):
    course_redemption_enabled: bool = True
    workshop_redemption_enabled: bool = True
    min_coins_required: int = Field(default=10, ge=0)
    max_coins_per_order: int = Field(default=500, ge=0)
    min_purchase_amount: float = Field(default=100.0, ge=0)
    max_discount_percentage: float = Field(default=50.0, ge=0, le=100.0)
    max_discount_amount: float = Field(default=1000.0, ge=0)
    allow_with_coupons: bool = False
    eligible_course_ids: List[str] = []
    eligible_workshop_ids: List[str] = []
    excluded_item_ids: List[str] = []

class ReferralProgramConfigUpdate(BaseModel):
    program_enabled: bool = True
    campaign_start_at: Optional[datetime] = None
    campaign_end_at: Optional[datetime] = None
    attribution_expiry_days: int = Field(default=30, ge=1)
    referrer_reward: RewardSettingSchema = Field(default_factory=lambda: RewardSettingSchema(enabled=True, coins=100))
    welcome_reward: RewardSettingSchema = Field(default_factory=lambda: RewardSettingSchema(enabled=True, coins=50))
    coin_valuation: CoinValuationSchema = Field(default_factory=CoinValuationSchema)
    coin_expiry: CoinExpirySchema = Field(default_factory=CoinExpirySchema)
    redemption_rules: RedemptionRulesSchema = Field(default_factory=RedemptionRulesSchema)

class ReferralProgramConfigResponse(ReferralProgramConfigUpdate):
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

# --- Referral Attribution & Profile Schemas ---

class ReferralAttributeRequest(BaseModel):
    referral_code: str = Field(..., min_length=4, max_length=32)

class ReferralProfileResponse(BaseModel):
    referral_code: str
    website_referral_link: str
    app_referral_link: str
    referral_link: str
    total_referrals: int
    qualified_referrals: int
    pending_referrals: int
    total_referrer_rewards_earned: int
    total_welcome_rewards_received: int
    referral_history: List[Dict[str, Any]] = []

# --- Wallet & Expiry Schemas ---

class WalletSummary(BaseModel):
    available_coins: int
    pending_coins: int
    reserved_coins: int
    total_earned_coins: int
    total_redeemed_coins: int
    total_expired_coins: int
    total_reversed_coins: int
    coin_value: str
    currency: str
    estimated_discount_value: str
    discount_label: str = 'Estimated Course/Workshop Discount Value'

class ExpirySummary(BaseModel):
    enabled: bool
    next_expiry_at: Optional[str] = None
    expiring_soon_coins: int = 0
    expiring_soon_days: int = 7

class CashWithdrawalRestriction(BaseModel):
    enabled: bool = False
    message: str = 'Cash withdrawal, bank transfer, and cash conversion are strictly prohibited. Coins represent Course/Workshop discount value only.'

class UserWalletResponse(BaseModel):
    wallet: WalletSummary
    referral_rewards: Dict[str, int]
    expiry: ExpirySummary
    cash_withdrawal: CashWithdrawalRestriction

# --- Course / Workshop Discount Calculation & Reservation Schemas ---

class DiscountQuoteRequest(BaseModel):
    item_id: str
    item_type: str = Field(..., pattern='^(course|workshop)$')
    coins_to_use: Optional[int] = None

class DiscountQuoteResponse(BaseModel):
    item_id: str
    item_type: str
    original_price: float
    coins_requested: int
    coins_applied: int
    coin_value_rate: float
    discount_amount: float
    final_payable_amount: float
    available_coins: int
    is_eligible: bool
    ineligible_reason: Optional[str] = None

class ReserveDiscountRequest(BaseModel):
    item_id: str
    item_type: str = Field(..., pattern='^(course|workshop)$')
    coins_to_reserve: int = Field(..., gt=0)
    order_reference: str

class ReserveDiscountResponse(BaseModel):
    reservation_id: str
    order_reference: str
    reserved_coins: int
    discount_amount: float
    status: str
    expires_at: datetime
