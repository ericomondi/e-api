import os
import requests
import base64
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic_models import TransactionRequest, QueryRequest, APIResponse, CallbackRequest , CheckTransactionStatus
from database import db_dependency
from sqlalchemy.orm import Session
from sqlalchemy import select
import models
from auth import get_active_user
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/payments", tags=["Payments"])

# User dependency
from typing import Annotated
user_dependency = Annotated[dict, Depends(get_active_user)]

class LNMORepository:
    """MPESA LNMO Repository for handling M-Pesa payments"""
    
    # MPESA configurations from environment variables
    MPESA_LNMO_CONSUMER_KEY = os.getenv("MPESA_LNMO_CONSUMER_KEY")
    MPESA_LNMO_CONSUMER_SECRET = os.getenv("MPESA_LNMO_CONSUMER_SECRET")
    MPESA_LNMO_ENVIRONMENT = os.getenv("MPESA_LNMO_ENVIRONMENT", "sandbox")
    MPESA_LNMO_INITIATOR_PASSWORD = os.getenv("MPESA_LNMO_INITIATOR_PASSWORD")
    MPESA_LNMO_INITIATOR_USERNAME = os.getenv("MPESA_LNMO_INITIATOR_USERNAME")
    MPESA_LNMO_PASS_KEY = os.getenv("MPESA_LNMO_PASS_KEY")
    MPESA_LNMO_SHORT_CODE = os.getenv("MPESA_LNMO_SHORT_CODE")
    MPESA_LNMO_CALLBACK_URL = os.getenv("MPESA_CALLBACK_URL")

    def __init__(self):
        # Validate required environment variables
        required_vars = [
            "MPESA_LNMO_CONSUMER_KEY", "MPESA_LNMO_CONSUMER_SECRET", 
            "MPESA_LNMO_PASS_KEY", "MPESA_LNMO_SHORT_CODE", "MPESA_LNMO_CALLBACK_URL"
        ]
        for var in required_vars:
            if not getattr(self, var):
                raise ValueError(f"Missing required environment variable: {var}")

    async def transact(self, data: Dict[str, Any], db: Session, user_id: int) -> Dict[str, Any]:
        """Handle MPESA LNMO transaction"""
        try:
            endpoint = f"https://{self.MPESA_LNMO_ENVIRONMENT}.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
            headers = {
                "Authorization": "Bearer " + self.generate_access_token(),
                "Content-Type": "application/json",
            }
            
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            payload = {
                "BusinessShortCode": self.MPESA_LNMO_SHORT_CODE,
                "Password": self.generate_password(),
                "Timestamp": timestamp,
                "TransactionType": "CustomerPayBillOnline",
                "Amount": str(data["amount"]),
                "PartyA": data["phone_number"],
                "PartyB": self.MPESA_LNMO_SHORT_CODE,
                "PhoneNumber": data["phone_number"],
                "CallBackURL": self.MPESA_LNMO_CALLBACK_URL,
                "AccountReference": str(data["order_id"]),
                "TransactionDesc": f"Payment for order {data['order_id']}",
            }

            response = requests.post(endpoint, json=payload, headers=headers)
            response_data = response.json()

            # Save transaction to the database
            transaction = models.Transaction(
                _pid=data["order_id"],
                party_a=data["phone_number"],
                party_b=self.MPESA_LNMO_SHORT_CODE,
                account_reference=str(data["order_id"]),
                transaction_category=0,  # PURCHASE_ORDER
                transaction_type=1,      # CREDIT
                transaction_channel=1,   # LNMO
                transaction_aggregator=0, # MPESA_KE
                transaction_id=response_data.get("CheckoutRequestID"),
                transaction_amount=Decimal(str(data["amount"])),
                transaction_code=None,
                transaction_timestamp=datetime.now(),
                transaction_details=f"Payment for order {data['order_id']}",
                _feedback=response_data,
                _status=models.TransactionStatus.PROCESSING,
                user_id=user_id,
                # order_id=None  # Will be linked when order is created
            )

            db.add(transaction)
            db.commit()
            db.refresh(transaction)

            logger.info(f"Transaction created: ID {transaction.id} for user {user_id}")
            return response_data

        except Exception as e:
            logger.error(f"Error in transact: {str(e)}")
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Transaction failed: {str(e)}"
            )

    def query(self, transaction_id: str) -> Dict[str, Any]:
        """Query MPESA LNMO transaction status"""
        try:
            endpoint = f"https://{self.MPESA_LNMO_ENVIRONMENT}.safaricom.co.ke/mpesa/stkpushquery/v1/query"
            headers = {
                "Authorization": "Bearer " + self.generate_access_token(),
                "Content-Type": "application/json",
            }
            
            payload = {
                "BusinessShortCode": self.MPESA_LNMO_SHORT_CODE,
                "Password": self.generate_password(),
                "Timestamp": datetime.now().strftime("%Y%m%d%H%M%S"),
                "CheckoutRequestID": transaction_id,
            }

            response = requests.post(endpoint, json=payload, headers=headers)
            return response.json()
            
        except Exception as e:
            logger.error(f"Error in query: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Query failed: {str(e)}"
            )

    async def callback(self, data: Dict[str, Any], db: Session) -> Dict[str, Any]:
        """Handle MPESA callback"""
        try:
            checkout_request_id = data["body"]["stkCallback"]["checkoutRequestID"]

            # Find the transaction in the database
            transaction = db.query(models.Transaction).filter(
                models.Transaction.transaction_id == checkout_request_id
            ).first()

            if transaction:
                # Store the entire callback response in _feedback
                transaction._feedback = data
                # Get the ResultCode to determine success or failure
                result_code = data["body"]["stkCallback"]["resultCode"]

                if result_code == 0:
                    # Transaction is successful
                    transaction._status = models.TransactionStatus.ACCEPTED
                    # Safely access CallbackMetadata
                    callback_metadata = data["body"]["stkCallback"].get("callbackMetadata")
                    if callback_metadata:
                        items = callback_metadata.get("item", [])
                        for item in items:
                            if item.get("name") == "MpesaReceiptNumber" and "value" in item:
                                transaction.transaction_code = item["value"]
                                break
                else:
                    # Transaction failed
                    transaction._status = models.TransactionStatus.REJECTED

                db.commit()
                logger.info(f"Transaction {transaction.id} updated via callback: status {transaction._status}")
            else:
                logger.warning(f"Transaction not found for checkout_request_id: {checkout_request_id}")

            return data

        except Exception as e:
            logger.error(f"Error in callback: {str(e)}")
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Callback processing failed: {str(e)}"
            )

    def generate_access_token(self) -> str:
        """Generate an access token for the MPESA API"""
        try:
            endpoint = f"https://{self.MPESA_LNMO_ENVIRONMENT}.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
            credentials = f"{self.MPESA_LNMO_CONSUMER_KEY}:{self.MPESA_LNMO_CONSUMER_SECRET}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()

            headers = {
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/json",
            }

            response = requests.get(endpoint, headers=headers)
            response_data = response.json()

            if response.status_code == 200:
                return response_data["access_token"]
            else:
                raise Exception(
                    f"Failed to generate access token: {response_data.get('error_description', 'Unknown error')}"
                )

        except Exception as e:
            logger.error(f"Error generating access token: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to generate access token"
            )

    def generate_password(self) -> str:
        """Generate a password for the MPESA API transaction"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            password = base64.b64encode(
                f"{self.MPESA_LNMO_SHORT_CODE}{self.MPESA_LNMO_PASS_KEY}{timestamp}".encode()
            ).decode()
            return password
        except Exception as e:
            logger.error(f"Error generating password: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to generate password"
            )


# Initialize the repository
lnmo_repository = LNMORepository()

# =============================================================================
# API ROUTES
# =============================================================================

@router.post("/lnmo/transact", response_model=APIResponse)
async def initiate_payment(
    transaction_data: TransactionRequest,
    user: user_dependency,
    db: db_dependency
):
    """Initiate MPESA LNMO payment for an order"""
    try:
        # Verify the order exists and belongs to the user
        order = db.query(models.Orders).filter(
            models.Orders.order_id == transaction_data.order_id,
            models.Orders.user_id == user.get("id")
        ).first()
        
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )
        
        # Check if order already has a successful payment
        existing_transaction = db.query(models.Transaction).filter(
            models.Transaction._pid == transaction_data.order_id,
            models.Transaction._status == models.TransactionStatus.ACCEPTED
        ).first()
        
        if existing_transaction:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order already has a successful payment"
            )

        data = {
            "amount": transaction_data.amount,
            "phone_number": transaction_data.phone_number,
            "order_id": transaction_data.order_id
        }
        
        response = await lnmo_repository.transact(data, db, user.get("id"))
        
        return APIResponse(
            status="success",
            message="Payment initiated successfully",
            data=response
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error initiating payment: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initiate payment"
        )


@router.post("/lnmo/query", response_model=APIResponse)
async def query_payment(
    query_data: QueryRequest,
    user: user_dependency
):
    """Query MPESA LNMO transaction status"""
    try:
        response = lnmo_repository.query(query_data.checkout_request_id)
        
        return APIResponse(
            status="success",
            message="Query completed successfully",
            data=response
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying payment: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to query payment"
        )


@router.post("/lnmo/callback")
async def payment_callback(
    callback_data: CallbackRequest,
    db: db_dependency
):
    """Handle MPESA callback (webhook endpoint)"""
    try:
        logger.info(f"Received callback: {callback_data}")
        
        response = await lnmo_repository.callback(callback_data.dict(), db)
        
        return {
            "ResultCode": 0,
            "ResultDesc": "Success"
        }
        
    except Exception as e:
        logger.error(f"Error processing callback: {str(e)}")
        return {
            "ResultCode": 1,
            "ResultDesc": "Failed"
        }


@router.get("/transactions", status_code=status.HTTP_200_OK)
async def get_user_transactions(
    user: user_dependency,
    db: db_dependency
):
    """Get user's transaction history"""
    try:
        transactions = db.query(models.Transaction).filter(
            models.Transaction.user_id == user.get("id")
        ).order_by(models.Transaction.created_at.desc()).all()
        
        return {
            "transactions": [
                {
                    "id": t.id,
                    "order_id": t._pid,
                    "amount": float(t.transaction_amount),
                    "status": t._status.value,
                    "transaction_code": t.transaction_code,
                    "transaction_id": t.transaction_id,
                    "created_at": t.created_at,
                    "phone_number": t.party_a
                }
                for t in transactions
            ]
        }
        
    except Exception as e:
        logger.error(f"Error fetching transactions: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch transactions"
        )



@router.post("/transactions", status_code=status.HTTP_200_OK)
async def get_transaction_by_order_id(
    request: CheckTransactionStatus,
    db: db_dependency
):
    """Get a single transaction by order_id"""
    try:
        transaction = db.query(models.Transaction).filter(
            models.Transaction._pid == request.order_id
        ).order_by(models.Transaction.created_at.desc()).first()
        
        if not transaction:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Transaction not found for the provided order_id"
            )
        
        return {
            "transaction": {
                "id": transaction.id,
                "order_id": transaction._pid,
                "amount": float(transaction.transaction_amount),
                "status": transaction._status.value,  # Enum value for status
                "transaction_code": transaction.transaction_code,
                "transaction_id": transaction.transaction_id,
                "created_at": transaction.created_at,
                "phone_number": transaction.party_a,
                "party_b": transaction.party_b,
                "account_reference": transaction.account_reference,
                "transaction_category": transaction.transaction_category,
                "transaction_type": transaction.transaction_type,
                "transaction_channel": transaction.transaction_channel,
                "transaction_aggregator": transaction.transaction_aggregator,
                "transaction_timestamp": transaction.transaction_timestamp,
                "transaction_details": transaction.transaction_details,
                "feedback": transaction._feedback,
                "updated_at": transaction.updated_at
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching transaction: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch transaction"
        )