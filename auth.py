from datetime import timedelta, datetime
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette import status
from database import db_dependency
from models import Users
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
import jwt
from pydantic_models import (
    CreateUserRequest,
    Token,
    TokenVerifyRequest,
    LoginUserRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    TokenVerificationResponse
)
from passlib.context import CryptContext
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
import os
from dotenv import load_dotenv
import logging

load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-default-secure-key")
ALGORITHM = "HS256"
bcrypt_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_bearer = OAuth2PasswordBearer(tokenUrl="auth/login")

conf = ConnectionConfig(
    MAIL_USERNAME="ericoochieng456@gmail.com",
    MAIL_PASSWORD="dhqf lxgw zlaw bwdj",
    MAIL_FROM="ericoochieng456@gmail.com",
    MAIL_PORT=587,
    MAIL_SERVER="smtp.gmail.com",
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True,
)

@router.post("/register/customer", status_code=status.HTTP_201_CREATED)
async def register_customer(db: db_dependency, create_user_request: CreateUserRequest):
    logger.info(f"Customer registration payload: {create_user_request}")
    existing_user = db.query(Users).filter(
        (Users.email == create_user_request.email) | (Users.username == create_user_request.username)
    ).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username or email already exists")
    
    create_user_model = Users(
        username=create_user_request.username,
        email=create_user_request.email,
        hashed_password=bcrypt_context.hash(create_user_request.password),
        role="customer"
    )
    db.add(create_user_model)
    db.commit()
    db.refresh(create_user_model)
    logger.info(f"Customer {create_user_request.username} registered successfully")
    return {"message": "Customer created successfully"}

@router.post("/register/admin", status_code=status.HTTP_201_CREATED)
async def register_admin(db: db_dependency, create_user_request: CreateUserRequest):
    logger.info(f"Admin registration payload: {create_user_request}")
    existing_user = db.query(Users).filter(
        (Users.email == create_user_request.email) | (Users.username == create_user_request.username)
    ).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username or email already exists")
    
    create_user_model = Users(
        username=create_user_request.username,
        email=create_user_request.email,
        hashed_password=bcrypt_context.hash(create_user_request.password),
        role="admin"
    )
    db.add(create_user_model)
    db.commit()
    db.refresh(create_user_model)
    logger.info(f"Admin {create_user_request.username} registered successfully")
    return {"message": "Admin created successfully"}


def authenticate_user(email: str, password: str, db: Session):
    user = db.query(Users).filter(Users.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User does not exist")
    if not bcrypt_context.verify(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid password")
    return user

def create_access_token(username: str, user_id: int, role: str, expires_delta: timedelta):
    encode = {"sub": username, "id": user_id, "role": role}
    expires = datetime.utcnow() + expires_delta
    encode.update({"exp": expires})
    return jwt.encode(encode, SECRET_KEY, algorithm=ALGORITHM)

@router.post("/login", response_model=Token)
async def login(form_data: LoginUserRequest, db: db_dependency):
    logger.info(f"Login attempt for email: {form_data.email}")
    user = authenticate_user(form_data.email, form_data.password, db)
    token = create_access_token(user.username, user.id, user.role.value, timedelta(hours=1))
    logger.info(f"User {user.username} logged in successfully")
    return {"access_token": token, "token_type": "bearer"}

async def get_active_user(token: Annotated[str, Depends(oauth2_bearer)]):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        user_id: int = payload.get("id")
        role: str = payload.get("role")
        if username is None or user_id is None or role is None:
            raise HTTPException(status_code=401, detail="Could not validate user")
        return {"username": username, "id": user_id, "role": role}
    except jwt.ExpiredSignatureError:
        logger.warning("Token expired")
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.DecodeError:
        logger.warning("Invalid token")
        raise HTTPException(status_code=401, detail="Invalid token")

@router.post("/verify-token", response_model=TokenVerificationResponse, status_code=status.HTTP_200_OK)
async def verify_token(request_body: TokenVerifyRequest):
    try:
        payload = jwt.decode(request_body.token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        exp_timestamp = float(payload["exp"])
        exp_datetime = datetime.fromtimestamp(exp_timestamp)
        if exp_datetime < datetime.utcnow():
            logger.warning("Token expired during verification")
            raise HTTPException(status_code=401, detail="Token expired")
        logger.info(f"Token verified for user: {username}")
        return {"username": username, "tokenverification": "success"}
    except jwt.DecodeError:
        logger.warning("Invalid token during verification")
        raise HTTPException(status_code=401, detail="Invalid token")

@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(forgot_password_request: ForgotPasswordRequest, db: db_dependency):
    email = forgot_password_request.email
    user = db.query(Users).filter(Users.email == email).first()
    if not user:
        logger.warning(f"Password reset requested for non-existent email: {email}")
        raise HTTPException(status_code=404, detail="User does not exist")
    
    token_expires = timedelta(hours=1)
    reset_token = create_access_token(user.username, user.id, user.role.value, token_expires)
    
    message = MessageSchema(
        subject="Password Reset Request",
        recipients=[email],
        body=f"Please use the following link to reset your password: "
             f"http://localhost:3000/reset-password?token={reset_token}",
        subtype="html",
    )
    fm = FastMail(conf)
    await fm.send_message(message)
    logger.info(f"Password reset email sent to: {email}")
    return {"message": "Password reset email sent"}
    

@router.post("/reset-password/{token}", status_code=status.HTTP_200_OK)
async def reset_password(token: str, reset_password_request: ResetPasswordRequest, db: db_dependency):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("id")
        if user_id is None:
            logger.warning("Invalid reset token")
            raise HTTPException(status_code=401, detail="Invalid token")
        
        user = db.query(Users).filter(Users.id == user_id).first()
        if not user:
            logger.warning(f"Password reset attempted for non-existent user ID: {user_id}")
            raise HTTPException(status_code=404, detail="User does not exist")
        
        user.hashed_password = bcrypt_context.hash(reset_password_request.new_password)
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"Password reset successfully for user: {user.username}")
        return {"message": "Password has been reset successfully"}
    except jwt.ExpiredSignatureError:
        logger.warning("Expired reset token")
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.DecodeError:
        logger.warning("Invalid reset token")
        raise HTTPException(status_code=401, detail="Invalid token")