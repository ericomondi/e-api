from fastapi import FastAPI, HTTPException, Depends, status
from pydantic_models import (
    ProductsBase, CartPayload, CartItem, UpdateProduct, CategoryBase, CategoryResponse,
    ProductResponse, OrderResponse, OrderDetailResponse, Role
)
from typing import Annotated, List
import models
from database import engine, db_dependency
from sqlalchemy.orm import Session
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

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
app.include_router(auth.router)
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
    if user.get("role") != Role.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

@app.get("/public/products", response_model=List[ProductResponse], status_code=status.HTTP_200_OK)
async def browse_products(db: db_dependency, skip: int = 0, limit: int = 10):
    try:
        products = db.query(models.Products).offset(skip).limit(limit).all()
        return products
    except SQLAlchemyError as e:
        logger.error(f"Error fetching products: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching products")

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

@app.get("/products", response_model=List[ProductResponse], status_code=status.HTTP_200_OK)
async def fetch_products(user: user_dependency, db: db_dependency, skip: int = 0, limit: int = 10):
    require_admin(user)
    try:
        products = db.query(models.Products).filter(models.Products.user_id == user.get("id")).offset(skip).limit(limit).all()
        return products
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
        new_order = models.Orders(user_id=user.get("id"), total=0)
        db.add(new_order)
        db.commit()
        db.refresh(new_order)
        
        total_cost = Decimal('0')
        for item in order_payload.cart:
            product = db.query(models.Products).filter_by(id=item.id).first()
            if not product:
                db.rollback()
                raise HTTPException(status_code=404, detail=f"Product ID {item.id} not found")
            quantity = Decimal(str(item.quantity))  # Convert float to Decimal
            if product.stock_quantity < quantity:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Insufficient stock for product {product.name}")
            
            order_detail = models.OrderDetails(
                order_id=new_order.order_id,
                product_id=product.id,
                quantity=quantity,
                total_price=product.price * quantity,
            )
            total_cost += order_detail.total_price
            product.stock_quantity -= quantity
            db.add(order_detail)
        
        new_order.total = total_cost
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

@app.get("/orders", response_model=List[OrderResponse], status_code=status.HTTP_200_OK)
async def fetch_orders(user: user_dependency, db: db_dependency, skip: int = 0, limit: int = 10):
    try:
        orders = db.query(models.Orders).filter(models.Orders.user_id == user.get("id")).offset(skip).limit(limit).all()
        return orders
    except SQLAlchemyError as e:
        logger.error(f"Error fetching orders: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching orders")

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)