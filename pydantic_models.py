from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import List, Optional
from enum import Enum
from decimal import Decimal

class Role(str, Enum):
    ADMIN = "admin"
    CUSTOMER = "customer"

class OrderStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

class CreateUserRequest(BaseModel):
    username: str
    email: EmailStr
    password: str

class LoginUserRequest(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class CategoryBase(BaseModel):
    name: str
    description: Optional[str]

class CategoryResponse(CategoryBase):
    id: int

class ProductsBase(BaseModel):
    name: str
    cost: float
    price: float
    img_url: str
    stock_quantity: float
    barcode: int
    category_id: Optional[int]
    brand: Optional[str]  # New optional brand field

class ProductResponse(ProductsBase):
    id: int
    created_at: datetime
    user_id: int
    category: Optional[CategoryResponse]

class CartItem(BaseModel):
    id: int
    quantity: float

class CartPayload(BaseModel):
    cart: List[CartItem]

class OrderDetailResponse(BaseModel):
    order_detail_id: int
    product_id: Optional[int]
    quantity: float
    total_price: float
    product: Optional[ProductResponse]

class OrderResponse(BaseModel):
    order_id: int
    total: float
    datetime: datetime
    status: OrderStatus
    user_id: int
    order_details: List[OrderDetailResponse]

class TokenVerifyRequest(BaseModel):
    token: str

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    new_password: str

class TokenVerificationResponse(BaseModel):
    username: str
    tokenverification: str

class UpdateProduct(BaseModel):
    name: Optional[str]
    price: Optional[float]
    cost: Optional[float]
    img_url: Optional[str]
    stock_quantity: Optional[float]
    barcode: Optional[int]
    category_id: Optional[int]
    brand: Optional[str]  # New optional brand field

class PaginatedProductResponse(BaseModel):
    items: List[ProductResponse]
    total: int
    page: int
    limit: int
    pages: int