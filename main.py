from fastapi import FastAPI, HTTPException, Depends, status, UploadFile, File, Query
from pydantic_models import (
    ProductsBase, CartPayload, CartItem, UpdateProduct, CategoryBase, CategoryResponse,
    ProductResponse, OrderResponse, OrderDetailResponse, Role, PaginatedProductResponse,
     ImageResponse, AddressCreate, AddressResponse, PaginatedOrderResponse, OrderStatus, InitiatePaymentRequest,
      PaymentCallbackRequest, PaginatedOrderWithUserResponse, UpdateOrderStatusRequest)
from typing import Annotated, List, Optional
import models
from database import engine, db_dependency
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import SQLAlchemyError
import auth
from auth import get_active_user
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from datetime import datetime
import logging
from dotenv import load_dotenv
import os
from decimal import Decimal
from math import ceil
import uuid
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from lnmo import router as lnmo_router

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
app.include_router(auth.router)
app.include_router(lnmo_router)
models.Base.metadata.create_all(bind=engine) 

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


user_dependency = Annotated[dict, Depends(get_active_user)]

def require_admin(user: user_dependency):
    print(f"User role: {user.get('role')}")
    try:
        user_role = Role(user.get("role"))  # Convert string to Role enum
    except ValueError:
        # Handle case where role is not a valid enum value
        raise HTTPException(status_code=403, detail="Invalid role")
    if user_role != Role.ADMIN:  # Compare enum members
        raise HTTPException(status_code=403, detail="Admin access required")
    return user



# Ensure uploads directory exists
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.post("/upload-image", response_model=ImageResponse, status_code=status.HTTP_201_CREATED)
async def upload_image(user: user_dependency, file: UploadFile = File(...)):
    require_admin(user)
    try:
        # Validate file type
        if not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="Only image files are allowed")
        
        # Validate file size (e.g., max 5MB)
        max_size = 5 * 1024 * 1024  # 5MB in bytes
        content = await file.read()
        if len(content) > max_size:
            raise HTTPException(status_code=400, detail="File size exceeds 5MB limit")
        
        # Generate unique filename
        file_extension = file.filename.split(".")[-1].lower()
        if file_extension not in ["jpg", "jpeg", "png", "gif"]:
            raise HTTPException(status_code=400, detail="Unsupported image format")
        unique_filename = f"{uuid.uuid4()}.{file_extension}"
        file_path = UPLOAD_DIR / unique_filename
        
        # Save file
        with file_path.open("wb") as f:
            f.write(content)
        
        # Generate URL (assuming static file serving or CDN in production)
        img_url = f"/uploads/{unique_filename}"
        
        logger.info(f"Image uploaded: {unique_filename} by user {user.get('id')}")
        return {"message": "Image uploaded successfully", "img_url": img_url}
    except Exception as e:
        logger.error(f"Error uploading image: {str(e)}")
        raise HTTPException(status_code=500, detail="Error uploading image")

@app.get("/public/products", response_model=PaginatedProductResponse, status_code=status.HTTP_200_OK)
async def browse_products(db: db_dependency, search: str = None, page: int = 1, limit: int = 10):
    try:
        skip = (page - 1) * limit
        query = db.query(models.Products)
        if search:
            query = query.filter(models.Products.name.ilike(f"%{search}%"))
            logger.info(f"Product search query: {search}")
        total = query.count()
        products = query.offset(skip).limit(limit).all()
        total_pages = ceil(total / limit)
        return {
            "items": products,
            "total": total,
            "page": page,
            "limit": limit,
            "pages": total_pages
        }
    except SQLAlchemyError as e:
        logger.error(f"Error fetching products: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching products")

@app.get("/public/products/{product_id}", response_model=ProductResponse, status_code=status.HTTP_200_OK)
async def get_product_by_id(product_id: int, db: db_dependency):
    try:
        product = db.query(models.Products).filter(models.Products.id == product_id).first()
        if not product:
            logger.info(f"Product not found: ID {product_id}")
            raise HTTPException(status_code=404, detail="Product not found")
        return product
    except SQLAlchemyError as e:
        logger.error(f"Error fetching product by ID {product_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching product")

@app.get("/public/categories", response_model=List[CategoryResponse], status_code=status.HTTP_200_OK)
async def browse_categories(db: db_dependency):
    try:
        categories = db.query(models.Categories).all()
        return categories
    except SQLAlchemyError as e:
        logger.error(f"Error fetching categories: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching categories")

@app.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(user: user_dependency, db: db_dependency, category: CategoryBase):
    require_admin(user)
    try:
        db_category = models.Categories(**category.dict())
        db.add(db_category)
        db.commit()
        db.refresh(db_category)
        return db_category
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Error creating category: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/products", status_code=status.HTTP_201_CREATED)
async def add_product(user: user_dependency, db: db_dependency, create_product: ProductsBase):
    require_admin(user)
    try:
        add_product = models.Products(
            **create_product.dict(),
            user_id=user.get("id")
        )
        db.add(add_product)
        db.commit()
        db.refresh(add_product)
        return {"message": "Product added successfully"}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Error adding product: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/products", response_model=PaginatedProductResponse, status_code=status.HTTP_200_OK)
async def fetch_products(user: user_dependency, db: db_dependency, search: str = None, page: int = 1, limit: int = 10):
    require_admin(user)
    try:
        skip = (page - 1) * limit
        query = db.query(models.Products).filter(models.Products.user_id == user.get("id"))
        if search:
            query = query.filter(models.Products.name.ilike(f"%{search}%"))
            logger.info(f"Admin product search query: {search}")
        total = query.count()
        products = query.offset(skip).limit(limit).all()
        total_pages = ceil(total / limit)
        return {
            "items": products,
            "total": total,
            "page": page,
            "limit": limit,
            "pages": total_pages
        }
    except SQLAlchemyError as e:
        logger.error(f"Error fetching products: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching products")

@app.put("/update-product/{product_id}", status_code=status.HTTP_200_OK)
async def update_product(product_id: int, updated_data: UpdateProduct, user: user_dependency, db: db_dependency):
    require_admin(user)
    try:
        product = db.query(models.Products).filter(
            models.Products.id == product_id,
            models.Products.user_id == user.get("id")
        ).first()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        update_dict = updated_data.dict(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(product, key, value)
        db.commit()
        db.refresh(product)
        return {"message": "Product updated successfully"}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Error updating product: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/delete-product/{product_id}", status_code=status.HTTP_200_OK)
async def delete_product(product_id: int, db: db_dependency, user: user_dependency):
    require_admin(user)
    try:
        product = db.query(models.Products).filter(
            models.Products.id == product_id,
            models.Products.user_id == user.get("id")
        ).first()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        order_details = db.query(models.OrderDetails).filter(models.OrderDetails.product_id == product_id).first()
        if order_details:
            raise HTTPException(status_code=400, detail="Cannot delete product with existing orders")
        db.delete(product)
        db.commit()
        return {"message": "Product deleted successfully"}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Error deleting product: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/create_order", status_code=status.HTTP_201_CREATED)
async def create_order(db: db_dependency, user: user_dependency, order_payload: CartPayload):
    try:
        # Validate address if provided
        address_id = order_payload.address_id
        if address_id:
            address = db.query(models.Address).filter(
                models.Address.id == address_id,
                models.Address.user_id == user.get("id")
            ).first()
            if not address:
                raise HTTPException(status_code=400, detail="Invalid address ID")
        
        # Convert delivery fee to Decimal for precise arithmetic
        delivery_fee = Decimal(str(order_payload.delivery_fee))
        
        # Create new order with default status PENDING
        new_order = models.Orders(
            user_id=user.get("id"),
            total=0,
            address_id=address_id,
            delivery_fee=delivery_fee,
            status=OrderStatus.PENDING  # Initial status
        )
        db.add(new_order)
        db.commit()
        db.refresh(new_order)
        
        # Process cart items and calculate total cost
        total_cost = Decimal('0')
        for item in order_payload.cart:
            product = db.query(models.Products).filter_by(id=item.id).first()
            if not product:
                db.rollback()
                raise HTTPException(status_code=404, detail=f"Product ID {item.id} not found")
            quantity = Decimal(str(item.quantity))
            if product.stock_quantity < quantity:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Insufficient stock for product {product.name}")
            
            # Create order detail entry
            order_detail = models.OrderDetails(
                order_id=new_order.order_id,
                product_id=product.id,
                quantity=quantity,
                total_price=product.price * quantity,
            )
            total_cost += order_detail.total_price
            product.stock_quantity -= quantity
            db.add(order_detail)
        
        # Update order total with cart total plus delivery fee
        new_order.total = total_cost + new_order.delivery_fee
        
        # Handle transaction linking if transaction_id is provided
        if order_payload.transaction_id:
            transaction = db.query(models.Transactions).filter(
                models.Transactions.id == order_payload.transaction_id,
                models.Transactions.user_id == user.get("id"),
                models.Transactions.order_id.is_(None),  # Ensure transaction isn't already linked
                models.Transactions._status == 4  # ACCEPTED status
            ).first()
            if not transaction:
                db.rollback()
                raise HTTPException(status_code=400, detail="Invalid or already used transaction")
            if transaction.transaction_amount < new_order.total:
                db.rollback()
                raise HTTPException(status_code=400, detail="Insufficient transaction amount")
            # Link transaction to order and update status
            transaction.order_id = new_order.order_id
            new_order.status = OrderStatus.PROCESSING  # Payment confirmed, ready for processing
        
        # Commit all changes
        db.commit()
        
        logger.info(f"Order {new_order.order_id} created for user {user.get('id')}")
        return {
            "message": "Order created successfully",
            "order_id": new_order.order_id,
        }
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Error creating order: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError as e:
        db.rollback()
        logger.error(f"Invalid quantity value: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid quantity value")



@app.get("/orders", response_model=PaginatedOrderResponse, status_code=status.HTTP_200_OK)
async def fetch_orders(
    user: user_dependency,
    db: db_dependency,
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    status: Optional[OrderStatus] = None
):
    try:
        query = db.query(models.Orders).filter(models.Orders.user_id == user.get("id"))
        if status:
            query = query.filter(models.Orders.status == status)
        
        total = query.count()
        orders = (
            query
            .options(joinedload(models.Orders.order_details).joinedload(models.OrderDetails.product))
            .offset(skip)
            .limit(limit)
            .all()
        )
        page = (skip // limit) + 1
        pages = ceil(total / limit) if limit > 0 else 0
        return {
            "items": orders,
            "total": total,
            "page": page,
            "limit": limit,
            "pages": pages
        }
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail="Error fetching orders")


@app.get("/orders/{order_id}", response_model=OrderResponse, status_code=status.HTTP_200_OK)
async def get_order_by_id(
    order_id: int,
    user: user_dependency,
    db: db_dependency
):
    """
    Fetch a specific order by ID for the authenticated user
    """
    try:
        order = (
            db.query(models.Orders)
            .filter(
                models.Orders.order_id == order_id,
                models.Orders.user_id == user.get("id")
            )
            .options(
                joinedload(models.Orders.order_details).joinedload(models.OrderDetails.product),
                joinedload(models.Orders.address)
            )
            .first()
        )
        
        if not order:
            logger.info(f"Order not found: ID {order_id} for user {user.get('id')}")
            raise HTTPException(status_code=404, detail="Order not found")
        
        logger.info(f"Retrieved order {order_id} for user {user.get('id')}")
        return order
        
    except SQLAlchemyError as e:
        logger.error(f"Error fetching order {order_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching order")



# New endpoint to update order status

@app.put("/update-order-status/{order_id}", status_code=status.HTTP_200_OK)
async def update_order_status(
    order_id: int,
    request: UpdateOrderStatusRequest,  # Accept body with "status"
    user: user_dependency,
    db: db_dependency
):
    """
    Update the status of an order and set completed_at if status is DELIVERED
    """
    try:
        require_admin(user)  # Only admins can update order status
        order = db.query(models.Orders).filter(models.Orders.order_id == order_id).first()
        if not order:
            logger.info(f"Order not found: ID {order_id}")
            raise HTTPException(status_code=404, detail="Order not found")
        
        # Update status from the request body
        order.status = request.status
        
        # Set completed_at if status is DELIVERED
        if request.status == OrderStatus.DELIVERED:
            order.completed_at = func.now()
        elif order.completed_at and request.status != OrderStatus.DELIVERED:
            # Optionally clear completed_at if status changes away from DELIVERED
            order.completed_at = None
        
        db.commit()
        db.refresh(order)
        
        logger.info(f"Order {order_id} status updated to {request.status} by user {user.get('id')}")
        return {"message": f"Order status updated to {request.status}"}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Error updating order status for order {order_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error updating order status")


@app.get("/dashboard", status_code=status.HTTP_200_OK)
async def dashboard(user: user_dependency, db: db_dependency):
    require_admin(user)
    try:
        id = user.get("id")
        today = datetime.utcnow().date()
        
        total_sales = db.query(func.sum(models.Orders.total)).filter(models.Orders.user_id == id).scalar() or 0
        total_products = db.query(func.count(models.Products.id)).filter(models.Products.user_id == id).scalar() or 0
        today_sale = db.query(func.sum(models.Orders.total)).filter(
            models.Orders.user_id == id, func.date(models.Orders.datetime) == today
        ).scalar() or 0
        
        return {
            "total_sales": float(total_sales),
            "total_products": total_products,
            "today_sale": float(today_sale),
        }
    except SQLAlchemyError as e:
        logger.error(f"Error fetching dashboard data: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching dashboard data")


 

# Create Address endpoint
@app.post("/addresses", response_model=AddressResponse, status_code=status.HTTP_201_CREATED)
async def create_address(user: user_dependency, db: db_dependency, address: AddressCreate):
    try:
        # If setting as default, unset other default addresses for this user
        if address.is_default:
            db.query(models.Address).filter(
                models.Address.user_id == user.get("id"),
                models.Address.is_default == True
            ).update({"is_default": False})
        
        db_address = models.Address(
            **address.dict(),
            user_id=user.get("id")
        )
        db.add(db_address)
        db.commit()
        db.refresh(db_address)
        logger.info(f"Address created for user {user.get('id')}: Address ID {db_address.id}")
        return db_address
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Error creating address: {str(e)}")
        raise HTTPException(status_code=500, detail="Error creating address")

# Get Addresses endpoint
@app.get("/addresses", response_model=List[AddressResponse], status_code=status.HTTP_200_OK)
async def get_addresses(user: user_dependency, db: db_dependency):
    try:
        addresses = db.query(models.Address).filter(
            models.Address.user_id == user.get("id")
        ).all()
        if not addresses:
            logger.info(f"No addresses found for user {user.get('id')}")
            return []
        logger.info(f"Retrieved {len(addresses)} addresses for user {user.get('id')}")
        return addresses
    except SQLAlchemyError as e:
        logger.error(f"Error fetching addresses: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching addresses")


@app.delete("/addresses/{address_id}", status_code=status.HTTP_200_OK)
async def delete_address(address_id: int, user: user_dependency, db: db_dependency):
    try:
        address = db.query(models.Address).filter(
            models.Address.id == address_id,
            models.Address.user_id == user.get("id")
        ).first()
        if not address:
            logger.info(f"Address not found: ID {address_id} for user {user.get('id')}")
            raise HTTPException(status_code=404, detail="Address not found")
        
        # Check if address is used in any orders
        order = db.query(models.Orders).filter(
            models.Orders.address_id == address_id
        ).first()
        if order:
            logger.info(f"Cannot delete address {address_id}: used in order {order.order_id}")
            raise HTTPException(status_code=400, detail="Cannot delete address used in orders")
        
        db.delete(address)
        db.commit()
        logger.info(f"Address {address_id} deleted by user {user.get('id')}")
        return {"message": "Address deleted successfully"}
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Error deleting address {address_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error deleting address")





@app.get("/admin/orders", response_model=PaginatedOrderWithUserResponse, status_code=status.HTTP_200_OK)
async def fetch_all_orders(
    user: user_dependency,
    db: db_dependency,
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    status: Optional[OrderStatus] = None
):
    """
    Fetch all orders with associated user and address details (admin only).
    Supports pagination and optional status filtering.
    Excludes order_details.
    """
    require_admin(user)
    try:
        query = db.query(models.Orders).join(models.Users, models.Orders.user_id == models.Users.id)
        if status:
            query = query.filter(models.Orders.status == status)
        
        total = query.count()
        orders = (
            query
            .options(
                joinedload(models.Orders.address),
                joinedload(models.Orders.user)
            )
            .offset(skip)
            .limit(limit)
            .all()
        )

        page = (skip // limit) + 1
        pages = ceil(total / limit) if limit > 0 else 0

        logger.info(f"Admin {user.get('id')} fetched {len(orders)} orders (page {page}, limit {limit})")
        return {
            "items": orders,
            "total": total,
            "page": page,
            "limit": limit,
            "pages": pages
        }
    except SQLAlchemyError as e:
        logger.error(f"Error fetching all orders: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching orders")
        
                
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)