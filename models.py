from sqlalchemy import Column, Integer, String, func, DateTime, Numeric, ForeignKey, Enum, Boolean, Text
from database import Base
from sqlalchemy.orm import relationship
import enum
from datetime import datetime
from sqlalchemy.dialects.mysql import JSON

class TransactionStatus(enum.Enum):
    PENDING = 0
    PROCESSING = 1
    PROCESSED = 2
    REJECTED = 3
    ACCEPTED = 4

class Role(enum.Enum):
    ADMIN = "admin"
    CUSTOMER = "customer"

class OrderStatus(enum.Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    PROCESSING = "processing"

class Users(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True)
    email = Column(String(200), unique=True, index=True)
    hashed_password = Column(String(256))
    role = Column(Enum(Role), default=Role.CUSTOMER, nullable=False)
    created_at = Column(DateTime, default=func.now())
    orders = relationship("Orders", back_populates="user")
    products = relationship("Products", back_populates="user")
    addresses = relationship("Address", back_populates="user")
    transactions = relationship("Transaction", back_populates="user")

class Categories(Base):
    __tablename__ = 'categories'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)
    description = Column(String(200))
    products = relationship("Products", back_populates="category")

class Products(Base):
    __tablename__ = 'products'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    cost = Column(Numeric(precision=14, scale=2), nullable=False)
    price = Column(Numeric(precision=14, scale=2), nullable=False)
    img_url = Column(String(200), nullable=True)
    stock_quantity = Column(Numeric(precision=14, scale=2), nullable=False)
    description = Column(String(200), nullable=True)  # New description field
    created_at = Column(DateTime, default=func.now())
    barcode = Column(Numeric(precision=12), unique=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    category_id = Column(Integer, ForeignKey('categories.id'))
    brand = Column(String(100), nullable=True)
    user = relationship("Users", back_populates="products")
    category = relationship("Categories", back_populates="products")
    order_details = relationship("OrderDetails", back_populates="product")

class Orders(Base):
    __tablename__ = "orders"
    order_id = Column(Integer, primary_key=True, index=True)
    total = Column(Numeric(precision=14, scale=2))
    datetime = Column(DateTime, default=func.now(), index=True)
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING, nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'))
    address_id = Column(Integer, ForeignKey('addresses.id'), nullable=True)
    delivery_fee = Column(Numeric(precision=14, scale=2), nullable=False, default=0)
    completed_at = Column(DateTime, nullable=True)
    user = relationship("Users", back_populates="orders")
    order_details = relationship("OrderDetails", back_populates="order")
    address = relationship("Address")
    transactions = relationship("Transaction", back_populates="order")


class OrderDetails(Base):
    __tablename__ = "order_details"
    order_detail_id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.order_id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Numeric(precision=15, scale=2))
    total_price = Column(Numeric(precision=15, scale=2))
    product = relationship("Products", back_populates="order_details")
    order = relationship("Orders", back_populates="order_details")

class Address(Base):
    __tablename__ = 'addresses'
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(100), nullable=False)  
    last_name = Column(String(100), nullable=False)  
    phone_number = Column(String(20), nullable=False)
    address = Column(String(100), nullable=False)  
    additional_info = Column(String(255), nullable=True) 
    region = Column(String(100), nullable=True)  # New field for Regions
    city = Column(String(100), nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'))
    created_at = Column(DateTime, default=func.now())
    
    user = relationship("Users", back_populates="addresses")
    orders = relationship("Orders", back_populates="address") 

class Transaction(Base):
    __tablename__ = 'transactions'
    
    id = Column(Integer, primary_key=True, index=True)
    _pid = Column(Integer,ForeignKey('orders.order_id'), nullable=False, index=True)
    party_a = Column(String(100), nullable=False)
    party_b = Column(String(100), nullable=False)
    account_reference = Column(String(150), nullable=False)
    transaction_category = Column(Integer, nullable=False)
    transaction_type = Column(Integer, nullable=False)
    transaction_channel = Column(Integer, nullable=False)
    transaction_aggregator = Column(Integer, nullable=False)
    transaction_id = Column(String(100), unique=True, nullable=True, index=True)
    transaction_amount = Column(Numeric(10, 2), nullable=False)
    transaction_code = Column(String(100), unique=True, nullable=True)
    transaction_timestamp = Column(DateTime, default=datetime.utcnow)
    transaction_details = Column(Text, nullable=False)
    _feedback = Column(JSON, nullable=False)
    _status = Column(Enum(TransactionStatus), default=TransactionStatus.PENDING)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=func.now())
    user_id = Column(Integer, ForeignKey('users.id'))
    
    user = relationship("Users", back_populates="transactions")
    order = relationship("Orders", back_populates="transactions")



