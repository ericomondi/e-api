from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import db_dependency
from models import Transaction, Orders, Users, TransactionStatus
from pydantic_models import (
    TransactionRequest, 
    QueryRequest, 
    APIResponse,
    TransactionResponse,
    CallbackRequest
)
from auth import get_active_user
import requests
import base64
import os
from dotenv import load_dotenv
import logging

load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lnmo", tags=["lnmo"])

class LNMORepository:
    def __init__(self):
        self.MPESA_LNMO_CONSUMER_KEY = os.getenv("MPESA_LNMO_CONSUMER_KEY", "LO5CCWw0F9QdXWVOMURJGUA8OIEGJ4kL53b2e5ZCm4nKCs7J")
        self.MPESA_LNMO_CONSUMER_SECRET = os.getenv("MPESA_LNMO_CONSUMER_SECRET", "yWbM4wSsOY7CMK4vhdkCgVAcZiBFLA3FtNQV2E3M4odi9gEXXjaHkfcoH42rEsv6")
        self.MPESA_LNMO_ENVIRONMENT = os.getenv("MPESA_LNMO_ENVIRONMENT", "sandbox")
        self.MPESA_LNMO_PASS_KEY = os.getenv("MPESA_LNMO_PASS_KEY", "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919")
        self.MPESA_LNMO_SHORT_CODE = os.getenv("MPESA_LNMO_SHORT_CODE", "174379")
        self.CALLBACK_URL = os.getenv("MPESA_CALLBACK_URL", "https://225e-197-237-26-50.ngrok-free.app/lnmo/callback")

    def generate_access_token(self) -> Optional[str]:
        """Generate M-Pesa access token"""
        try:
            endpoint = f"https://{self.MPESA_LNMO_ENVIRONMENT}.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
            credentials = f"{self.MPESA_LNMO_CONSUMER_KEY}:{self.MPESA_LNMO_CONSUMER_SECRET}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()
            headers = {"Authorization": f"Basic {encoded_credentials}"}
            
            response = requests.get(endpoint, headers=headers)
            response.raise_for_status()
            
            return response.json().get("access_token")
        except Exception as e:
            logger.error(f"Error generating access token: {str(e)}")
            return None

    def generate_password(self) -> Optional[str]:
        """Generate M-Pesa password"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            password = base64.b64encode(
                f"{self.MPESA_LNMO_SHORT_CODE}{self.MPESA_LNMO_PASS_KEY}{timestamp}".encode()
            ).decode()
            return password
        except Exception as e:
            logger.error(f"Error generating password: {str(e)}")
            return None

    async def initiate_transaction(self, data: Dict[str, Any], db: Session, user_id: int) -> Dict[str, Any]:
        """Initiate M-Pesa STK Push transaction"""
        try:
            # Validate that the order exists and belongs to the user
            order = db.query(Orders).filter(
                Orders.order_id == int(data["AccountReference"]),
                Orders.user_id == user_id
            ).first()
            
            if not order:
                raise HTTPException(
                    status_code=404, 
                    detail="Order not found or doesn't belong to user"
                )

            endpoint = f"https://{self.MPESA_LNMO_ENVIRONMENT}.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
            access_token = self.generate_access_token()
            
            if not access_token:
                raise HTTPException(status_code=500, detail="Failed to get M-Pesa access token")

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }

            payload = {
                "BusinessShortCode": self.MPESA_LNMO_SHORT_CODE,
                "Password": self.generate_password(),
                "Timestamp": datetime.now().strftime("%Y%m%d%H%M%S"),
                "TransactionType": "CustomerPayBillOnline",
                "Amount": str(int(data["Amount"])),  # Convert to integer for M-Pesa
                "PartyA": data["PhoneNumber"],
                "PartyB": self.MPESA_LNMO_SHORT_CODE,
                "PhoneNumber": data["PhoneNumber"],
                "CallBackURL": self.CALLBACK_URL,
                "AccountReference": data["AccountReference"],
                "TransactionDesc": f"Payment for order {data['AccountReference']}",
            }

            response = requests.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            response_data = response.json()

            # Create transaction record
            transaction = Transaction(
                _pid=int(data["AccountReference"]),  # This is the order_id
                party_a=data["PhoneNumber"],
                party_b=self.MPESA_LNMO_SHORT_CODE,
                account_reference=data["AccountReference"],
                transaction_category=0,  # PURCHASE_ORDER
                transaction_type=1,      # CREDIT
                transaction_channel=1,   # LNMO
                transaction_aggregator=0, # MPESA_KE
                transaction_id=response_data.get("CheckoutRequestID"),
                transaction_amount=data["Amount"],
                transaction_code=None,
                transaction_timestamp=datetime.now(),
                transaction_details=f"Payment for order {data['AccountReference']}",
                _feedback=response_data,
                _status=TransactionStatus.PROCESSING,
                user_id=user_id
            )
            
            db.add(transaction)
            db.commit()
            db.refresh(transaction)
            
            logger.info(f"Transaction initiated for order {data['AccountReference']} by user {user_id}")
            return {
                "transaction_id": transaction.id,
                "checkout_request_id": response_data.get("CheckoutRequestID"),
                "response_data": response_data
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"M-Pesa API error: {str(e)}")
            raise HTTPException(status_code=500, detail="M-Pesa service unavailable")
        except Exception as e:
            logger.error(f"Transaction initiation error: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    async def query_transaction_status(self, checkout_request_id: str, db: Session) -> Dict[str, Any]:
        """Query M-Pesa transaction status"""
        try:
            endpoint = f"https://{self.MPESA_LNMO_ENVIRONMENT}.safaricom.co.ke/mpesa/stkpushquery/v1/query"
            access_token = self.generate_access_token()
            
            if not access_token:
                raise HTTPException(status_code=500, detail="Failed to get M-Pesa access token")

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }

            payload = {
                "BusinessShortCode": self.MPESA_LNMO_SHORT_CODE,
                "Password": self.generate_password(),
                "Timestamp": datetime.now().strftime("%Y%m%d%H%M%S"),
                "CheckoutRequestID": checkout_request_id,
            }

            response = requests.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            
            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"M-Pesa query error: {str(e)}")
            raise HTTPException(status_code=500, detail="M-Pesa service unavailable")
        except Exception as e:
            logger.error(f"Transaction query error: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

# Initialize repository
lnmo_repository = LNMORepository()

@router.post("/transact", response_model=APIResponse, status_code=status.HTTP_200_OK)
async def initiate_payment(
    transaction_data: TransactionRequest, 
    db: db_dependency, 
    user: dict = Depends(get_active_user)
):
    """Initiate M-Pesa STK Push payment"""
    try:
        data = {
            "Amount": transaction_data.amount,
            "PhoneNumber": transaction_data.phone_number,
            "AccountReference": str(transaction_data.order_id)
        }
        
        response = await lnmo_repository.initiate_transaction(data, db, user.get("id"))
        
        return APIResponse(
            status="success",
            message="Transaction initiated successfully",
            data=response
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Payment initiation error: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail="Failed to initiate payment"
        )

@router.post("/query", response_model=APIResponse, status_code=status.HTTP_200_OK)
async def query_transaction(
    query_data: QueryRequest, 
    db: db_dependency, 
    user: dict = Depends(get_active_user)
):
    """Query M-Pesa transaction status"""
    try:
        # Verify the transaction belongs to the user
        transaction = db.query(Transaction).filter(
            Transaction.transaction_id == query_data.checkout_request_id,
            Transaction.user_id == user.get("id")
        ).first()
        
        if not transaction:
            raise HTTPException(status_code=404, detail="Transaction not found")

        response = await lnmo_repository.query_transaction_status(query_data.checkout_request_id, db)
        
        return APIResponse(
            status="success",
            message="Transaction status retrieved",
            data=response
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transaction query error: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail="Failed to query transaction"
        )

@router.post("/callback", status_code=status.HTTP_200_OK)
async def payment_callback(callback_data: CallbackRequest, db: db_dependency):
    """Handle M-Pesa payment callback"""
    try:
        logger.info(f"Received M-Pesa callback: {callback_data}")
        
        # Extract relevant data from callback
        checkout_request_id = callback_data.body.stkCallback.checkoutRequestID
        result_code = callback_data.body.stkCallback.resultCode
        
        # Find the transaction
        transaction = db.query(Transaction).filter(
            Transaction.transaction_id == checkout_request_id
        ).first()
        
        if not transaction:
            logger.warning(f"Transaction not found for CheckoutRequestID: {checkout_request_id}")
            return {"status": "error", "message": "Transaction not found"}

        # Update transaction based on result code
        if result_code == 0:  # Success
            transaction._status = TransactionStatus.ACCEPTED
            if hasattr(callback_data.body.stkCallback, 'callbackMetadata'):
                # Extract transaction code if available
                for item in callback_data.body.stkCallback.callbackMetadata.item:
                    if item.name == "MpesaReceiptNumber":
                        transaction.transaction_code = item.value
                        break
        else:  # Failed
            transaction._status = TransactionStatus.REJECTED
        
        # Update feedback with full callback data
        transaction._feedback = callback_data.dict()
        
        db.commit()
        db.refresh(transaction)
        
        logger.info(f"Transaction {transaction.id} updated with status {transaction._status}")
        
        return {"status": "success", "message": "Callback processed"}
        
    except Exception as e:
        logger.error(f"Callback processing error: {str(e)}")
        return {"status": "error", "message": "Callback processing failed"}

@router.get("/transactions", response_model=list[TransactionResponse], status_code=status.HTTP_200_OK)
async def get_user_transactions(
    db: db_dependency, 
    user: dict = Depends(get_active_user),
    skip: int = 0,
    limit: int = 10
):
    """Get user's transactions"""
    try:
        transactions = db.query(Transaction).filter(
            Transaction.user_id == user.get("id")
        ).offset(skip).limit(limit).all()
        
        return transactions
        
    except Exception as e:
        logger.error(f"Error fetching transactions: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail="Failed to fetch transactions"
        )